import os
import sys
import shutil
import psutil
import tempfile
import subprocess
import logging
import shlex
import signal
import asyncio
import inspect
import time
import math
import tenacity
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

from google import genai
from google.genai import types
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import ANSI, HTML, FormattedText
from prompt_toolkit.key_binding import KeyBindings


from rich.console import Console, Group
from rich.panel import Panel
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text
from rich import box
from rich.logging import RichHandler
from rich.spinner import Spinner
from rich.markup import escape

from arch_ai.config import Config, BORDERLESS_BOX
from arch_ai.mcp_manager import MCPManager
from arch_ai.analyzer import BehaviorAnalyzer
from arch_ai.troubleshooter import Troubleshooter
from arch_ai.gaming import GamingCompanion
from arch_ai.image_generator import ImageGenerator
from arch_ai.tools import TOOLS_SCHEMA, TOOL_MAP
from arch_ai.sandbox import sandbox_manager, SandboxManager
from arch_ai.memory_manager import MemoryManager
from arch_ai.response_utils import extract_full_model_response, extract_function_calls

from rich.markdown import CodeBlock
from rich.syntax import Syntax

class CustomCodeBlock(CodeBlock):
    def __rich_console__(self, console, options):
        code = str(self.text).rstrip()
        yield Syntax(
            code,
            self.lexer_name,
            theme=self.theme,
            word_wrap=True,
            background_color="default"
        )

Markdown.elements["fence"] = CustomCodeBlock
Markdown.elements["code_block"] = CustomCodeBlock



def rainbow_text(text: str, freq: float = 0.1) -> Text:
    rich_text = Text()
    for i, char in enumerate(text):
        r = int(math.sin(freq * i + 0) * 127 + 128)
        g = int(math.sin(freq * i + 2 * math.pi / 3) * 127 + 128)
        b = int(math.sin(freq * i + 4 * math.pi / 3) * 127 + 128)
        rich_text.append(char, style=f"rgb({r},{g},{b})")
    return rich_text

from arch_ai.logger_utils import StructuredLogger, setup_logging

log = setup_logging(Config.LOG_FILE)

_BASE_COMMANDS = ("/chat", "/cli", "/gaming", "/steam", "/image", "/sandbox", "/shell", "/stats", "/audit", "/clear", "/help", "/index", "exit")
_CLI_EXTRA_COMMANDS = ("pacman", "yay", "systemctl", "ls", "cd", "cat", "rm", "mv", "cp", "mkdir")
_SANDBOX_SUBS = ("shell", "status", "stop", "reset")
_STEAM_SUBS = ("status", "installed", "search", "launch", "news", "refresh")
_GAMING_SUBS = ("scan", "optimize", "status", "library", "refresh")

class TerminalCompleter(Completer):
    def __init__(self, terminal):
        self.terminal = terminal
        self._cache = {}

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/sandbox "):
            sub_text = text[len("/sandbox "):]
            for sub in _SANDBOX_SUBS:
                if sub.startswith(sub_text):
                    yield Completion(sub, start_position=-len(sub_text))
            return

        if text.startswith("/gaming "):
            sub_text = text[len("/gaming "):]
            for sub in _GAMING_SUBS:
                if sub.startswith(sub_text):
                    yield Completion(sub, start_position=-len(sub_text))
            return

        if text.startswith("/steam "):
            sub_text = text[len("/steam "):]
            for sub in _STEAM_SUBS:
                if sub.startswith(sub_text):
                    yield Completion(sub, start_position=-len(sub_text))
            return

        for cmd in _BASE_COMMANDS:
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text))

        if self.terminal.current_mode == "CLI":
            for cmd in _CLI_EXTRA_COMMANDS:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))

        try:
            parts = text.split()
            if not parts:
                return
            to_complete = parts[-1]
            dirname = os.path.dirname(to_complete) or "."
            basename = os.path.basename(to_complete)

            now = time.monotonic()
            cached = self._cache.get(dirname)
            if cached is None or (now - cached["time"] > 2.0):
                if os.path.isdir(dirname):
                    if len(self._cache) > 64:
                        self._cache.clear()
                    entries = []
                    try:
                        with os.scandir(dirname) as it:
                            for entry in it:
                                try:
                                    is_d = entry.is_dir()
                                except OSError:
                                    is_d = False
                                entries.append((entry.name, is_d))
                    except OSError:
                        entries = []
                    self._cache[dirname] = {"entries": entries, "time": now}
                else:
                    return

            cached_entries = self._cache.get(dirname, {}).get("entries", [])
            for name, is_d in cached_entries:
                if name.startswith(basename):
                    yield Completion(name, start_position=-len(basename), display=name + "/" if is_d else name)
        except Exception:
            pass

class AITerminal:
    def __init__(self):
        self.console = Console()
        self.analyzer = BehaviorAnalyzer()
        self.mcp = MCPManager()
        self.current_mode = "CLI"
        self.is_running = True
        self.prev_dir = os.getcwd()
        self.last_command: Optional[str] = None
        self.last_suggested_command: Optional[str] = None
        self.last_command_time: float = 0.0
        self.last_suggested_time: float = 0.0
        self.last_exec_time: Optional[float] = None
        self.last_exit_code: Optional[int] = None

        try:
            Config.validate()
        except ValueError as e:
            self.console.print(f"[bold red]Configuration Error:[/bold red] {escape(str(e))}")
            sys.exit(1)

        try:
            self.client = genai.Client(api_key=Config.GEMINI_API_KEY)
            self.grounding_tool = types.Tool(google_search=types.GoogleSearch())
            self.troubleshooter = Troubleshooter(self.console, model=self.client)
            self.gaming_companion = GamingCompanion(self.console, model=self.client)
            self.image_generator = ImageGenerator(self.console, model=self.client)
            self.analyzer.model = self.client
        except Exception as e:
            self.console.print(f"[bold red]Initialization Failed:[/bold red] {escape(str(e))}")
            sys.exit(1)

        signal.signal(signal.SIGINT, self.handle_interrupt)
        self._setup_prompt_toolkit()

    def _setup_prompt_toolkit(self):
        bindings = KeyBindings()

        @bindings.add('f1')
        def _(event):
            self.current_mode = "CLI"
            event.app.invalidate()

        @bindings.add('f2')
        def _(event):
            self.current_mode = "CHAT"
            event.app.invalidate()

        @bindings.add('f3')
        def _(event):
            self.current_mode = "GAMING"
            event.app.invalidate()

        @bindings.add('f4')
        def _(event):
            self.current_mode = "SANDBOX"
            event.app.invalidate()

        @bindings.add('f5')
        def _(event):
            self.current_mode = "IMAGE"
            event.app.invalidate()

        def get_bottom_toolbar():
            box_status = "🟢 Active" if sandbox_manager.is_active else "⚪ Inactive"
            timing_str = f"{self.last_exec_time:.2f}s (code {self.last_exit_code})" if self.last_exec_time is not None else "Ready"

            return FormattedText([
                ('bold', "Mode: "),
                ('', f"{self.current_mode} | "),
                ('bold', "Sandbox: "),
                ('', f"{box_status} | "),
                ('bold', "Last: "),
                ('', f"{timing_str} | "),
                ('italic', "[F1:CLI F2:CHAT F3:GAME F4:BOX F5:IMG]")
            ])

        history = FileHistory(os.path.expanduser("~/.agent_terminal_history"))
        style = Style.from_dict({
            'prompt': 'bold magenta',
            'prompt-meta': '#707070',
            'prompt-prefix': 'bold cyan',
            'auto-suggestion': '#666666 italic',
            'bottom-toolbar': '#ffffff bg:#1e1e1e',
        })
        self.session = PromptSession(
            history=history,
            completer=TerminalCompleter(self),
            auto_suggest=AutoSuggestFromHistory(),
            key_bindings=bindings,
            bottom_toolbar=get_bottom_toolbar,
            mouse_support=False,
            style=style
        )



    def display_header(self, clear=False):
        if clear: self.console.clear()
        theme = {"CLI": Config.COLOR_ARCH, "CHAT": Config.COLOR_GHOST, "SANDBOX": "green", "GAMING": Config.COLOR_GAMING, "IMAGE": Config.COLOR_IMAGE}.get(self.current_mode, "white")
        icon = {"SANDBOX": "📦", "GAMING": "🕹️", "IMAGE": "🎨"}.get(self.current_mode, "ᗧ")
        self.console.print(Panel(
            rainbow_text(f"{icon} ARCH AI TERMINAL"),
            subtitle=f" [bold yellow]Mode:[/bold yellow] [bold {theme}]{self.current_mode}[/bold {theme}]",
            border_style=theme, box=BORDERLESS_BOX, expand=True
        ))

    def display_help(self):
        help_text = """
        [bold cyan]# <query>[/bold cyan]    - Natural Language Shell Synthesis (e.g. # find large files)
        [bold cyan]/chat[/bold cyan]       - AI Chat Mode (with Autonomous System Tools)
        [bold cyan]/cli[/bold cyan]        - CLI Mode (Host Execution)
        [bold cyan]/gaming[/bold cyan]     - Gaming Companion & Optimization (/gaming scan, optimize, status)
        [bold cyan]/audit[/bold cyan]      - Arch System Health & Maintenance Audit
        [bold cyan]/sandbox[/bold cyan]    - Fused Stateful Sandbox (/sandbox <cmd>, shell, status, stop, reset)
        [bold cyan]!<cmd>[/bold cyan]      - Host Escape: execute <cmd> on host while inside Sandbox
        [bold cyan]/image[/bold cyan]      - AI Image Generator (/image <prompt>)
        [bold cyan]/steam[/bold cyan]      - Steam Gaming Hub (/steam <game>, installed, status, search, launch, news)
        [bold cyan]/stats[/bold cyan]      - Behavioral Stats & Telemetry Info
        [bold cyan]/index[/bold cyan]      - Index current directory for AI knowledge
        [bold cyan]/clear[/bold cyan]      - Clear Screen
        [bold cyan]/help[/bold cyan]       - Show this message
        [bold cyan]exit[/bold cyan]        - Quit

        """
        self.console.print(Panel(help_text, title="[HELP]", border_style="cyan", box=BORDERLESS_BOX, expand=True))

    def display_sandbox_status(self):
        """Displays diagnostic and configuration status for the sandbox."""
        st = sandbox_manager.get_status()
        status_color = "green" if st["is_active"] else "red"
        status_text = "Active 🟢" if st["is_active"] else "Inactive ⚪"
        lines = [
            f"[bold]Status:[/bold] [{status_color}]{status_text}[/{status_color}]",
            f"[bold]Engine:[/bold] [cyan]{st['engine']}[/cyan]",
            f"[bold]Image:[/bold] [yellow]{st['image']}[/yellow]",
            f"[bold]Container ID:[/bold] {st['container_name'] if st['is_active'] else '[dim]N/A[/dim]'}",
            f"[bold]Working Dir:[/bold] [magenta]{st['cwd']}[/magenta]",
            f"[bold]Network Mode:[/bold] {st['network_mode']}",
            f"[bold]Memory Limit:[/bold] {st['mem_limit']}",
            f"[bold]CPU Quota:[/bold] {st['cpu_quota']}",
            f"[bold]PIDs Limit:[/bold] {st['pids_limit']}",
            f"[bold]Read-Only Mount:[/bold] {st['read_only']}",
            f"[bold]Active Mounts:[/bold] {st['mount_map'] if st['mount_map'] else '[dim]None[/dim]'}",
            f"[bold]Tracked Env Vars:[/bold] {st['env_vars_count']}"
        ]
        self.console.print(Panel(
            "\n".join(lines),
            title="[SANDBOX STATUS]",
            border_style="green",
            box=BORDERLESS_BOX,
            expand=True
        ))



    def handle_interrupt(self, signum, frame):
        self.console.print("\n[yellow]Interrupted. Type 'exit' to quit.[/yellow]")

    def handle_cd(self, command: str) -> bool:
        parts = shlex.split(command)
        if not parts or parts[0] != "cd": return False
        try:
            current = os.getcwd()
            path = self.prev_dir if (len(parts) > 1 and parts[1] == "-") else os.path.expanduser(parts[1] if len(parts) > 1 else "~")
            os.chdir(path)
            self.prev_dir = current
        except Exception as e:
            self.console.print(f"[bold red]cd error:[/bold red] {escape(str(e))}")
        return True

    def _get_prompt(self) -> FormattedText:
        now = datetime.now().strftime("%H:%M:%S")
        if self.current_mode == "SANDBOX":
            cwd = sandbox_manager.cwd
        else:
            cwd = os.getcwd().replace(os.path.expanduser("~"), "~")
        prefix = {"CHAT": "ᗧ [CHAT]", "SANDBOX": "📦 [SANDBOX]", "GAMING": "🕹️ [GAMING]", "IMAGE": "🎨 [IMAGE]"}.get(self.current_mode, "ᗧ")
        return FormattedText([
            ('class:prompt-meta', f"{now} {cwd} "),
            ('class:prompt-prefix', f"{prefix} ❯ "),
        ])


    async def summarize_history(self, history: List[Dict[str, str]]) -> str:
        """Uses the AI to summarize old history into a single cohesive state."""
        if not history: return ""
        
        summary_prompt = (
            "Summarize the following technical conversation history into a concise 'Current System State & Goal' summary. "
            "Focus on the technical decisions, current directory, and the user's ultimate objective. "
            "Keep it under 200 words.\n\n"
        )
        for h in history:
            summary_prompt += f"USER: {h['user']}\nAGENT: {h['agent']}\n"
            
        try:
            response = await self.client.aio.models.generate_content(
                model=Config.MODEL_NAME,
                contents=summary_prompt
            )
            summary_text = extract_full_model_response(response)
            return f"SUMMARY OF PREVIOUS CONVERSATION:\n{summary_text}\n" if summary_text else ""
        except Exception as e:
            log.error(f"Summarization failed: {e}")
            return ""

    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
        stop=tenacity.stop_after_attempt(3),
        retry=tenacity.retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: log.warning(f"Retrying AI call (attempt {retry_state.attempt_number})..."),
        reraise=True
    )
    async def _execute_gemini_turn(self, contents: List[types.Content], system_instruction: str, tools_list: list):
        return await self.client.aio.models.generate_content(
            model=Config.MODEL_NAME,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=tools_list,
                tool_config=types.ToolConfig(
                    include_server_side_tool_invocations=True
                ),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                )
            )
        )

    async def _execute_gemini_turn_stream(self, contents: List[types.Content], system_instruction: str, tools_list: list):
        return await self.client.aio.models.generate_content_stream(
            model=Config.MODEL_NAME,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=tools_list,
                tool_config=types.ToolConfig(
                    include_server_side_tool_invocations=True
                ),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                )
            )
        )


    async def query_gemini(self, prompt_text: str):
        if not prompt_text.strip(): return
        
        start_time = asyncio.get_event_loop().time()
        # 1. Local Knowledge Retrieval
        local_results = await self.analyzer.knowledge.search(prompt_text)
        local_context = ""
        if local_results:
            local_context = "\nLOCAL PROJECT CONTEXT (RETRIEVED FILES):\n" + "\n".join([f"FILE: {r['path']}\nCONTENT: {r['content']}" for r in local_results]) + "\n\n"

        # 2. History Management (Summarization if too long)
        raw_history = await self.analyzer.get_chat_history(limit=20)
        history_summary = ""
        if len(raw_history) > 10:
            to_summarize = raw_history[:-5]
            recent_history = raw_history[-5:]
            history_summary = await self.summarize_history(to_summarize)
        else:
            recent_history = raw_history

        contents: List[types.Content] = []
        for h in recent_history:
            u = h.get("user", "").strip()
            a = h.get("agent", "").strip()
            if u and a:
                contents.append(types.Content(role="user", parts=[types.Part(text=u)]))
                contents.append(types.Content(role="model", parts=[types.Part(text=a)]))
        
        # Add summary and current context to the latest message
        final_prompt = f"{history_summary}{local_context}{prompt_text}"
        contents.append(types.Content(role="user", parts=[types.Part(text=final_prompt)]))
        
        memories = MemoryManager.get_instance().get_all_memories()
        memory_str = "\n".join([f"- {m}" for m in memories]) if memories else "None"
        
        system_instruction = (
            "ROLE: You are the 'Arch AI System Architect', an expert Linux and development assistant.\n"
            "KNOWLEDGE: You operate using 'Google Search Grounding' and local 'System Tools' for verified real-world accuracy.\n"
            f"LONG TERM MEMORY (User Preferences & Facts):\n{memory_str}\n\n"
            "TOOLS: You have access to system inspection and execution tools: analyze_gaming_session, scan_gaming_system, optimize_gaming_system, "
            "execute_host_command, list_files, read_file, write_file, patch_file, get_system_info, check_process, "
            "execute_code_in_sandbox, execute_sandbox_command, write_to_sandbox_file, read_sandbox_file, get_steam_game_compatibility, get_steam_library.\n"
            "Call these tools autonomously whenever you need to execute commands, check system status, view files, or test solutions.\n"
            "CORE PROTOCOLS:\n"
            "1. Autonomously execute required commands and tools to resolve queries before providing the final answer.\n"
            "2. When asked to analyze a gaming session, find micro-stuttering, or check game performance, ALWAYS use analyze_gaming_session and scan_gaming_system.\n"
            "3. For host system diagnostics, hardware, GPU, kernel, and gaming sessions, ALWAYS use execute_host_command or analyze_gaming_session on the host.\n"
            "4. Do NOT use sandbox tools (execute_code_in_sandbox) to inspect the host system or games; the sandbox is an isolated container with no access to host processes or GPU.\n"
            "5. Ground technical recommendations in verified documentation retrieved via search or tool data.\n"
            "6. Maintain a professional, architect-level tone; omit conversational filler.\n"
            "7. Format all responses using valid Markdown with syntax highlighting.\n"
            "8. Prioritize security, official Arch Linux guidelines, and upstream documentation."
        )

        tools_list = [self.grounding_tool] + TOOLS_SCHEMA + self.mcp.sessions
        full_reply = ""
        grounding_detected = False
        max_tool_iterations = 10
        intermediate_texts: List[str] = []
        executed_tool_results: List[Dict[str, str]] = []

        for iteration in range(max_tool_iterations):
            turn_text = ""
            function_calls = []
            candidate_parts = []
            
            self.console.print(rainbow_text("\nᗧ ARCH AI ASSISTANT"))
            with Live(console=self.console, refresh_per_second=15, transient=False) as live:
                try:
                    response_stream = await self._execute_gemini_turn_stream(contents, system_instruction, tools_list)
                    async for chunk in response_stream:
                        if getattr(chunk, "candidates", None) and getattr(chunk.candidates[0], "grounding_metadata", None):
                            grounding_detected = True
                            
                        chunk_text = extract_full_model_response(chunk, include_function_calls=False)
                        if chunk_text:
                            turn_text += chunk_text
                            live.update(Markdown(turn_text))
                            
                        from arch_ai.response_utils import extract_parts_from_response
                        for p in extract_parts_from_response(chunk):
                            if getattr(p, "function_call", None):
                                function_calls.append(p.function_call)
                                candidate_parts.append(p)
                            
                except Exception as e:
                    log.error(f"AI Generation Error: {e}")
                    full_reply = f"Error generating response: {e}"
                    break

            if not turn_text and not function_calls:
                full_reply = "AI returned an empty response."
                break

            if turn_text:
                intermediate_texts.append(turn_text)
                # Ensure the text is at the beginning of the candidate_parts
                candidate_parts.insert(0, types.Part(text=turn_text))

            if function_calls:
                # Add model's tool request to conversation turn
                contents.append(types.Content(role="model", parts=candidate_parts))
                tool_response_parts = []

                for fc in function_calls:
                    tool_name = fc.name
                    tool_args = dict(fc.args) if getattr(fc, "args", None) else {}
                    self.console.print(f"[bold cyan]🔧 Executing Tool:[/bold cyan] [green]{escape(tool_name)}[/green]({escape(str(tool_args))})")
                    
                    if tool_name in TOOL_MAP:
                        try:
                            tool_fn = TOOL_MAP[tool_name]
                            if inspect.iscoroutinefunction(tool_fn):
                                result = await asyncio.wait_for(tool_fn(**tool_args), timeout=30)
                            else:
                                result = await asyncio.wait_for(asyncio.to_thread(tool_fn, **tool_args), timeout=30)
                        except asyncio.TimeoutError:
                            result = f"Error: Tool '{tool_name}' execution timed out after 30 seconds."
                        except Exception as e:
                            result = f"Tool Error: {e}"
                    elif hasattr(self, 'mcp') and tool_name in self.mcp.tool_map:
                        try:
                            mcp_result = await self.mcp.call_tool(tool_name, tool_args)
                            if getattr(mcp_result, "content", None):
                                result = "\n".join([c.text for c in mcp_result.content if getattr(c, "type", "") == "text"])
                            else:
                                result = str(mcp_result)
                        except Exception as e:
                            result = f"MCP Tool Error: {e}"
                    else:
                        result = f"Error: Tool '{tool_name}' not recognized."
                    
                    executed_tool_results.append({
                        "tool": tool_name,
                        "args": str(tool_args),
                        "result": str(result)
                    })
                    tool_response_parts.append(
                        types.Part.from_function_response(
                            name=tool_name,
                            response={"result": str(result)}
                        )
                    )

                contents.append(types.Content(role="user", parts=tool_response_parts))
                continue
            else:
                # Final text answer reached (no more tool calls)
                full_reply = "\n\n".join(intermediate_texts) if intermediate_texts else turn_text
                break

        # If the model exhausted tool iterations without generating a final text response, force a synthesis turn
        if not full_reply:
            synth_text = ""
            self.console.print(rainbow_text("\nᗧ ARCH AI ASSISTANT"))
            with Live(console=self.console, refresh_per_second=15, transient=False) as live:
                try:
                    synthesis_user_msg = (
                        "All data gathering and diagnostic tools have completed. "
                        f"Synthesize all the system information, diagnostic outputs, and logs retrieved above into a comprehensive, "
                        f"clear, detailed, and actionable final report answering my request: '{prompt_text}'. "
                        "Do not invoke any tools. Provide the complete analysis in valid Markdown now."
                    )
                    synth_contents = list(contents) + [
                        types.Content(role="user", parts=[types.Part(text=synthesis_user_msg)])
                    ]
                    
                    response_stream = await self.client.aio.models.generate_content_stream(
                        model=Config.MODEL_NAME,
                        contents=synth_contents,
                        config=types.GenerateContentConfig(
                            system_instruction=(
                                "You are the Arch AI System Architect. All tool gathering is complete. "
                                "Synthesize all data, logs, and system metrics provided in the conversation into a comprehensive, "
                                "professional, actionable Markdown report with headings, bullet points, and code blocks. "
                                "Do NOT request any more tool calls."
                            ),
                            tool_config=types.ToolConfig(
                                function_calling_config=types.FunctionCallingConfig(
                                    mode=types.FunctionCallingConfigMode.NONE
                                )
                            ),
                            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                                disable=True
                            )
                        )
                    )
                    
                    async for chunk in response_stream:
                        if getattr(chunk, "candidates", None) and getattr(chunk.candidates[0], "grounding_metadata", None):
                            grounding_detected = True
                        chunk_text = extract_full_model_response(chunk, include_function_calls=False)
                        if chunk_text:
                            synth_text += chunk_text
                            live.update(Markdown(synth_text))

                    if intermediate_texts:
                        full_reply = "\n\n".join(intermediate_texts + ([synth_text] if synth_text else []))
                    else:
                        full_reply = synth_text
                except Exception as e:
                    log.error(f"Final Synthesis Error: {e}")
                    full_reply = f"Diagnostic completed, but encountered an error synthesizing response: {e}"

        if not full_reply and executed_tool_results:
            with self.console.status(f"[bold {Config.COLOR_GHOST}]Formatting Analysis from Tool Findings...", spinner="dots"):
                try:
                    summary_prompt = (
                        "The following diagnostic and inspection tools were executed on the system:\n\n"
                    )
                    for item in executed_tool_results:
                        res_preview = item['result'][:1500]
                        summary_prompt += f"### Tool: `{item['tool']}`\nArguments: `{item['args']}`\nOutput:\n```\n{res_preview}\n```\n\n"
                    summary_prompt += (
                        f"Original User Request: {prompt_text}\n\n"
                        "Provide a comprehensive, professional, and actionable final report and recommendations based on all tool results above."
                    )
                    retry_resp = await self.client.aio.models.generate_content(
                        model=Config.MODEL_NAME,
                        contents=summary_prompt
                    )
                    full_reply = extract_full_model_response(retry_resp, include_function_calls=False)
                except Exception as e:
                    log.error(f"Fallback synthesis retry failed: {e}")

            if not full_reply:
                # Format structured diagnostic report directly from executed tools
                report_lines = [
                    "### 🛠️ Diagnostic & Execution Summary\n",
                    f"Completed **{len(executed_tool_results)}** autonomous actions for query: *{prompt_text}*\n"
                ]
                for item in executed_tool_results:
                    report_lines.append(f"#### 🔧 `{item['tool']}`")
                    if item['args'] and item['args'] != "{}":
                        report_lines.append(f"**Arguments:** `{item['args']}`")
                    res = item['result'].strip()
                    report_lines.append(f"```\n{res}\n```\n")
                full_reply = "\n".join(report_lines)

        if not full_reply:
            if intermediate_texts:
                full_reply = "\n\n".join(intermediate_texts)
            else:
                full_reply = "Diagnostic completed, but no written analysis was generated by the model. Please check the logs or retry the query."

        # Display final response (only if not already streamed)
        sub = "Completed" + (" | [cyan]ᗧ Grounded[/cyan]" if grounding_detected else "")
        if local_results: sub += " | [green]📁 Local Knowledge[/green]"
        
        # We only print the fallback or un-streamed full reply if the main streams didn't render it
        # Actually, since we stream each turn, `full_reply` contains the combined text.
        # But we don't want to duplicate what was already streamed.
        # If we reached the fallback structured diagnostic (report_lines), it wasn't streamed.
        if "### 🛠️ Diagnostic & Execution Summary" in full_reply:
            self.console.print(Panel(
                Markdown(full_reply),
                title=rainbow_text("ᗧ ARCH AI ASSISTANT"),
                border_style=Config.COLOR_GHOST,
                subtitle=sub,
                box=BORDERLESS_BOX,
                padding=(1, 2),
                expand=True
            ))


        await self.analyzer.log_chat(prompt_text, full_reply)
        
        # Log telemetry
        duration = asyncio.get_event_loop().time() - start_time
        await self.analyzer.telemetry.log_metric("ai_latency", duration, {"grounding": str(grounding_detected)})

    async def _check_command(self, cmd: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec("which", cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
            return proc.returncode == 0
        except:
            return False

    async def _execute_command_with_capture(self, command: str) -> Tuple[int, str]:
        if not await self._check_command("script"):
            process = await asyncio.create_subprocess_shell(command, stdout=None, stderr=None, stdin=None)
            await process.wait()
            return process.returncode, ""

        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as tmp:
            tmp_path = tmp.name
        
        try:
            # Use script to capture both stdout and stderr while maintaining TTY interactivity
            wrapped_command = f"script -q -e -c {shlex.quote(command)} {tmp_path}"
            process = await asyncio.create_subprocess_shell(wrapped_command, stdout=None, stderr=None, stdin=None)
            await process.wait()
            
            output = ""
            if os.path.exists(tmp_path):
                with open(tmp_path, 'r', encoding='utf-8', errors='ignore') as f:
                    output = f.read()
            
            # Basic cleanup of script output
            lines = output.splitlines()
            cleaned_lines = []
            for line in lines:
                if "Script started on" in line or "Script done on" in line:
                    continue
                cleaned_lines.append(line)
            output = "\n".join(cleaned_lines).strip()
            
            return process.returncode, output
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    async def run_cli_command(self, command: str):
        self.last_command = command
        self.last_command_time = time.time()

        risk_level, reason = await self.analyzer.async_classify_risk(command)
        if risk_level in ["high", "critical", "medium"]:
            color = {"critical": "red", "high": "red", "medium": "yellow"}.get(risk_level, "white")
            self.console.print(Panel(f"[bold {color}]RISK:[/bold {color}] {escape(reason)}", border_style=color, box=BORDERLESS_BOX, expand=True))
            # Use prompt_async to get user confirmation
            confirm = await self.session.prompt_async(ANSI(f"\x1b[1;33mConfirm execution? [y/N]: \x1b[0m"))
            if confirm.lower() != "y":
                return

        if self.handle_cd(command): return
        
        t0 = time.perf_counter()
        return_code, output = await self._execute_command_with_capture(command)
        self.last_exec_time = time.perf_counter() - t0
        self.last_exit_code = return_code

        status = f"failed({return_code})" if return_code != 0 else "success"
        await self.analyzer.log_command(command, status)
        
        if return_code != 0:
            self.console.print(f"\n[bold red]Command failed with exit code {return_code}.[/bold red]")
            if (await self.session.prompt_async(ANSI("\x1b[1;36mRun AI troubleshooter? [y/N]: \x1b[0m"))).lower() == "y":
                error_msg = output if output else f"Exit Code: {return_code}"
                if len(error_msg) > 5000:
                    error_msg = error_msg[:2500] + "\n... [TRUNCATED] ...\n" + error_msg[-2500:]
                
                fix_cmd = await self.troubleshooter.troubleshoot(command, error_msg)
                if fix_cmd:
                    self.last_suggested_command = fix_cmd
                    self.last_suggested_time = time.time()
                    choice = (await self.session.prompt_async(ANSI(f"\x1b[1;32mExecute suggested fix ({escape(fix_cmd)})? [y/N]: \x1b[0m"))).strip().lower()
                    if choice == "y":
                        await self.run_cli_command(fix_cmd)

    async def synthesize_nl_command(self, query: str):
        """Converts natural language request (prefixed by #) into an executable shell command."""
        if not query.strip(): return
        with self.console.status("[bold cyan]Synthesizing Arch Linux command...", spinner="dots"):
            prompt = (
                "You are an expert Arch Linux terminal assistant. Convert this user request into a single, safe, "
                "correct, and optimized bash command. Return ONLY the raw shell command on a single line, with NO markdown formatting, NO backticks, and NO explanations.\n\n"
                f"Request: {query}"
            )
            try:
                resp = await self.client.aio.models.generate_content(
                    model=Config.MODEL_NAME,
                    contents=prompt
                )
                raw_text = extract_full_model_response(resp).strip()
                cmd = raw_text.replace("```bash", "").replace("```sh", "").replace("```", "").strip()
            except Exception as e:
                self.console.print(f"[bold red]Command Synthesis Failed:[/bold red] {escape(str(e))}")
                return

        if not cmd:
            self.console.print("[yellow]Could not synthesize a command.[/yellow]")
            return

        self.console.print(Panel(
            f"[bold cyan]{escape(cmd)}[/bold cyan]",
            title="🤖 [bold]SYNTHESIZED COMMAND[/bold]",
            subtitle="[dim]Generated from natural language request[/dim]",
            border_style="cyan",
            box=BORDERLESS_BOX,
            padding=(0, 2),
            expand=True
        ))

        choice = (await self.session.prompt_async(ANSI("\x1b[1;32mExecute this command? [y/N]: \x1b[0m"))).strip().lower()
        if choice == "y":
            await self.run_cli_command(cmd)

    async def audit_system_health(self):
        """Runs a comprehensive Arch Linux system health, pacman, and service audit."""
        with self.console.status("[bold green]Running Arch Linux System Audit...", spinner="aesthetic"):
            # 1. Failed systemd units
            failed_units = []
            try:
                proc = await asyncio.create_subprocess_shell("systemctl --failed --no-legend --plain", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                out, _ = await proc.communicate()
                failed_units = [line.split()[0] for line in out.decode().splitlines() if line.strip()]
            except Exception:
                pass

            # 2. Pacman orphan packages
            orphans = []
            try:
                proc = await asyncio.create_subprocess_shell("pacman -Qtdq", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                out, _ = await proc.communicate()
                orphans = [line.strip() for line in out.decode().splitlines() if line.strip()]
            except Exception:
                pass

            # 3. Pacman cache size
            cache_size = "Unknown"
            if os.path.exists("/var/cache/pacman/pkg"):
                try:
                    proc = await asyncio.create_subprocess_shell("du -sh /var/cache/pacman/pkg", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                    out, _ = await proc.communicate()
                    if out: cache_size = out.decode().split()[0]
                except Exception:
                    pass

            # 4. Disk and memory stats
            disk = shutil.disk_usage("/")
            disk_free_gb = round(disk.free / (1024**3), 2)
            disk_total_gb = round(disk.total / (1024**3), 2)
            disk_pct = round((disk.used / disk.total) * 100, 1)

            mem = psutil.virtual_memory()
            ram_used_pct = mem.percent

            # 5. Upgradable packages count (via checkupdates if available)
            updates_count = "N/A"
            if shutil.which("checkupdates"):
                try:
                    proc = await asyncio.create_subprocess_shell("checkupdates | wc -l", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                    out, _ = await proc.communicate()
                    if out: updates_count = out.decode().strip()
                except Exception:
                    pass

        lines = [
            "### 🛡️ Arch Linux System Health & Maintenance Audit\n",
            f"| Metric | Value | Status |",
            f"| :--- | :--- | :--- |",
            f"| **Failed Services** | `{len(failed_units)} failed units` | {'✅ All Services Clean' if not failed_units else f'❌ {len(failed_units)} Failed'} |",
            f"| **Orphan Packages** | `{len(orphans)} unneeded` | {'✅ No Orphans' if not orphans else f'⚠️ {len(orphans)} to clean (`pacman -Rns $(pacman -Qtdq)`)'} |",
            f"| **Pacman Cache** | `{cache_size}` | {'✅ Clean' if 'G' not in cache_size or float(cache_size.replace('G','')) < 5 else '⚠️ Large Cache (`paccache -r`)'} |",
            f"| **Root Disk Free** | `{disk_free_gb} GB / {disk_total_gb} GB ({disk_pct}% used)` | {'✅ Healthy' if disk_pct < 85 else '⚠️ Disk Space Low'} |",
            f"| **Memory Usage** | `{ram_used_pct}% in use` | {'✅ Healthy' if ram_used_pct < 85 else '⚠️ High Memory Load'} |",
            f"| **Pending Updates** | `{updates_count} packages` | {'✅ Up to date' if updates_count in ('0', 'N/A') else f'ℹ️ {updates_count} updates available'} |",
            ""
        ]

        if failed_units:
            lines.append("#### ❌ Failed Systemd Units:")
            for u in failed_units[:5]:
                lines.append(f"- `{u}` (`systemctl status {u}`)")
            lines.append("")

        if orphans:
            lines.append(f"#### 🧹 Clean Orphaned Packages ({len(orphans)}):")
            lines.append("```bash\nsudo pacman -Rns $(pacman -Qtdq)\n```\n")

        self.console.print(Panel(
            Markdown("\n".join(lines)),
            title="[bold cyan]🛡️ ARCH SYSTEM AUDIT[/bold cyan]",
            border_style="cyan",
            box=BORDERLESS_BOX,
            padding=(1, 2),
            expand=True
        ))

    async def start(self):
        SandboxManager.cleanup_orphaned_containers()
        self.display_header(clear=True)
        await self.analyzer.initialize()
        
        # Load any MCP servers from environment or config
        import os
        mcp_servers = os.getenv("MCP_SERVERS", "")
        if mcp_servers:
            for srv in mcp_servers.split(","):
                parts = srv.split()
                if parts:
                    self.mcp.add_server(parts[0], parts[1:])
        await self.mcp.initialize()
        try:
            while self.is_running:
                try:
                    user_input = (await self.session.prompt_async(self._get_prompt)).strip()
                    if not user_input: continue
                    if user_input.lower() in ['exit', 'quit']: break
                    if user_input.startswith("#"):
                        await self.synthesize_nl_command(user_input[1:].strip())
                        continue
                    if user_input.startswith("/"):
                        cmd = user_input.lower().split()[0]
                        if cmd == "/chat":
                            self.current_mode = "CHAT"
                        elif cmd == "/cli":
                            self.current_mode = "CLI"
                        elif cmd == "/gaming":
                            parts = user_input.split(maxsplit=1)
                            if len(parts) > 1 and parts[1].strip():
                                subcmd = parts[1].strip().lower()
                                if subcmd == "scan":
                                    await self.gaming_companion.scan_system()
                                elif subcmd == "optimize":
                                    await self.gaming_companion.optimize_system(apply=True)
                                elif subcmd in ["status", "installed", "library"]:
                                    await self.gaming_companion.show_steam_status()
                                else:
                                    await self.gaming_companion.query(parts[1].strip(), await self.analyzer.get_chat_history(limit=5), analyzer=self.analyzer)
                            else:
                                self.current_mode = "GAMING"
                        elif cmd == "/audit":
                            await self.audit_system_health()
                        elif cmd == "/sandbox":
                            parts = user_input.split(maxsplit=1)
                            if len(parts) > 1 and parts[1].strip():
                                subcmd = parts[1].strip()
                                if subcmd in ["shell", "bash", "sh"]:
                                    await self._enter_sandbox_shell()
                                elif subcmd == "status":
                                    self.display_sandbox_status()
                                elif subcmd in ["stop", "down"]:
                                    if sandbox_manager.is_active:
                                        sandbox_manager.stop()
                                        self.console.print("[bold yellow]Sandbox container stopped cleanly.[/bold yellow]")
                                    else:
                                        self.console.print("[dim]Sandbox is not active.[/dim]")
                                elif subcmd in ["reset", "restart"]:
                                    if sandbox_manager.is_active:
                                        sandbox_manager.stop()
                                    self._ensure_sandbox_active()
                                else:
                                    await self.run_sandbox_command(subcmd)
                            else:
                                self.current_mode = "SANDBOX"
                                if not self._ensure_sandbox_active():
                                    self.current_mode = "CLI"
                        elif cmd == "/image":
                            parts = user_input.split(maxsplit=1)
                            if len(parts) > 1 and parts[1].strip():
                                await self.image_generator.generate_image(parts[1].strip())
                            else:
                                self.current_mode = "IMAGE"
                        elif cmd in ["/steam", "/games"]:
                            parts = user_input.split(maxsplit=2)
                            subcmd = parts[1].strip().lower() if len(parts) > 1 else ""

                            if not subcmd:
                                await self.gaming_companion.show_steam_library()
                            elif subcmd == "status":
                                await self.gaming_companion.show_steam_status()
                            elif subcmd == "installed":
                                await self.gaming_companion.show_installed_games()
                            elif subcmd == "search":
                                search_term = user_input.split(maxsplit=1)[1].replace("search", "", 1).strip()
                                await self.gaming_companion.search_store_games(search_term)
                            elif subcmd == "launch":
                                launch_target = user_input.split(maxsplit=1)[1].replace("launch", "", 1).strip()
                                self.gaming_companion.launch_game(launch_target)
                            elif subcmd == "news":
                                news_target = user_input.split(maxsplit=1)[1].replace("news", "", 1).strip()
                                await self.gaming_companion.show_game_news(news_target)
                            elif subcmd == "refresh":
                                await self.gaming_companion.refresh_steam_cache()
                            else:
                                # Handle direct game query by AppID or Title (e.g. /steam 1086940 or /steam baldur)
                                query = user_input.split(maxsplit=1)[1].strip()
                                await self.gaming_companion.show_game_compatibility(query)
                        elif cmd == "/shell":
                            await self._enter_sandbox_shell()
                        elif cmd == "/clear": self.display_header(clear=True)
                        elif cmd == "/help": self.display_help()
                        elif cmd == "/stats": self.console.print(Panel(await self.analyzer.get_behavioral_stats(), title="[STATS]", border_style="yellow", box=BORDERLESS_BOX, expand=True))
                        elif cmd == "/index":
                            with self.console.status("[bold green]Indexing local files...", spinner="earth"):
                                count = await self.analyzer.knowledge.index_directory(".")
                                self.console.print(f"[bold green]Success:[/bold green] Indexed {count} new/modified files.")
                        else:
                            self.console.print(f"[yellow]Unknown command '{escape(cmd)}'. Type [bold cyan]/help[/bold cyan] for available commands.[/yellow]")
                        continue
                    if self.current_mode == "CLI": await self.run_cli_command(user_input)
                    elif self.current_mode == "SANDBOX":
                        if user_input.startswith("!"):
                            host_cmd = user_input[1:].strip()
                            if host_cmd:
                                self.console.print(f"[dim]Host execution:[/dim] [cyan]{escape(host_cmd)}[/cyan]")
                                await self.run_cli_command(host_cmd)
                        else:
                            await self.run_sandbox_command(user_input)
                    elif self.current_mode == "CHAT": await self.query_gemini(user_input)
                    elif self.current_mode == "GAMING": await self.gaming_companion.query(user_input, await self.analyzer.get_chat_history(limit=5), analyzer=self.analyzer)
                    elif self.current_mode == "IMAGE": await self.image_generator.generate_image(user_input)

                except (EOFError, KeyboardInterrupt): break
                except Exception as e: 
                    log.exception(f"Loop Error: {e}")
                    self.console.print(f"[red]Critical Error: {escape(str(e))}[/red]")
        finally:
            if hasattr(self, "gaming_companion") and self.gaming_companion:
                await self.gaming_companion.aclose()
            if hasattr(self, "mcp") and self.mcp:
                await self.mcp.cleanup()

    def _ensure_sandbox_active(self) -> bool:
        """Ensures container sandbox is running and mounted; returns True if active."""
        if sandbox_manager.is_active:
            return True
        with self.console.status("[bold green]Initializing Container Sandbox (Syncing Project)...", spinner="dots"):
            if sandbox_manager.start(mount_map={"./": "/workspace"}):
                self.console.print(f"[bold green]Sandbox active ({sandbox_manager.engine}). Project synced to /workspace.[/bold green]")
                return True
            else:
                self.console.print("[bold red]Failed to start sandbox. Is Docker or Podman running?[/bold red]")
                return False

    async def _enter_sandbox_shell(self):
        """Drops into an interactive persistent container shell session."""
        if self._ensure_sandbox_active():
            self.console.print("[bold green]Entering Sandbox Shell... (type 'exit' to return)[/bold green]")
            sandbox_manager.execute_interactive("bash || sh")

    async def run_sandbox_command(self, command: str):
        """Executes a command within the active sandbox with full interactivity."""
        self.last_command = command
        self.last_command_time = time.time()

        if not self._ensure_sandbox_active():
            return

        # We don't use a spinner here because an interactive command needs the TTY immediately
        return_code = sandbox_manager.execute_interactive(command)
        
        status = f"sandbox_failed({return_code})" if return_code != 0 else "sandbox_success"
        await self.analyzer.log_command(f"[SANDBOX] {command}", status)
        
        if return_code != 0:
            self.console.print(f"[bold red]Sandbox Error (Exit Code: {return_code})[/bold red]")


def main():
    try:
        asyncio.run(AITerminal().start())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        if sandbox_manager.is_active:
            print("\nShutting down sandbox...")
            sandbox_manager.stop()

if __name__ == "__main__":
    main()

