import os
import shutil
import asyncio
import platform
import psutil
import logging
from typing import List, Dict, Optional

from arch_ai.sandbox import sandbox_manager

logger = logging.getLogger("agent_terminal.tools")

class SystemTools:
    """A collection of safe system tools for the AI agent."""
    @staticmethod
    def spawn_background_researcher(task_description: str) -> str:
        """Spawns an asynchronous background sub-agent to perform deep research or long-running tasks without blocking the main terminal."""
        from arch_ai.subagents import SubAgentManager
        return SubAgentManager.get_instance().spawn_researcher(task_description)

    @staticmethod
    def save_memory(fact: str) -> str:
        """Saves a long-term fact or preference about the user or project into persistent memory."""
        from arch_ai.memory_manager import MemoryManager
        return MemoryManager.get_instance().save_memory(fact)

    @staticmethod
    def clear_memory(fact: str) -> str:
        """Removes a fact from the persistent memory store."""
        from arch_ai.memory_manager import MemoryManager
        return MemoryManager.get_instance().clear_memory(fact)

    @staticmethod
    def semantic_search(query: str, n_results: int = 5) -> str:
        """Searches the indexed codebase semantically for the given query."""
        from arch_ai.rag_searcher import RAGManager
        return RAGManager.get_instance().semantic_search(query, n_results)

    @staticmethod
    def index_directory(path: str = ".") -> str:
        """Indexes all python files in the directory for semantic search."""
        import os
        from arch_ai.rag_searcher import RAGManager
        rag = RAGManager.get_instance()
        
        resolved_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(resolved_path):
            return f"Error: Directory not found: {path}"
            
        count = 0
        for root, _, files in os.walk(resolved_path):
            if ".venv" in root or "__pycache__" in root or ".git" in root:
                continue
            for file in files:
                if file.endswith((".py", ".md", ".txt", ".json", ".toml", ".sh")):
                    filepath = os.path.join(root, file)
                    rag.index_file(filepath)
                    count += 1
        return f"Successfully indexed {count} files in {path}."


    @staticmethod
    def list_files(path: str = ".") -> str:
        """Lists files in the specified directory."""
        try:
            resolved_path = os.path.abspath(os.path.expanduser(path))
            if not os.path.exists(resolved_path):
                return f"Error: Directory not found: {path}"
            if not os.path.isdir(resolved_path):
                return f"Error: Path is not a directory: {path}"
            items = sorted(os.listdir(resolved_path))
            return "\n".join(items) if items else "Directory is empty."
        except Exception as e:
            return f"Error listing files: {e}"

    @staticmethod
    def read_file(path: str, lines: int = 50) -> str:
        """Reads the first N lines of a file safely."""
        try:
            resolved_path = os.path.abspath(os.path.expanduser(path))
            if not os.path.exists(resolved_path):
                return f"Error: File not found: {path}"
            if not os.path.isfile(resolved_path):
                return f"Error: Path is not a file: {path}"
            
            read_lines = []
            with open(resolved_path, 'r', encoding='utf-8', errors='ignore') as f:
                for _ in range(lines):
                    line = f.readline()
                    if not line:
                        break
                    read_lines.append(line)
            return "".join(read_lines) if read_lines else "File is empty."
        except Exception as e:
            return f"Error reading file: {e}"

    @staticmethod
    def get_system_info() -> str:
        """Returns basic system and resource information."""
        try:
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            info = {
                "os": platform.system(),
                "release": platform.release(),
                "arch": platform.machine(),
                "cpu_count": psutil.cpu_count(logical=True),
                "memory_total_gb": round(mem.total / (1024**3), 2),
                "memory_available_gb": round(mem.available / (1024**3), 2),
                "memory_used_percent": f"{mem.percent}%",
                "disk_free_gb": round(disk.free / (1024**3), 2),
                "load_avg": os.getloadavg() if hasattr(os, "getloadavg") else "N/A"
            }
            return "\n".join([f"{k}: {v}" for k, v in info.items()])
        except Exception as e:
            return f"Error getting system info: {e}"

    @staticmethod
    def check_process(name: str) -> str:
        """Checks if a process is running by name."""
        try:
            processes = []
            for p in psutil.process_iter(['pid', 'name', 'status']):
                try:
                    p_name = p.info.get('name', '')
                    if p_name and name.lower() in p_name.lower():
                        processes.append(f"PID: {p.info['pid']} | Name: {p_name} | Status: {p.info.get('status', 'unknown')}")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if processes:
                return "\n".join(processes[:20])
            return f"No process found matching: {name}"
        except Exception as e:
            return f"Error checking process: {e}"

    @staticmethod
    def _ensure_sandbox() -> Optional[str]:
        """Ensures container sandbox is active, returning an error message if it fails."""
        if not sandbox_manager.is_active:
            if not sandbox_manager.start(mount_map={"./": "/workspace"}):
                return "Error: Could not start sandbox container. Ensure Docker or Podman is running."
        return None

    @staticmethod
    async def execute_code_in_sandbox(code: str, language: str = "python") -> str:
        """Executes code within the isolated container sandbox. NOTE: The sandbox is an isolated container and CANNOT inspect host processes, games, or GPU hardware. Use execute_host_command or analyze_gaming_session for host diagnostics."""
        err = await asyncio.to_thread(SystemTools._ensure_sandbox)
        if err:
            return err
        
        if language in ("bash", "sh"):
            rc, output = await sandbox_manager.execute_async(code, timeout=20)
        else:
            rc, output = await sandbox_manager.execute_async(f"{language} -c {repr(code)}", timeout=20)
        return f"Exit Code: {rc}\nOutput:\n{output}"

    @staticmethod
    async def execute_sandbox_command(command: str) -> str:
        """Executes an arbitrary shell command within the isolated container sandbox."""
        err = await asyncio.to_thread(SystemTools._ensure_sandbox)
        if err:
            return err
        rc, output = await sandbox_manager.execute_async(command, timeout=20)
        return f"Exit Code: {rc}\nOutput:\n{output}"

    @staticmethod
    def write_to_sandbox_file(path: str, content: str) -> str:
        """Writes content to a file inside the sandbox."""
        err = SystemTools._ensure_sandbox()
        if err:
            return err
        
        success = sandbox_manager.write_file(path, content)
        return f"Successfully wrote to {path}" if success else f"Failed to write to {path}"

    @staticmethod
    def read_sandbox_file(path: str, lines: int = 50) -> str:
        """Reads the first N lines of a file inside the sandbox."""
        err = SystemTools._ensure_sandbox()
        if err:
            return err
        return sandbox_manager.read_file(path, lines=lines)

    @staticmethod
    async def generate_image(prompt: str) -> str:
        """Generates an AI image from a text prompt and saves it to disk."""
        from google import genai
        from arch_ai.config import Config
        from arch_ai.image_generator import ImageGenerator

        if not Config.GEMINI_API_KEY:
            return "Error: GEMINI_API_KEY not configured."
        client = genai.Client(api_key=Config.GEMINI_API_KEY)
        gen = ImageGenerator(model=client)
        path = await gen.generate_image(prompt)
        return f"Image successfully generated and saved to: {path}" if path else "Image generation failed."

    @staticmethod
    async def execute_host_command(command: str, timeout: int = 15) -> str:
        """Executes a read-only or diagnostic shell command on the host system for system troubleshooting."""
        blocked = ["rm -rf", "mkfs", "dd if=", ":(){ :|:& };:", "> /dev/sd", "shutdown", "reboot"]
        for b in blocked:
            if b in command.lower():
                return f"Error: Command contains dangerous token '{b}' and was blocked."

        cmd_clean = command
        if cmd_clean.strip().startswith("sudo ") and not cmd_clean.strip().startswith("sudo -n "):
            cmd_clean = cmd_clean.strip().replace("sudo ", "sudo -n ", 1)

        env = dict(os.environ)
        env["PAGER"] = "cat"
        env["SYSTEMD_PAGER"] = "cat"

        effective_timeout = timeout if timeout is not None else 15
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd_clean,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=env,
                start_new_session=True
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout)
            except asyncio.TimeoutError:
                try:
                    import signal
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                return f"Error: Command execution timed out after {effective_timeout} seconds and was terminated."

            out = stdout.decode("utf-8", errors="ignore").strip()
            err = stderr.decode("utf-8", errors="ignore").strip()
            result = out if out else (err if err else "[No output]")
            if len(result) > 4000:
                result = result[:2000] + "\n... [OUTPUT TRUNCATED] ...\n" + result[-2000:]
            return f"Exit Code: {proc.returncode}\n{result}"
        except Exception as e:
            return f"Execution error: {e}"

    @staticmethod
    def write_file(path: str, content: str) -> str:
        """Safely creates or writes a file on the local host filesystem. Creates parent directories if needed and backs up existing file."""
        try:
            resolved = os.path.abspath(os.path.expanduser(path))
            forbidden_roots = ["/etc/shadow", "/etc/sudoers", "/boot", "/bin", "/sbin", "/usr/bin", "/usr/sbin"]
            if any(resolved.startswith(fb) for fb in forbidden_roots):
                return f"Error: Writing to protected system path '{resolved}' is blocked for security."

            os.makedirs(os.path.dirname(resolved), exist_ok=True)
            if os.path.exists(resolved):
                backup_path = f"{resolved}.bak"
                try:
                    shutil.copy2(resolved, backup_path)
                except Exception:
                    pass
            with open(resolved, 'w', encoding='utf-8') as f:
                f.write(content)
            return f"Successfully wrote {len(content)} bytes to {resolved}"
        except Exception as e:
            return f"Error writing file: {e}"

    @staticmethod
    def patch_file(path: str, target: str, replacement: str) -> str:
        """Surgically replaces a specific block of text in a local file."""
        try:
            resolved = os.path.abspath(os.path.expanduser(path))
            if not os.path.exists(resolved):
                return f"Error: File not found: {path}"
            with open(resolved, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            if target not in content:
                return f"Error: Target text block not found in {path}"
            new_content = content.replace(target, replacement, 1)
            with open(resolved, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return f"Successfully patched {path}"
        except Exception as e:
            return f"Error patching file: {e}"

    @staticmethod
    def scan_gaming_system() -> str:
        """Scans PC hardware, kernel parameters, GPU Vulkan drivers, and gaming daemons on Arch Linux for gaming readiness."""
        try:
            from arch_ai.gaming import GamingOptimizer
            data = GamingOptimizer.scan_system()
            return GamingOptimizer.format_scan_report(data)
        except Exception as e:
            return f"Error scanning gaming system: {e}"

    @staticmethod
    def analyze_gaming_session() -> str:
        """Analyzes active gaming processes, GPU & VRAM telemetry, CPU governor, Wayland compositor event pacing, and micro-stutter sources."""
        try:
            from arch_ai.gaming import GamingOptimizer
            data = GamingOptimizer.analyze_gaming_session()
            return GamingOptimizer.format_gaming_session_report(data)
        except Exception as e:
            return f"Error analyzing gaming session: {e}"

    @staticmethod
    def optimize_gaming_system() -> str:
        """Applies or previews Arch Linux performance optimizations (GameMode, sysctl, CPU governor) for gaming."""
        try:
            from arch_ai.gaming import GamingOptimizer
            success, actions, summary = GamingOptimizer.optimize_system(apply=True)
            return f"Gaming Optimization Report:\n{summary}"
        except Exception as e:
            return f"Error optimizing gaming system: {e}"

    @staticmethod
    async def get_steam_game_compatibility(game: str) -> str:
        """Checks ProtonDB compatibility rating, Linux launch parameters, and system requirements for a Steam game by title or AppID."""
        from arch_ai.steam_utils import SteamClient
        async with SteamClient() as client:
            return await client.format_game_report(game)

    @staticmethod
    async def get_steam_library() -> str:
        """Fetches the user's Steam game library or locally installed Steam games with playtimes and sizes."""
        from arch_ai.steam_utils import SteamClient
        async with SteamClient() as client:
            return await client.format_owned_games_summary(limit=15)

# Tools Schema for Gemini function calling
TOOLS_SCHEMA = [
    SystemTools.list_files,
    SystemTools.read_file,
    SystemTools.write_file,
    SystemTools.patch_file,
    SystemTools.get_system_info,
    SystemTools.check_process,
    SystemTools.scan_gaming_system,
    SystemTools.analyze_gaming_session,
    SystemTools.optimize_gaming_system,
    SystemTools.execute_host_command,
    SystemTools.execute_code_in_sandbox,
    SystemTools.execute_sandbox_command,
    SystemTools.write_to_sandbox_file,
    SystemTools.read_sandbox_file,
    SystemTools.generate_image,
    SystemTools.get_steam_game_compatibility,
    SystemTools.get_steam_library,
    SystemTools.index_directory,
    SystemTools.semantic_search,
    SystemTools.save_memory,
    SystemTools.clear_memory,
    SystemTools.spawn_background_researcher
]


TOOL_MAP = {fn.__name__: fn for fn in TOOLS_SCHEMA}





