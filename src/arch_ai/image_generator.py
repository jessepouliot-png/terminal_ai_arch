import os
import re
import shutil
import asyncio
import logging
from functools import lru_cache
from datetime import datetime
from typing import Optional, Tuple, Any

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

from arch_ai.config import Config, BORDERLESS_BOX

log = logging.getLogger("agent_terminal.image")

_RE_SLUG_CHARS = re.compile(r'[^a-zA-Z0-9_\s-]')
_RE_SPACES_DASHES = re.compile(r'[\s-]+')

@lru_cache(maxsize=8)
def _find_binary(name: str) -> Optional[str]:
    """Caches executable path lookups to eliminate redundant disk scans."""
    return shutil.which(name)

class ImageGenerator:
    """Generates and manages AI images using Gemini's native image models."""

    def __init__(self, console: Optional[Console] = None, model: Any = None):
        self.console = console or Console()
        self.model = model
        self.output_dir = Config.IMAGE_DIR
        os.makedirs(self.output_dir, exist_ok=True)

    def _sanitize_filename(self, prompt: str, ext: str = ".png") -> str:
        """Creates a filesystem-safe filename based on the prompt and timestamp."""
        slug = _RE_SLUG_CHARS.sub('', prompt).strip().lower()
        slug = _RE_SPACES_DASHES.sub('_', slug)[:30]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{slug}_{timestamp}" if slug else f"image_{timestamp}"
        return f"{name}{ext}"

    async def _render_terminal_thumbnail(self, file_path: str):
        """Attempts to render an inline terminal graphic preview if chafa is installed."""
        chafa_bin = _find_binary("chafa")
        if chafa_bin:
            try:
                proc = await asyncio.create_subprocess_exec(
                    chafa_bin, "--size=50x20", "--format=symbols", file_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                if stdout:
                    self.console.print(stdout.decode("utf-8", errors="ignore"))
            except Exception as e:
                log.debug(f"Terminal thumbnail preview error: {e}")

    async def open_image(self, file_path: str) -> bool:
        """Opens the image in the system's default image viewer (e.g. xdg-open)."""
        xdg_bin = _find_binary("xdg-open")
        if xdg_bin and os.path.exists(file_path):
            try:
                proc = await asyncio.create_subprocess_exec(
                    xdg_bin, file_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                return True
            except Exception as e:
                log.error(f"Failed to open image with xdg-open: {e}")
        return False

    async def generate_image(self, prompt: str, output_dir: Optional[str] = None, open_after: bool = False) -> Optional[str]:
        """Generates an image from a prompt, saves it to disk, and displays a summary."""
        if not prompt.strip():
            self.console.print("[bold yellow]Please provide an image prompt.[/bold yellow]")
            return None

        if not self.model:
            self.console.print("[bold red]AI Client not initialized.[/bold red]")
            return None

        target_dir = output_dir or self.output_dir
        os.makedirs(target_dir, exist_ok=True)

        try:
            with self.console.status(f"[bold {Config.COLOR_IMAGE}]🎨 Generating AI Image with {Config.IMAGE_MODEL}...", spinner="dots"):
                response = await self.model.aio.models.generate_content(
                    model=Config.IMAGE_MODEL,
                    contents=f"Generate an image: {prompt}"
                )

            if not response or not response.candidates:
                self.console.print("[bold red]Image Generation Failed: No response received.[/bold red]")
                return None

            candidate = response.candidates[0]
            image_data = None
            mime_type = "image/png"
            ai_text = ""

            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if getattr(part, "inline_data", None):
                        image_data = part.inline_data.data
                        mime_type = getattr(part.inline_data, "mime_type", "image/png")
                    elif getattr(part, "text", None):
                        ai_text = part.text.strip()

            if not image_data:
                err_msg = ai_text or "Model did not return image data."
                self.console.print(f"[bold red]Image Generation Failed:[/bold red] {err_msg}")
                return None

            # Determine extension
            ext = ".jpg" if "jpeg" in mime_type or "jpg" in mime_type else ".png"
            filename = self._sanitize_filename(prompt, ext=ext)
            file_path = os.path.abspath(os.path.join(target_dir, filename))

            # Save image bytes asynchronously to avoid blocking event loop
            def _write_bytes():
                with open(file_path, "wb") as f:
                    f.write(image_data)
            await asyncio.to_thread(_write_bytes)

            file_size_kb = round(len(image_data) / 1024, 1)
            file_size_str = f"{round(file_size_kb / 1024, 2)} MB" if file_size_kb > 1024 else f"{file_size_kb} KB"

            # Optional terminal thumbnail preview if chafa is present
            await self._render_terminal_thumbnail(file_path)

            # Render summary panel
            summary_md = [
                f"### 🖼️ Image Generated Successfully",
                f"- **Prompt**: *{prompt}*",
                f"- **Saved To**: `{file_path}`",
                f"- **Format**: `{mime_type}` ({file_size_str})",
                "",
                f"> **Tip**: View your image with `xdg-open {file_path}` or from your file manager."
            ]

            if ai_text:
                summary_md.insert(2, f"- **Description**: {ai_text}")

            self.console.print(Panel(
                Markdown("\n".join(summary_md)),
                title=f"[bold {Config.COLOR_IMAGE}]🎨 IMAGE GENERATOR[/bold {Config.COLOR_IMAGE}]",
                border_style=Config.COLOR_IMAGE,
                box=BORDERLESS_BOX,
                padding=(1, 2),
                expand=True
            ))

            if open_after:
                await self.open_image(file_path)

            return file_path

        except Exception as e:
            log.exception(f"Image generation error: {e}")
            self.console.print(f"[bold red]Image Generation Error:[/bold red] {e}")
            return None

