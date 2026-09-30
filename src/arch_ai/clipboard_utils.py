import os
import sys
import re
import base64
import shutil
import logging
import subprocess
from functools import lru_cache
from typing import List, Optional

logger = logging.getLogger("agent_terminal.clipboard")

RE_FENCED_CODE = re.compile(r"```(?:bash|sh|zsh|shell)?\s*\n([\s\S]*?)\n```", re.IGNORECASE)
RE_INLINE_CODE = re.compile(r"`([^`\n]+)`")

COMMAND_STARTERS = (
    "sudo ", "pacman ", "yay ", "systemctl ", "docker ", "podman ",
    "git ", "cd ", "mkdir ", "chmod ", "chown ", "cat ", "echo ",
    "export ", "rm ", "cp ", "mv ", "ls ", "grep ", "find ", "ip ",
    "journalctl ", "uname ", "curl ", "wget "
)

@lru_cache(maxsize=8)
def _find_binary(name: str) -> Optional[str]:
    return shutil.which(name)

def emit_osc52(text: str) -> bool:
    """
    Emits an OSC 52 ANSI escape sequence to copy text to the terminal emulator clipboard.
    Compatible with Alacritty, Kitty, WezTerm, Foot, xterm, tmux, and modern terminal multiplexers.
    """
    if not text:
        return False
    try:
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        if os.environ.get("TMUX"):
            seq = f"\x1bPtmux;\x1b\x1b]52;c;{encoded}\x07\x1b\\"
        else:
            seq = f"\x1b]52;c;{encoded}\x07"
        
        # Write escape sequence to stdout
        if hasattr(sys.stdout, "write"):
            sys.stdout.write(seq)
            sys.stdout.flush()
        return True
    except Exception as e:
        logger.debug(f"OSC 52 emission failed: {e}")
        return False

def copy_to_clipboard(text: str) -> bool:
    """
    Copies text to the system clipboard across Wayland (wl-copy), X11 (xclip/xsel),
    and terminal emulators (OSC 52).
    
    Returns True if at least one clipboard mechanism succeeded.
    """
    if text is None:
        return False
    
    text_str = str(text)
    copied = False

    # 1. Try Wayland (wl-copy) if running in a Wayland session
    if os.environ.get("WAYLAND_DISPLAY"):
        wl_copy_bin = _find_binary("wl-copy")
        if wl_copy_bin:
            try:
                proc = subprocess.Popen(
                    [wl_copy_bin],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                proc.communicate(input=text_str.encode("utf-8"), timeout=1.0)
                if proc.returncode == 0:
                    copied = True
            except Exception as e:
                logger.debug(f"wl-copy failed: {e}")

    # 2. Try X11 (xclip / xsel) if running under X11 or Wayland with Xwayland fallback
    if not copied and os.environ.get("DISPLAY"):
        xclip_bin = _find_binary("xclip")
        if xclip_bin:
            try:
                proc = subprocess.Popen(
                    [xclip_bin, "-selection", "clipboard"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                proc.communicate(input=text_str.encode("utf-8"), timeout=1.0)
                if proc.returncode == 0:
                    copied = True
            except Exception as e:
                logger.debug(f"xclip failed: {e}")

        if not copied:
            xsel_bin = _find_binary("xsel")
            if xsel_bin:
                try:
                    proc = subprocess.Popen(
                        [xsel_bin, "--clipboard", "--input"],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    proc.communicate(input=text_str.encode("utf-8"), timeout=1.0)
                    if proc.returncode == 0:
                        copied = True
                except Exception as e:
                    logger.debug(f"xsel failed: {e}")

    # 3. Fallback: if session variables were absent or tools failed, try available binaries directly
    if not copied:
        for bin_name, args in [("wl-copy", []), ("xclip", ["-selection", "clipboard"]), ("xsel", ["--clipboard", "--input"])]:
            binary = _find_binary(bin_name)
            if binary:
                try:
                    proc = subprocess.Popen([binary] + args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    proc.communicate(input=text_str.encode("utf-8"), timeout=1.0)
                    if proc.returncode == 0:
                        copied = True
                        break
                except Exception:
                    pass

    # 4. Also emit OSC 52 for terminal-level clipboard synchronization
    osc_success = emit_osc52(text_str)
    if osc_success:
        copied = True

    return copied

def get_clipboard_text() -> Optional[str]:
    """Retrieves current text from the clipboard via wl-paste, xclip, or xsel."""
    if os.environ.get("WAYLAND_DISPLAY"):
        wl_paste_bin = _find_binary("wl-paste")
        if wl_paste_bin:
            try:
                res = subprocess.run([wl_paste_bin, "--no-newline"], capture_output=True, text=True, timeout=1.0)
                if res.returncode == 0:
                    return res.stdout
            except Exception as e:
                logger.debug(f"wl-paste failed: {e}")

    if os.environ.get("DISPLAY"):
        xclip_bin = _find_binary("xclip")
        if xclip_bin:
            try:
                res = subprocess.run([xclip_bin, "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=1.0)
                if res.returncode == 0:
                    return res.stdout
            except Exception as e:
                logger.debug(f"xclip read failed: {e}")

        xsel_bin = _find_binary("xsel")
        if xsel_bin:
            try:
                res = subprocess.run([xsel_bin, "--clipboard", "--output"], capture_output=True, text=True, timeout=1.0)
                if res.returncode == 0:
                    return res.stdout
            except Exception as e:
                logger.debug(f"xsel read failed: {e}")

    for bin_name, args in [("wl-paste", ["--no-newline"]), ("xclip", ["-selection", "clipboard", "-o"]), ("xsel", ["--clipboard", "--output"])]:
        binary = _find_binary(bin_name)
        if binary:
            try:
                res = subprocess.run([binary] + args, capture_output=True, text=True, timeout=1.0)
                if res.returncode == 0:
                    return res.stdout
            except Exception:
                pass

    return None

def extract_commands_from_text(text: str) -> List[str]:
    """
    Extracts shell commands from Markdown text (code blocks or inline code).
    Prioritizes fenced code blocks (```bash ... ```).
    """
    if not text:
        return []

    commands: List[str] = []

    # 1. Match fenced code blocks
    blocks = RE_FENCED_CODE.findall(text)
    for block in blocks:
        cleaned = block.strip()
        if cleaned:
            commands.append(cleaned)

    # 2. If no fenced code blocks, match inline backtick code
    if not commands:
        matches = RE_INLINE_CODE.findall(text)
        for m in matches:
            cmd = m.strip()
            if any(cmd.startswith(starter) for starter in COMMAND_STARTERS) or ((" " in cmd or "-" in cmd) and len(cmd) > 2):
                commands.append(cmd)

    return commands

def extract_primary_command(text: str) -> Optional[str]:
    """Extracts the first or primary command found in Markdown text."""
    cmds = extract_commands_from_text(text)
    if cmds:
        return cmds[0]
    return None
