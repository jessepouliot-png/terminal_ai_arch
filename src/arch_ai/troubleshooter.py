import sys
import logging
import asyncio
from typing import Optional, Any, Dict

from google.genai import types
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.markup import escape

from arch_ai.config import Config, BORDERLESS_BOX
from arch_ai.searcher import WebSearcher

# Configure logging for production use
log = logging.getLogger(__name__)

from arch_ai.clipboard_utils import extract_primary_command
from arch_ai.response_utils import extract_full_model_response

class Troubleshooter:
    """An AI diagnostic engine with Google Search Grounding for accurate fixes."""
    
    def __init__(self, console: Optional[Console] = None, model: Any = None):
        self.console = console or Console()
        self.model = model # This is the genai.Client
        self.searcher = WebSearcher(model=model)
        self.grounding_tool = types.Tool(google_search=types.GoogleSearch())

    async def get_solutions(self, command: str, error_msg: str) -> str:
        """Queries Gemini with native grounding for diagnostic solutions."""
        if not self.model:
            return "Diagnostic engine not initialized."

        prompt = (
            f"COMMAND_FAILURE_ANALYSIS\n"
            f"Input: `{command}`\n"
            f"Context: {error_msg}\n\n"
            "DIAGNOSIS: Identify typos, syntax errors, or system failures. Provide a concise root cause. "
            "Deliver 1-3 numbered Markdown fixes based on Arch Linux standards. Ground responses using search data."
        )
        
        try:
            response = await self.model.aio.models.generate_content(
                model=Config.MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[self.grounding_tool]
                )
            )
            
            reply = extract_full_model_response(response, include_function_calls=False)
            if not reply:
                reply = "AI empty response."

            if response and getattr(response, "candidates", None) and len(response.candidates) > 0:
                candidate = response.candidates[0]
                grounding = getattr(candidate, "grounding_metadata", None)
                if grounding:
                    reply += "\n\n---\n*Diagnostic reinforced with real-time Google Search data.*"
            return reply
            
        except Exception as e:
            log.exception(f"Diagnostic Engine Failure: {e}")
            return f"An internal error occurred during analysis: {str(e)}"

    async def troubleshoot(self, command: str, error_msg: str) -> Optional[str]:
        """
        Orchestrates the full troubleshooting workflow: Local AI -> Web Search (if needed).

        Args:
            command: The failed command string.
            error_msg: The error message received from the system.

        Returns:
            The primary suggested fix command if one was identified, else None.
        """
        self.console.print(Panel(
            f"Command: [bold cyan]{escape(command)}[/bold cyan]\nError: [bold red]{escape(error_msg)}[/bold red]",
            title="[DIAGNOSTIC ENGINE]",
            border_style="yellow",
            box=BORDERLESS_BOX,
            padding=(1, 2),
            expand=True
        ))
        
        solutions = ""
        with self.console.status("[bold green]Performing system analysis...", spinner="dots"):
            solutions = await self.get_solutions(command, error_msg)
        
        suggested_cmd = extract_primary_command(solutions) if solutions else None

        # Check if internal intelligence requires external lookup
        if "SEARCH_REQUIRED" in solutions:
            self.console.print("[italic blue]Internal diagnostic inconclusive. Consulting global knowledge base...[/italic blue]")
            
            # Formulate a targeted search query
            query = f"Arch Linux troubleshooting: {command} error: {error_msg}"
            
            with self.console.status("[bold blue]Consulting global web intelligence...", spinner="bouncingBar"):
                results = await self.searcher.search_and_analyze(query)
            
            # The WebSearcher handles its own complex UI display
            self.searcher.display_results(results)
            if not suggested_cmd and results:
                suggested_cmd = extract_primary_command(str(results))
        else:
            # Display the AI-generated solutions in a clean panel
            self.console.print(Panel(
                Markdown(solutions),
                title="[DIAGNOSTIC REPORT]",
                border_style="green",
                box=BORDERLESS_BOX,
                padding=(1, 2),
                expand=True
            ))

        return suggested_cmd



def main():
    import os
    from google import genai
    
    command = sys.argv[1] if len(sys.argv) > 1 else "ls /root"
    error = sys.argv[2] if len(sys.argv) > 2 else "Permission denied"
    
    api_key = os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    
    troubleshooter = Troubleshooter(model=client)
    asyncio.run(troubleshooter.troubleshoot(command, error))

if __name__ == "__main__":
    main()
