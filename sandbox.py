import subprocess
import os
import sys
import uuid
import logging
import atexit
import signal
import threading
import shutil
import shlex
import asyncio
from typing import Tuple, Optional, Set, Dict, Any

from logger_utils import StructuredLogger
from config import Config

log = StructuredLogger(logging.getLogger("agent_terminal.sandbox"))

_active_sandboxes: Set["SandboxManager"] = set()
_signal_handlers_installed = False


def _cleanup_all_sandboxes():
    """Stops all active sandbox instances."""
    for sm in list(_active_sandboxes):
        try:
            sm.stop()
        except Exception as e:
            log.error(f"Error stopping sandbox during cleanup: {e}")


def setup_signal_handlers():
    """Installs signal handlers for process termination signals (SIGHUP, SIGTERM, etc.)
    to ensure sandboxes are cleanly stopped when the terminal closes or the process exits."""
    global _signal_handlers_installed
    if _signal_handlers_installed:
        return
    if threading.current_thread() is not threading.main_thread():
        return

    signals_to_catch = []
    for sig_name in ("SIGHUP", "SIGTERM", "SIGQUIT"):
        if hasattr(signal, sig_name):
            signals_to_catch.append(getattr(signal, sig_name))

    for sig in signals_to_catch:
        try:
            prev = signal.getsignal(sig)

            def make_handler(previous):
                def handler(signum, frame):
                    _cleanup_all_sandboxes()
                    if callable(previous) and previous not in (signal.SIG_DFL, signal.SIG_IGN):
                        previous(signum, frame)
                    else:
                        sys.exit(128 + signum)
                return handler

            signal.signal(sig, make_handler(prev))
        except (ValueError, AttributeError):
            pass

    _signal_handlers_installed = True


class SandboxManager:
    """Manages a containerized (Docker or Podman) sandbox for safe execution."""

    def __init__(
        self,
        image: Optional[str] = None,
        network_mode: Optional[str] = None,
        engine: Optional[str] = None
    ):
        self.image = image or getattr(Config, "SANDBOX_IMAGE", "archlinux:latest")
        self.network_mode = network_mode or getattr(Config, "SANDBOX_NETWORK", "bridge")
        self.engine = engine or self._detect_engine()
        self.container_name = f"ai-terminal-sandbox-{uuid.uuid4().hex[:8]}"
        self.is_active = False
        self.cwd = "/workspace"
        self.prev_cwd = "/workspace"
        self.env: dict[str, str] = {}
        self.mount_map: dict = {}
        self.read_only: bool = getattr(Config, "SANDBOX_READ_ONLY", False)
        self.mem_limit: str = getattr(Config, "SANDBOX_MEMORY", "512m")
        self.cpu_quota: int = getattr(Config, "SANDBOX_CPU_QUOTA", 50000)
        self.pids_limit: int = getattr(Config, "SANDBOX_PIDS_LIMIT", 256)

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass

    @classmethod
    def _detect_engine(cls) -> str:
        preferred = getattr(Config, "SANDBOX_ENGINE", "auto")
        if preferred in ("docker", "podman"):
            return preferred
        if shutil.which("docker"):
            return "docker"
        if shutil.which("podman"):
            return "podman"
        return "docker"

    def check_engine_health(self) -> Tuple[bool, str]:
        """Checks if the container engine executable is available and the daemon is reachable."""
        if not shutil.which(self.engine):
            return False, f"Container engine '{self.engine}' executable not found in PATH. Please install Docker or Podman."
        try:
            res = subprocess.run([self.engine, "info"], capture_output=True, text=True, timeout=3)
            if res.returncode != 0:
                err_snippet = (res.stderr or res.stdout).strip().splitlines()
                first_line = err_snippet[0] if err_snippet else "daemon unreachable"
                return False, f"Cannot connect to {self.engine} daemon ({first_line}). On Arch Linux, try: sudo systemctl start {self.engine}"
            return True, ""
        except subprocess.TimeoutExpired:
            return False, f"Timeout checking {self.engine} daemon status."
        except Exception as e:
            return False, f"Error checking {self.engine}: {e}"

    def _build_env_args(self) -> list[str]:
        """Builds container -e environment flags from self.env."""
        args = []
        for k, v in self.env.items():
            args.extend(["-e", f"{k}={v}"])
        return args

    def handle_cd(self, command: str) -> Tuple[bool, Optional[str]]:
        """Handles cd commands inside the container and updates self.cwd."""
        cmd = command.strip()
        try:
            parts = shlex.split(cmd)
        except ValueError:
            parts = cmd.split()

        if not parts or parts[0] != "cd":
            return False, None

        # Pass through compound commands (e.g. cd /tmp && ls) to the shell
        if any(op in parts for op in ("&&", ";", "||", "|", "&")):
            return False, None

        target = parts[1] if len(parts) > 1 else "/workspace"

        # If sandbox is not running, compute target using normalized path fallback
        if not self.is_active:
            if target == "-":
                target_path = self.prev_cwd
            elif target.startswith("/"):
                target_path = os.path.normpath(target)
            elif target == "~":
                target_path = "/root"
            elif target.startswith("~/"):
                target_path = os.path.normpath(os.path.join("/root", target[2:]))
            else:
                target_path = os.path.normpath(os.path.join(self.cwd, target))
            self.prev_cwd = self.cwd
            self.cwd = target_path
            return True, None

        # Resolve directory natively inside the active container (handles symlinks, ~, spaces)
        try:
            proc = subprocess.run([
                self.engine, "exec", "-w", self.cwd, self.container_name,
                "sh", "-c", 'cd "$1" 2>/dev/null && pwd', "_", target
            ], capture_output=True, text=True)
            if proc.returncode == 0 and proc.stdout.strip():
                self.prev_cwd = self.cwd
                self.cwd = proc.stdout.strip()
                return True, None
            else:
                return True, f"cd: {target}: No such file or directory"
        except Exception as e:
            return True, f"cd error: {e}"

    def handle_export(self, command: str) -> bool:
        """Handles export and unset commands to maintain container environment state."""
        cmd = command.strip()
        if cmd.startswith("export "):
            var_part = cmd[7:].strip()
            if "=" in var_part:
                k, v = var_part.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k:
                    self.env[k] = v
                    return True
        elif cmd.startswith("unset "):
            var_name = cmd[6:].strip()
            self.env.pop(var_name, None)
            return True
        return False

    def start(
        self,
        mem_limit: Optional[str] = None,
        cpu_quota: Optional[int] = None,
        mount_map: Optional[dict] = None,
        read_only: Optional[bool] = None,
        network_mode: Optional[str] = None
    ) -> bool:
        """Starts a persistent container with resource limits, security caps, and volume mounts."""
        setup_signal_handlers()

        effective_mem = mem_limit or getattr(Config, "SANDBOX_MEMORY", "512m")
        effective_cpu = cpu_quota if cpu_quota is not None else getattr(Config, "SANDBOX_CPU_QUOTA", 50000)
        effective_pids = getattr(Config, "SANDBOX_PIDS_LIMIT", 256)
        effective_ro = read_only if read_only is not None else getattr(Config, "SANDBOX_READ_ONLY", False)
        if network_mode:
            self.network_mode = network_mode

        self.mem_limit = effective_mem
        self.cpu_quota = effective_cpu
        self.pids_limit = effective_pids
        self.read_only = effective_ro
        self.mount_map = mount_map or {}

        if not self.is_active:
            self.container_name = f"ai-terminal-sandbox-{uuid.uuid4().hex[:8]}"

        # Pre-flight engine check to avoid false image pull errors when daemon is offline
        healthy, err = self.check_engine_health()
        if not healthy:
            log.error(f"Sandbox engine error: {err}")
            return False

        try:
            # Check if image exists, if not pull it
            subprocess.run([self.engine, "image", "inspect", self.image], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            log.info(f"Pulling image {self.image} via {self.engine}...")
            try:
                subprocess.run([self.engine, "pull", self.image], check=True, timeout=20)
            except Exception as e:
                log.error(f"Failed to pull image {self.image}: {e}")
                return False

        try:
            # Prepare mount arguments
            mount_args = []
            if self.mount_map:
                for host_path, container_path in self.mount_map.items():
                    abs_host = os.path.abspath(os.path.expanduser(host_path))
                    if ":" in container_path:
                        mount_args.extend(["-v", f"{abs_host}:{container_path}"])
                    else:
                        mode = ":ro" if effective_ro else ":rw"
                        mount_args.extend(["-v", f"{abs_host}:{container_path}{mode}"])

            cmd = [
                self.engine, "run", "-d", "--rm", 
                "--network", self.network_mode,
                "--name", self.container_name,
                "--memory", effective_mem,
                "--cpu-quota", str(effective_cpu),
                "--pids-limit", str(effective_pids),
                "--security-opt", "no-new-privileges",
                "--init"
            ]
            cmd.extend(mount_args)
            cmd.extend([self.image, "tail", "-f", "/dev/null"])

            subprocess.run(cmd, check=True, timeout=15)
            self.is_active = True
            _active_sandboxes.add(self)
            self.cwd = "/workspace"
            self.prev_cwd = "/workspace"
            self.env = {}

            # Create /workspace if it doesn't exist
            subprocess.run([self.engine, "exec", self.container_name, "mkdir", "-p", "/workspace"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)

            log.info("Sandbox started successfully", 
                     container_name=self.container_name, 
                     engine=self.engine,
                     mounts=self.mount_map)
            return True

        except Exception as e:
            log.error(f"Failed to start sandbox: {e}")
            return False

    def stop(self):
        """Stops and removes the sandbox container."""
        if self.is_active:
            try:
                subprocess.run(
                    [self.engine, "stop", "-t", "1", self.container_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                )
            except Exception:
                try:
                    subprocess.run(
                        [self.engine, "rm", "-f", self.container_name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=3,
                    )
                except Exception:
                    pass
            finally:
                self.is_active = False
                _active_sandboxes.discard(self)

    def execute(self, command: str, timeout: Optional[int] = None) -> Tuple[int, str]:
        """Executes a command inside the sandbox container with persistent cwd and env."""
        if not self.is_active:
            return 1, "Sandbox is not active."

        # Check internal cd
        is_cd, cd_err = self.handle_cd(command)
        if is_cd:
            return (1, cd_err) if cd_err else (0, "")

        # Check internal export
        if self.handle_export(command):
            return 0, ""

        exec_timeout = timeout or getattr(Config, "SANDBOX_TIMEOUT", 30)

        try:
            cmd = [self.engine, "exec", "-w", self.cwd]
            cmd.extend(self._build_env_args())
            cmd.extend([self.container_name, "sh", "-c", command])

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=exec_timeout)
            output = result.stdout + result.stderr
            return result.returncode, output
        except subprocess.TimeoutExpired:
            return 124, f"Command timed out after {exec_timeout} seconds."
        except Exception as e:
            return 1, f"Execution error: {str(e)}"

    async def execute_async(self, command: str, timeout: Optional[int] = None) -> Tuple[int, str]:
        """Executes a command inside the sandbox container asynchronously without blocking the event loop."""
        return await asyncio.to_thread(self.execute, command, timeout)

    def execute_interactive(self, command: str) -> int:
        """Executes a command in the sandbox with full terminal interaction, cwd, and env state."""
        if not self.is_active:
            return 1

        clean_cmd = command.strip()
        tty_flags = ["-it"] if (sys.stdin.isatty() and sys.stdout.isatty()) else ["-i"]

        # Handle raw subshell request (e.g. 'shell', 'bash', 'sh')
        if clean_cmd in ["shell", "bash", "sh"]:
            cmd = [self.engine, "exec"] + tty_flags + ["-w", self.cwd]
            cmd.extend(self._build_env_args())
            cmd.extend([self.container_name, "bash"])
            try:
                result = subprocess.run(cmd)
                return result.returncode
            except Exception:
                # Fallback to sh if bash is unavailable
                cmd[-1] = "sh"
                return subprocess.run(cmd).returncode

        # Check stateful cd command
        is_cd, cd_err = self.handle_cd(clean_cmd)
        if is_cd:
            if cd_err:
                print(cd_err)
                return 1
            return 0

        # Check stateful export/unset command
        if self.handle_export(clean_cmd):
            return 0
            
        try:
            cmd = [self.engine, "exec"] + tty_flags + ["-w", self.cwd]
            cmd.extend(self._build_env_args())
            cmd.extend([self.container_name, "sh", "-c", command])
            result = subprocess.run(cmd)
            return result.returncode
        except Exception as e:
            log.error(f"Interactive execution failed: {e}")
            return 1

    def connect(self, container_id: str) -> bool:
        """Connects to an existing running container."""
        setup_signal_handlers()
        try:
            result = subprocess.run([self.engine, "inspect", "-f", "{{.State.Running}}", container_id], 
                                    capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip() == "true":
                self.container_name = container_id
                self.is_active = True
                _active_sandboxes.add(self)
                return True
            return False
        except Exception:
            return False

    def write_file(self, path: str, content: str) -> bool:
        """Writes content to a file inside the sandbox safely, ensuring parent dirs exist."""
        if not self.is_active:
            return False
            
        try:
            target_path = path if path.startswith("/") else os.path.normpath(os.path.join(self.cwd, path))
            dir_path = os.path.dirname(target_path)
            if dir_path:
                subprocess.run(
                    [self.engine, "exec", self.container_name, "mkdir", "-p", dir_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False
                )
            process = subprocess.Popen([
                self.engine, "exec", "-i", "-w", self.cwd, self.container_name, "sh", "-c", 'cat > "$1"', "_", target_path
            ], stdin=subprocess.PIPE, text=True)
            process.communicate(input=content, timeout=10)
            return process.returncode == 0
        except Exception as e:
            log.error(f"Sandbox write_file failed: {e}")
            return False

    def read_file(self, path: str, lines: int = 50) -> str:
        """Reads the first N lines of a file inside the sandbox safely."""
        if not self.is_active:
            return "Error: Sandbox is not active."
        try:
            target_path = path if path.startswith("/") else os.path.normpath(os.path.join(self.cwd, path))
            res = subprocess.run(
                [self.engine, "exec", "-w", self.cwd, self.container_name, "sh", "-c", 'head -n "$1" "$2" 2>&1', "_", str(lines), target_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            if res.returncode == 0:
                return res.stdout if res.stdout else "File is empty."
            else:
                return f"Error reading sandbox file: {res.stdout or res.stderr}"
        except Exception as e:
            return f"Error reading sandbox file: {e}"

    def get_status(self) -> Dict[str, Any]:
        """Returns diagnostic details about the sandbox container."""
        return {
            "is_active": self.is_active,
            "engine": self.engine,
            "container_name": self.container_name,
            "image": self.image,
            "cwd": self.cwd,
            "network_mode": self.network_mode,
            "env_vars_count": len(self.env),
            "mount_map": self.mount_map,
            "read_only": self.read_only,
            "mem_limit": self.mem_limit,
            "cpu_quota": self.cpu_quota,
            "pids_limit": self.pids_limit,
        }

    @classmethod
    def cleanup_orphaned_containers(cls, engine: Optional[str] = None):
        """Cleans up any orphaned sandbox containers from previous sessions."""
        eng = engine or "docker"
        try:
            res = subprocess.run(
                [eng, "ps", "-a", "-q", "--filter", "name=ai-terminal-sandbox-"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                container_ids = res.stdout.strip().split()
                for cid in container_ids:
                    subprocess.run(
                        [eng, "rm", "-f", cid],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
        except Exception as e:
            log.debug(f"Failed to cleanup orphaned containers: {e}")


# Global instance for shared use
sandbox_manager = SandboxManager()
atexit.register(_cleanup_all_sandboxes)
setup_signal_handlers()
