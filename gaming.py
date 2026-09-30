import os
import glob
import shutil
import subprocess
import psutil
import logging
import re
from typing import Optional, List, Dict, Union, Any, Tuple
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.live import Live
from rich.spinner import Spinner, SPINNERS
from rich.markup import escape
from google.genai import types
from steam_utils import SteamClient
from config import Config, BORDERLESS_BOX
from response_utils import extract_full_model_response

# Register custom retro-arcade gaming spinner with animated gaming icons
GAMING_SPINNER_NAME = "gaming_arcade"
if GAMING_SPINNER_NAME not in SPINNERS:
    SPINNERS[GAMING_SPINNER_NAME] = {
        "interval": 80,
        "frames": [
            "🎮 ⠋", "🕹️ ⠙", "👾 ⠹", "⚡ ⠸", "🚀 ⠼",
            "🔥 ⠴", "🎯 ⠦", "✨ ⠧", "💎 ⠇", "👑 ⠏"
        ]
    }

log = logging.getLogger("agent_terminal")

class GamingOptimizer:
    """Arch Linux PC diagnostic scanner and system optimizer for high-performance gaming."""

    @staticmethod
    def scan_system() -> Dict[str, Any]:
        """Scans hardware, kernel parameters, Vulkan drivers, and gaming daemons."""
        score = 100
        issues: List[str] = []
        recommendations: List[Dict[str, str]] = []

        # 1. CPU & Governor
        cpu_count = psutil.cpu_count(logical=True) or 1
        governor = "unknown"
        gov_files = glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor")
        if gov_files:
            try:
                with open(gov_files[0], "r", encoding="utf-8") as f:
                    governor = f.read().strip()
            except Exception:
                pass

        epp = "unknown"
        epp_files = glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference")
        if epp_files:
            try:
                with open(epp_files[0], "r", encoding="utf-8") as f:
                    epp = f.read().strip()
            except Exception:
                pass

        if governor in ("powersave", "conservative"):
            score -= 15
            issues.append(f"CPU scaling governor is '{governor}' instead of 'performance'.")
            recommendations.append({
                "category": "CPU",
                "action": "Set CPU Governor to Performance",
                "command": "echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"
            })

        # 2. Kernel Sysctl Parameters
        max_map_count = 65530
        if os.path.exists("/proc/sys/vm/max_map_count"):
            try:
                with open("/proc/sys/vm/max_map_count", "r", encoding="utf-8") as f:
                    max_map_count = int(f.read().strip())
            except Exception:
                pass

        if max_map_count < 1048576:
            score -= 25
            issues.append(f"vm.max_map_count is {max_map_count} (Modern games & Proton require >= 1048576).")
            recommendations.append({
                "category": "Kernel/Memory",
                "action": "Increase vm.max_map_count for Wine/Proton stability",
                "command": "sudo sysctl -w vm.max_map_count=2147483642 && echo 'vm.max_map_count=2147483642' | sudo tee /etc/sysctl.d/80-game-max-map-count.conf"
            })

        split_lock = None
        if os.path.exists("/proc/sys/kernel/split_lock_mitigate"):
            try:
                with open("/proc/sys/kernel/split_lock_mitigate", "r", encoding="utf-8") as f:
                    split_lock = int(f.read().strip())
            except Exception:
                pass

        if split_lock == 1:
            score -= 10
            issues.append("kernel.split_lock_mitigate is active (causes frame drops in games like God of War).")
            recommendations.append({
                "category": "Kernel",
                "action": "Disable split lock mitigation to eliminate game stutter",
                "command": "sudo sysctl -w kernel.split_lock_mitigate=0 && echo 'kernel.split_lock_mitigate=0' | sudo tee /etc/sysctl.d/80-splitlock.conf"
            })

        swappiness = 60
        if os.path.exists("/proc/sys/vm/swappiness"):
            try:
                with open("/proc/sys/vm/swappiness", "r", encoding="utf-8") as f:
                    swappiness = int(f.read().strip())
            except Exception:
                pass

        if swappiness > 20:
            score -= 5
            recommendations.append({
                "category": "Memory",
                "action": "Reduce swappiness for gaming responsiveness",
                "command": "sudo sysctl -w vm.swappiness=10"
            })

        # 3. GPU & Vulkan Drivers
        gpu_detected = []
        if os.path.exists("/dev/nvidia0"):
            gpu_detected.append("NVIDIA")
        if os.path.exists("/dev/dri/card0"):
            gpu_detected.append("AMD/Intel")
        gpu_str = " + ".join(gpu_detected) if gpu_detected else "Standard Graphics"

        vulkan_icds = glob.glob("/usr/share/vulkan/icd.d/*.json")
        has_32bit_icd = any("i686" in f or "32" in f for f in vulkan_icds)
        has_radv = any("radeon_icd" in f for f in vulkan_icds)
        has_amdvlk = any("amd_icd" in f for f in vulkan_icds)
        has_nvidia = any("nvidia" in f for f in vulkan_icds)

        if not vulkan_icds:
            score -= 30
            issues.append("No Vulkan ICD drivers detected! Vulkan is required for Proton & DXVK.")
            recommendations.append({
                "category": "Vulkan",
                "action": "Install Arch Linux Vulkan packages",
                "command": "sudo pacman -S --needed vulkan-radeon lib32-vulkan-radeon vulkan-icd-loader lib32-vulkan-icd-loader"
            })

        # 4. Gaming Daemons & Helpers
        gamemode_bin = bool(shutil.which("gamemoded"))
        mangohud_bin = bool(shutil.which("mangohud"))
        gamescope_bin = bool(shutil.which("gamescope"))
        wine_bin = bool(shutil.which("wine"))

        gamemode_active = False
        if gamemode_bin:
            try:
                res = subprocess.run(["systemctl", "--user", "is-active", "gamemoded"], capture_output=True, text=True, timeout=2)
                gamemode_active = (res.stdout.strip() == "active")
            except Exception:
                pass

        if not gamemode_bin:
            score -= 15
            issues.append("Feral GameMode is not installed (vital for automatic CPU/GPU optimization during games).")
            recommendations.append({
                "category": "Gaming Tools",
                "action": "Install GameMode & 32-bit helper",
                "command": "sudo pacman -S --needed gamemode lib32-gamemode && systemctl --user enable --now gamemoded"
            })
        elif not gamemode_active:
            score -= 5
            recommendations.append({
                "category": "Gaming Tools",
                "action": "Enable and start user GameMode service",
                "command": "systemctl --user enable --now gamemoded"
            })

        if not mangohud_bin:
            score -= 5
            recommendations.append({
                "category": "Gaming Tools",
                "action": "Install MangoHud (Vulkan/OpenGL performance overlay)",
                "command": "sudo pacman -S --needed mangohud lib32-mangohud"
            })

        if not gamescope_bin:
            score -= 5
            recommendations.append({
                "category": "Gaming Tools",
                "action": "Install Gamescope micro-compositor (HDR & FSR upscaling)",
                "command": "sudo pacman -S --needed gamescope"
            })

        # 5. Memory
        mem = psutil.virtual_memory()
        total_ram_gb = round(mem.total / (1024**3), 2)
        swap = psutil.swap_memory()
        swap_gb = round(swap.total / (1024**3), 2)

        if total_ram_gb < 12:
            score -= 10
            issues.append(f"System RAM is {total_ram_gb} GB (16 GB+ recommended for modern AAA gaming).")

        final_score = max(0, min(100, score))
        if final_score >= 90:
            rating = "🏆 OPTIMIZED (Ready for Competitive & AAA Gaming)"
        elif final_score >= 70:
            rating = "🟡 GOOD (Minor Tweaks Recommended for Best FPS)"
        elif final_score >= 50:
            rating = "⚠️ SUBOPTIMAL (Crucial Gaming Tweaks Missing)"
        else:
            rating = "❌ POOR (Significant Bottlenecks Detected)"

        return {
            "score": final_score,
            "rating": rating,
            "issues": issues,
            "recommendations": recommendations,
            "cpu": {
                "cores": cpu_count,
                "governor": governor,
                "epp": epp
            },
            "kernel": {
                "max_map_count": max_map_count,
                "split_lock_mitigate": split_lock,
                "swappiness": swappiness
            },
            "gpu": {
                "hardware": gpu_str,
                "vulkan_icds": [os.path.basename(f) for f in vulkan_icds],
                "has_radv": has_radv,
                "has_amdvlk": has_amdvlk,
                "has_nvidia": has_nvidia
            },
            "daemons": {
                "gamemode_installed": gamemode_bin,
                "gamemode_active": gamemode_active,
                "mangohud": mangohud_bin,
                "gamescope": gamescope_bin,
                "wine": wine_bin
            },
            "memory": {
                "ram_gb": total_ram_gb,
                "swap_gb": swap_gb
            }
        }

    @staticmethod
    def format_scan_report(data: Dict[str, Any]) -> str:
        """Formats the scan dictionary into a structured, readable Markdown report."""
        lines = [
            f"### 🚀 Arch Linux Gaming System Diagnostic Report\n",
            f"**Overall Gaming Score:** `{data['score']}/100` — **{data['rating']}**\n",
            "| Component | Detected Value | Status |",
            "| :--- | :--- | :--- |",
            f"| **CPU Governor** | `{data['cpu']['governor']}` | {'✅ Performance' if data['cpu']['governor'] == 'performance' else '⚠️ Suboptimal'} |",
            f"| **Energy Perf Bias** | `{data['cpu']['epp']}` | {'✅ Performance' if data['cpu']['epp'] == 'performance' else 'ℹ️ Standard'} |",
            f"| **vm.max_map_count** | `{data['kernel']['max_map_count']}` | {'✅ Optimal (>= 1048576)' if data['kernel']['max_map_count'] >= 1048576 else '❌ Critical (< 1048576)'} |",
            f"| **Split Lock Mitigate** | `{data['kernel']['split_lock_mitigate']}` | {'✅ Disabled (0)' if data['kernel']['split_lock_mitigate'] == 0 else '⚠️ Enabled (Stutter risk)'} |",
            f"| **Swappiness** | `{data['kernel']['swappiness']}` | {'✅ Low (<= 20)' if data['kernel']['swappiness'] <= 20 else 'ℹ️ Default (60)'} |",
            f"| **GPU Hardware** | `{data['gpu']['hardware']}` | ✅ Active |",
            f"| **Vulkan ICDs** | `{', '.join(data['gpu']['vulkan_icds']) if data['gpu']['vulkan_icds'] else 'None'}` | {'✅ Installed' if data['gpu']['vulkan_icds'] else '❌ Missing'} |",
            f"| **GameMode Daemon** | `{'Installed & Active' if data['daemons']['gamemode_active'] else ('Installed (Inactive)' if data['daemons']['gamemode_installed'] else 'Missing')}` | {'✅ Active' if data['daemons']['gamemode_active'] else '⚠️ Setup Needed'} |",
            f"| **MangoHud Overlay** | `{'Installed' if data['daemons']['mangohud'] else 'Missing'}` | {'✅ Ready' if data['daemons']['mangohud'] else 'ℹ️ Optional'} |",
            f"| **Gamescope Compositor** | `{'Installed' if data['daemons']['gamescope'] else 'Missing'}` | {'✅ Ready' if data['daemons']['gamescope'] else 'ℹ️ Optional'} |",
            f"| **System RAM** | `{data['memory']['ram_gb']} GB` | {'✅ Excellent' if data['memory']['ram_gb'] >= 16 else '⚠️ Limited'} |",
            ""
        ]

        if data["issues"]:
            lines.append("#### ⚠️ Identified Bottlenecks:")
            for issue in data["issues"]:
                lines.append(f"- 🔴 {issue}")
            lines.append("")

        if data["recommendations"]:
            lines.append("#### 🛠️ Recommended Optimization Commands:")
            for idx, rec in enumerate(data["recommendations"], 1):
                lines.append(f"**{idx}. {rec['action']}** ({rec['category']}):")
                lines.append(f"```bash\n{rec['command']}\n```")
            lines.append("")
            lines.append("*💡 Run `/gaming optimize` or ask the assistant to apply available user optimizations automatically.*")
        else:
            lines.append("✅ **Your PC is fully configured and optimized for Arch Linux gaming!**")

        return "\n".join(lines)

    @staticmethod
    def optimize_system(apply: bool = False) -> Tuple[bool, List[str], str]:
        """Applies or previews safe gaming optimizations."""
        actions: List[str] = []
        errors: List[str] = []

        # 1. Start user gamemoded if installed
        if shutil.which("gamemoded"):
            try:
                subprocess.run(["systemctl", "--user", "enable", "--now", "gamemoded"], capture_output=True, check=True, timeout=3)
                actions.append("Enabled & started user GameMode daemon (`systemctl --user enable --now gamemoded`).")
            except Exception as e:
                errors.append(f"Could not enable GameMode service: {e}")

        # 2. Check if running with root permissions to apply kernel sysctl tweaks directly
        is_root = (os.geteuid() == 0) if hasattr(os, "geteuid") else False
        if is_root and apply:
            try:
                subprocess.run(["sysctl", "-w", "vm.max_map_count=2147483642"], check=True, capture_output=True)
                actions.append("Set `vm.max_map_count=2147483642`.")
            except Exception as e:
                errors.append(f"Failed to set vm.max_map_count: {e}")

            if os.path.exists("/proc/sys/kernel/split_lock_mitigate"):
                try:
                    subprocess.run(["sysctl", "-w", "kernel.split_lock_mitigate=0"], check=True, capture_output=True)
                    actions.append("Disabled split lock mitigation (`kernel.split_lock_mitigate=0`).")
                except Exception as e:
                    errors.append(f"Failed to set split_lock_mitigate: {e}")

            gov_files = glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor")
            if gov_files:
                try:
                    for gf in gov_files:
                        with open(gf, "w", encoding="utf-8") as f:
                            f.write("performance\n")
                    actions.append("Set all CPU core governors to `performance`.")
                except Exception as e:
                    errors.append(f"Failed setting CPU governor: {e}")
        else:
            actions.append("To apply system-level kernel & CPU tweaks, run with sudo:")
            actions.append("`sudo sysctl -w vm.max_map_count=2147483642 kernel.split_lock_mitigate=0`")
            actions.append("`echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`")

        success = len(actions) > 0 and len(errors) == 0
        summary = "\n".join([f"- {a}" for a in actions])
        if errors:
            summary += "\n\n**Warnings:**\n" + "\n".join([f"- {e}" for e in errors])
        return success, actions, summary

    @staticmethod
    def analyze_gaming_session() -> Dict[str, Any]:
        """Analyzes active gaming processes, GPU telemetry, compositor event pacing, and micro-stutter sources."""
        active_games: List[Dict[str, Any]] = []
        high_cpu_background: List[Dict[str, Any]] = []
        xalia_detected = False

        known_game_indicators = [
            "wine64", "wineserver", "proton", "gamescope", "steamapps/common",
            "pressure-vessel", "shipping.exe", "win64", ".exe"
        ]
        excluded_procs = ["agent_terminal", "pytest", "python", "bash", "sh", "systemd", "gnome-shell"]

        try:
            for p in psutil.process_iter(['pid', 'name', 'cmdline', 'cpu_percent', 'memory_info', 'num_threads', 'status']):
                try:
                    p_name = p.info.get('name') or ''
                    cmdline_list = p.info.get('cmdline') or []
                    cmdline_str = " ".join(cmdline_list)

                    if "xalia.exe" in p_name.lower() or "xalia.exe" in cmdline_str.lower():
                        xalia_detected = True

                    is_game = False
                    if any(p_name.lower().endswith(ext) for ext in [".exe"]):
                        if not any(ign in p_name.lower() for ign in ["conhost", "explorer", "services"]):
                            is_game = True
                    elif any(ind in cmdline_str.lower() for ind in ["steamapps/common", "pressure-vessel-wrap", "gamescope"]):
                        if not any(ign in p_name.lower() for ign in ["steam", "srt-logger", "pressure-vessel"]):
                            is_game = True
                    elif "shipping" in p_name.lower() or "shipping" in cmdline_str.lower():
                        is_game = True

                    mem_info = p.info.get('memory_info')
                    rss_mb = round(mem_info.rss / (1024 * 1024), 1) if mem_info else 0.0
                    cpu_pct = p.info.get('cpu_percent') or 0.0

                    if is_game:
                        active_games.append({
                            "pid": p.info['pid'],
                            "name": p_name,
                            "cpu_percent": cpu_pct,
                            "memory_mb": rss_mb,
                            "threads": p.info.get('num_threads', 1),
                            "cmdline": cmdline_str[:200]
                        })
                    elif cpu_pct > 3.0 and not any(ign in p_name.lower() for ign in excluded_procs):
                        high_cpu_background.append({
                            "pid": p.info['pid'],
                            "name": p_name,
                            "cpu_percent": cpu_pct,
                            "memory_mb": rss_mb
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            log.warning(f"Error scanning processes: {e}")

        # 2. GPU & VRAM Telemetry
        gpu_telemetry: Dict[str, Any] = {}
        if shutil.which("nvidia-smi"):
            try:
                res = subprocess.run([
                    "nvidia-smi",
                    "--query-gpu=temperature.gpu,utilization.gpu,utilization.memory,memory.total,memory.used,pstate,clocks.current.graphics,clocks.current.memory,power.draw",
                    "--format=csv,noheader,nounits"
                ], capture_output=True, text=True, timeout=3)
                if res.returncode == 0 and res.stdout.strip():
                    parts = [pt.strip() for pt in res.stdout.strip().split(",")]
                    if len(parts) >= 9:
                        def _safe_float(val: str, default: float = 0.0) -> float:
                            try:
                                return float(val) if val not in ("[N/A]", "N/A", "") else default
                            except ValueError:
                                return default

                        def _safe_int(val: str, default: int = 0) -> int:
                            try:
                                return int(float(val)) if val not in ("[N/A]", "N/A", "") else default
                            except ValueError:
                                return default

                        v_total = _safe_float(parts[3])
                        v_used = _safe_float(parts[4])
                        gpu_telemetry = {
                            "vendor": "NVIDIA",
                            "temperature_c": _safe_int(parts[0]),
                            "gpu_util_percent": _safe_int(parts[1]),
                            "mem_ctrl_util_percent": _safe_int(parts[2]),
                            "vram_total_mb": int(v_total),
                            "vram_used_mb": int(v_used),
                            "vram_percent": round((v_used / v_total) * 100, 1) if v_total > 0 else 0,
                            "pstate": parts[5],
                            "graphics_clock_mhz": _safe_int(parts[6]),
                            "memory_clock_mhz": _safe_int(parts[7]),
                            "power_draw_w": _safe_float(parts[8])
                        }
            except Exception as e:
                log.warning(f"nvidia-smi telemetry query failed: {e}")

        # 3. CPU Governor & EPP
        governor = "unknown"
        all_performance = True
        gov_files = glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor")
        if gov_files:
            try:
                govs = set()
                for gf in gov_files:
                    with open(gf, "r", encoding="utf-8") as f:
                        govs.add(f.read().strip())
                governor = ", ".join(govs)
                all_performance = (govs == {"performance"})
            except Exception:
                pass

        epp = "unknown"
        epp_files = glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference")
        if epp_files:
            try:
                with open(epp_files[0], "r", encoding="utf-8") as f:
                    epp = f.read().strip()
            except Exception:
                pass

        # 4. Display & Wayland Compositor Analysis
        session_type = os.environ.get("XDG_SESSION_TYPE", "unknown")
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
        compositor_issues: List[str] = []
        try:
            res = subprocess.run([
                "journalctl", "--user", "-b", "-n", "80", "--no-pager", "-o", "cat"
            ], capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and res.stdout:
                j_out = res.stdout
                key_repeat_drops = len(re.findall(r"Key repeat discarded", j_out))
                if key_repeat_drops > 0:
                    compositor_issues.append(
                        f"Wayland compositor event queue lag: {key_repeat_drops} input event(s) discarded (`Key repeat discarded, Wayland compositor doesn't seem to be processing events fast enough!`)."
                    )
                if "direct scanout page flip failed" in j_out.lower():
                    compositor_issues.append("Direct scanout page flips failed in Mutter/Wayland, forcing composited frame presentation.")
                if "missed deadline" in j_out.lower():
                    compositor_issues.append("Compositor missed frame presentation deadlines, indicating presentation stutter.")
        except Exception:
            pass

        # 5. Kernel Sysctl Check
        split_lock = None
        if os.path.exists("/proc/sys/kernel/split_lock_mitigate"):
            try:
                with open("/proc/sys/kernel/split_lock_mitigate", "r", encoding="utf-8") as f:
                    split_lock = int(f.read().strip())
            except Exception:
                pass

        swappiness = 60
        if os.path.exists("/proc/sys/vm/swappiness"):
            try:
                with open("/proc/sys/vm/swappiness", "r", encoding="utf-8") as f:
                    swappiness = int(f.read().strip())
            except Exception:
                pass

        max_map_count = 65530
        if os.path.exists("/proc/sys/vm/max_map_count"):
            try:
                with open("/proc/sys/vm/max_map_count", "r", encoding="utf-8") as f:
                    max_map_count = int(f.read().strip())
            except Exception:
                pass

        # 6. Synthesize Micro-Stutter Bottlenecks
        stutter_sources: List[Dict[str, str]] = []

        if not all_performance:
            stutter_sources.append({
                "severity": "HIGH",
                "source": f"CPU Governor is '{governor}'",
                "impact": "Dynamic CPU downclocking causes frame delivery latency and thread scheduling micro-stutters.",
                "fix": "Lock all CPU cores to performance: `echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor`"
            })

        if xalia_detected:
            stutter_sources.append({
                "severity": "HIGH",
                "source": "xalia.exe (Proton Accessibility Daemon) Active",
                "impact": "xalia hooks window message pump and polls UI trees every frame, causing noticeable hitching in UE5/DX12.",
                "fix": "Disable in Steam launch options or environment: `PROTON_USE_XALIA=0 %command%`"
            })

        if compositor_issues:
            for ci in compositor_issues:
                stutter_sources.append({
                    "severity": "HIGH",
                    "source": "GNOME Wayland (Mutter) Presentation Lag",
                    "impact": ci,
                    "fix": "Ensure full-screen direct scanout: run game borderless/fullscreen, or launch via `gamescope -W 2560 -H 1440 -r 60 -- %command%` or enable VRR `gsettings set org.gnome.mutter experimental-features \"['variable-refresh-rate']\"`."
                })

        if split_lock == 1:
            stutter_sources.append({
                "severity": "MEDIUM",
                "source": "kernel.split_lock_mitigate is active (1)",
                "impact": "Kernel penalizes unaligned memory atomic operations with 10ms thread sleeps, causing severe periodic frame hitches.",
                "fix": "Disable split lock mitigation: `sudo sysctl -w kernel.split_lock_mitigate=0`"
            })

        if gpu_telemetry:
            if gpu_telemetry.get("vram_percent", 0) > 90:
                stutter_sources.append({
                    "severity": "HIGH",
                    "source": f"VRAM Near Capacity ({gpu_telemetry['vram_used_mb']}/{gpu_telemetry['vram_total_mb']} MB - {gpu_telemetry['vram_percent']}%)",
                    "impact": "Texture thrashing between VRAM and system RAM causes violent multi-frame hitches.",
                    "fix": "Reduce texture pool bounds (e.g. `r.Streaming.PoolSize` in Engine.ini) or lower Shadow/Texture quality."
                })
            if gpu_telemetry.get("pstate") not in ("P0", "P1", None):
                stutter_sources.append({
                    "severity": "MEDIUM",
                    "source": f"GPU Downclocked in Performance State {gpu_telemetry.get('pstate')}",
                    "impact": "GPU is not operating at maximum boost clock.",
                    "fix": "Ensure `gamemoderun` is active and power profile is set to maximum performance."
                })

        if high_cpu_background:
            top_hogs = ", ".join([f"{p['name']} ({p['cpu_percent']}%)" for p in high_cpu_background[:3]])
            stutter_sources.append({
                "severity": "MEDIUM",
                "source": f"Background Processes Competing for CPU: {top_hogs}",
                "impact": "Competes with game render and worker threads for CPU time slices.",
                "fix": "Close resource-heavy background applications while gaming."
            })

        return {
            "active_games": active_games,
            "gpu": gpu_telemetry,
            "cpu": {
                "governor": governor,
                "all_performance": all_performance,
                "epp": epp
            },
            "display": {
                "session_type": session_type,
                "desktop": desktop,
                "compositor_issues": compositor_issues
            },
            "kernel": {
                "split_lock_mitigate": split_lock,
                "swappiness": swappiness,
                "max_map_count": max_map_count
            },
            "xalia_detected": xalia_detected,
            "high_cpu_background": high_cpu_background,
            "stutter_sources": stutter_sources
        }

    @staticmethod
    def format_gaming_session_report(data: Dict[str, Any]) -> str:
        """Formats gaming session diagnostic data into a structured Markdown report."""
        lines = [
            "### 🔬 Live Gaming Session Forensic Report\n"
        ]

        if data["active_games"]:
            lines.append("#### 🎮 Active Game Processes:")
            lines.append("| PID | Process Name | CPU % | Memory (RSS) | Threads |")
            lines.append("| :--- | :--- | :--- | :--- | :--- |")
            for g in data["active_games"]:
                lines.append(f"| `{g['pid']}` | **{g['name']}** | `{g['cpu_percent']}%` | `{g['memory_mb']} MB` | `{g['threads']}` |")
            lines.append("")
        else:
            lines.append("ℹ️ *No active Steam/Wine/Proton game process currently detected.* Running in idle/standby analysis mode.\n")

        if data["gpu"]:
            gpu = data["gpu"]
            lines.append("#### ⚡ GPU & VRAM Telemetry:")
            lines.append("| Metric | Value | Status |")
            lines.append("| :--- | :--- | :--- |")
            lines.append(f"| **VRAM Usage** | `{gpu['vram_used_mb']} MiB / {gpu['vram_total_mb']} MiB ({gpu['vram_percent']}%)` | {'🟢 Headroom Available' if gpu['vram_percent'] < 85 else '🔴 Near Limit'} |")
            lines.append(f"| **GPU Temperature** | `{gpu['temperature_c']}°C` | {'🟢 Optimal' if gpu['temperature_c'] < 75 else '⚠️ Warm'} |")
            lines.append(f"| **GPU Utilization** | `{gpu['gpu_util_percent']}%` | Normal |")
            lines.append(f"| **Performance State** | `{gpu['pstate']}` | {'🟢 Maximum Performance (P0)' if gpu['pstate'] == 'P0' else '🟡 Throttled / Low Power'} |")
            lines.append(f"| **Clocks** | Graphics: `{gpu['graphics_clock_mhz']} MHz` / Mem: `{gpu['memory_clock_mhz']} MHz` | Normal |")
            lines.append(f"| **Power Draw** | `{gpu['power_draw_w']} W` | Normal |")
            lines.append("")

        lines.append("#### 🖥️ Display & System Configuration:")
        lines.append(f"- **Display Server:** `{data['display']['session_type'].upper()}` ({data['display']['desktop']})")
        lines.append(f"- **CPU Governor:** `{data['cpu']['governor']}` ({'✅ All Performance' if data['cpu']['all_performance'] else '⚠️ Suboptimal Governor Active'})")
        lines.append(f"- **EPP (Energy Perf Preference):** `{data['cpu']['epp']}`")
        lines.append(f"- **vm.max_map_count:** `{data['kernel']['max_map_count']}` ({'✅ Optimal' if data['kernel']['max_map_count'] >= 1048576 else '❌ Low'})")
        lines.append(f"- **Split Lock Mitigation:** `{data['kernel']['split_lock_mitigate']}` ({'✅ Disabled' if data['kernel']['split_lock_mitigate'] == 0 else '⚠️ Enabled - Stutter Risk'})")
        lines.append("")

        if data["stutter_sources"]:
            lines.append("#### 🚨 Identified Sources of Micro-Stuttering & Frame Drops:")
            for idx, src in enumerate(data["stutter_sources"], 1):
                sev_icon = "🔴" if src["severity"] == "HIGH" else "🟡"
                lines.append(f"**{idx}. {sev_icon} [{src['severity']}] {src['source']}**")
                lines.append(f"- **Impact:** {src['impact']}")
                lines.append(f"- **Remediation:** {src['fix']}\n")
        else:
            lines.append("✅ **No active micro-stutter bottlenecks detected in the current session!**\n")

        return "\n".join(lines)

class GamingCompanion:
    """Specialized AI persona for gaming info, Arch Linux gaming optimization, and Steam integration."""
    def __init__(self, console: Console, model=None):
        self.console = console
        self.model = model  # This is the genai.Client
        self.steam = SteamClient()
        self.grounding_tool = types.Tool(google_search=types.GoogleSearch())
        self.gaming_history: List[Dict[str, str]] = []

    def _get_system_prompt(self) -> str:
        return (
            "ROLE: You are the 'Gaming Intelligence Unit' for Arch Linux.\n"
            "EXPERTISE: You are an authority on Linux gaming, Proton/Wine compatibility, MangoHud, GameMode, "
            "game mechanics, patch notes, hardware performance, and Steam configurations.\n"
            "KNOWLEDGE: You utilize 'Google Search Grounding' for real-time patch notes, release schedules, and community-verified strategies.\n"
            "MISSION: Provide precise, data-driven gaming intelligence. Base claims on verified search data to avoid hallucinations.\n"
            "ARCH LINUX TIPS: Recommend launch options (e.g. `gamemoderun %command%`, `MANGOHUD=1 %command%`) and Proton versions when asked about Linux gaming.\n"
            "OUTPUT: Use Markdown tables for stats and clear hierarchical lists for walkthroughs, guides, and requirements."
        )

    async def show_steam_library(self, limit: int = 15):
        """Fetches and displays the user's Steam library."""
        with self.console.status(f"[bold {Config.COLOR_GAMING}]Fetching Steam library...", spinner=GAMING_SPINNER_NAME):
            summary = await self.steam.format_owned_games_summary(limit=limit)
        
        self.console.print(Panel(
            Markdown(summary),
            title="[bold gold1]🎮 STEAM LIBRARY[/bold gold1]",
            border_style=Config.COLOR_GAMING,
            box=BORDERLESS_BOX,
            padding=(1, 2),
            expand=True
        ))

    async def show_game_compatibility(self, appid_or_name: Union[int, str]):
        """Fetches and displays compatibility and ProtonDB status for a specific game."""
        with self.console.status(f"[bold {Config.COLOR_GAMING}]Querying ProtonDB & Steam Store...", spinner=GAMING_SPINNER_NAME):
            report = await self.steam.format_game_report(appid_or_name)
            
        self.console.print(Panel(
            Markdown(report),
            title=f"[bold gold1]🕹️ GAME COMPATIBILITY ({appid_or_name})[/bold gold1]",
            border_style=Config.COLOR_GAMING,
            box=BORDERLESS_BOX,
            padding=(1, 2),
            expand=True
        ))

    async def show_steam_status(self):
        """Displays diagnostic status of Steam, Proton runtimes, and Arch Linux gaming packages."""
        summary = self.steam.format_steam_status()
        self.console.print(Panel(
            Markdown(summary),
            title="[bold gold1]🕹️ ARCH LINUX GAMING & STEAM STATUS[/bold gold1]",
            border_style=Config.COLOR_GAMING,
            box=BORDERLESS_BOX,
            padding=(1, 2),
            expand=True
        ))

    async def show_installed_games(self, limit: int = 25):
        """Displays locally installed Steam games with disk usage."""
        summary = self.steam.format_installed_games_summary(limit=limit)
        self.console.print(Panel(
            Markdown(summary),
            title="[bold gold1]💾 INSTALLED STEAM GAMES[/bold gold1]",
            border_style=Config.COLOR_GAMING,
            box=BORDERLESS_BOX,
            padding=(1, 2),
            expand=True
        ))

    async def search_store_games(self, query: str, limit: int = 6):
        """Searches the Steam Store and displays matching games with prices."""
        with self.console.status(f"[bold {Config.COLOR_GAMING}]Searching Steam Store for '{query}'...", spinner=GAMING_SPINNER_NAME):
            summary = await self.steam.format_store_search(query, limit=limit)
        self.console.print(Panel(
            Markdown(summary),
            title=f"[bold gold1]🔍 STEAM STORE SEARCH: '{query}'[/bold gold1]",
            border_style=Config.COLOR_GAMING,
            box=BORDERLESS_BOX,
            padding=(1, 2),
            expand=True
        ))

    async def show_game_news(self, appid_or_name: Union[int, str], count: int = 3):
        """Fetches and displays latest news and patch notes for a game."""
        with self.console.status(f"[bold {Config.COLOR_GAMING}]Fetching Steam patch notes & news...", spinner=GAMING_SPINNER_NAME):
            summary = await self.steam.format_game_news(appid_or_name, count=count)
        self.console.print(Panel(
            Markdown(summary),
            title=f"[bold gold1]📰 STEAM PATCH NOTES & NEWS: {appid_or_name}[/bold gold1]",
            border_style=Config.COLOR_GAMING,
            box=BORDERLESS_BOX,
            padding=(1, 2),
            expand=True
        ))

    def launch_game(self, appid_or_name: str):
        """Launches a game via the Steam client protocol."""
        success, msg = self.steam.launch_game(appid_or_name)
        if success:
            self.console.print(f"[bold green]🚀 {escape(msg)}[/bold green]")
        else:
            self.console.print(f"[bold red]❌ {escape(msg)}[/bold red]")

    async def scan_system(self):
        """Scans the local PC for gaming performance, kernel tweaks, and Arch gaming packages."""
        with self.console.status(f"[bold {Config.COLOR_GAMING}]Scanning PC hardware & gaming parameters...", spinner=GAMING_SPINNER_NAME):
            data = GamingOptimizer.scan_system()
            report = GamingOptimizer.format_scan_report(data)
        self.console.print(Panel(
            Markdown(report),
            title="[bold gold1]🚀 ARCH LINUX GAMING OPTIMIZATION SCAN[/bold gold1]",
            border_style=Config.COLOR_GAMING,
            box=BORDERLESS_BOX,
            padding=(1, 2),
            expand=True
        ))
        return data

    async def optimize_system(self, apply: bool = True):
        """Applies or previews system optimizations for gaming."""
        with self.console.status(f"[bold {Config.COLOR_GAMING}]Analyzing and applying gaming optimizations...", spinner=GAMING_SPINNER_NAME):
            success, actions, summary = GamingOptimizer.optimize_system(apply=apply)

        self.console.print(Panel(
            Markdown(f"### ⚡ Gaming Optimization Status\n\n{summary}"),
            title="[bold gold1]⚡ SYSTEM OPTIMIZER[/bold gold1]",
            border_style="green" if success else "yellow",
            box=BORDERLESS_BOX,
            padding=(1, 2),
            expand=True
        ))
        return success

    async def refresh_steam_cache(self):
        """Clears the local SQLite Steam cache."""
        success = await self.steam.clear_cache()
        if success:
            self.console.print("[bold green]⚡ Steam cache cleared successfully.[/bold green]")
        else:
            self.console.print("[bold red]Failed to clear Steam cache.[/bold red]")

    async def query(self, prompt_text: str, chat_history: List[Dict[str, str]] = None, analyzer=None):
        """Processes gaming queries with native grounding, system diagnostics, and streaming intelligence."""
        if not prompt_text.strip(): return

        clean_prompt = prompt_text.strip().lower()

        # Check for direct scan or optimize triggers
        if any(term in clean_prompt for term in ["scan pc", "scan system", "check gaming", "benchmark", "diagnose gaming"]):
            await self.scan_system()
            return

        if any(term in clean_prompt for term in ["optimize pc", "optimize system", "boost fps", "gaming optimize", "speed up games"]):
            await self.optimize_system(apply=True)
            return

        if any(term in clean_prompt for term in ["analyze session", "gaming session", "micro stutter", "stuttering", "current session"]):
            with self.console.status(f"[bold {Config.COLOR_GAMING}]Analyzing live gaming session & micro-stutter sources...", spinner=GAMING_SPINNER_NAME):
                data = GamingOptimizer.analyze_gaming_session()
                report = GamingOptimizer.format_gaming_session_report(data)
            self.console.print(Panel(
                Markdown(report),
                title="[bold gold1]🔬 LIVE GAMING FORENSIC ANALYSIS[/bold gold1]",
                border_style=Config.COLOR_GAMING,
                box=BORDERLESS_BOX,
                padding=(1, 2),
                expand=True
            ))
            if analyzer:
                await analyzer.log_chat(prompt_text, report)
            return report

        # Check for direct library or appid command patterns
        if clean_prompt in ["/steam", "steam", "library", "my games", "show games"]:
            await self.show_steam_library()
            return

        appid_match = re.search(r"\b(?:appid|app)\s*[:=]?\s*(\d{3,8})\b", clean_prompt)
        if appid_match:
            appid = int(appid_match.group(1))
            await self.show_game_compatibility(appid)

        history_context = ""
        if chat_history:
            history_context = "PAST CONVERSATION:\n" + "\n".join([f"User: {e['user']}\nAssistant: {e['agent']}" for e in chat_history]) + "\n\n"

        system_prompt = self._get_system_prompt() + "\n" + history_context

        try:
            full_text = ""
            grounding_detected = False

            gaming_spinner = Spinner(
                GAMING_SPINNER_NAME,
                text=" [bold gold1]Consulting ProtonDB, Steam & Linux Gaming Intelligence...[/bold gold1]",
                style="bold gold1"
            )

            # Live display with immediate animated arcade gaming spinner
            with Live(
                Panel(
                    gaming_spinner,
                    title="[bold gold1]🕹️ GAMING COMPANION[/bold gold1]",
                    subtitle="[dim]⚡ AI is thinking...[/dim]",
                    border_style=Config.COLOR_GAMING,
                    box=BORDERLESS_BOX,
                    padding=(1, 2),
                    expand=True
                ),
                console=self.console,
                refresh_per_second=12
            ) as live:
                try:
                    stream = await self.model.aio.models.generate_content_stream(
                        model=Config.MODEL_NAME,
                        contents=system_prompt + "\nUser: " + prompt_text,
                        config=types.GenerateContentConfig(
                            tools=[self.grounding_tool]
                        )
                    )
                    async for chunk in stream:
                        chunk_text = extract_full_model_response(chunk)
                        if chunk_text:
                            full_text += chunk_text
                            live.update(Panel(
                                Markdown(full_text),
                                title="[bold gold1]🕹️ GAMING COMPANION[/bold gold1]",
                                border_style=Config.COLOR_GAMING,
                                subtitle="[dim]⚡ Streaming response...[/dim]",
                                box=BORDERLESS_BOX,
                                padding=(1, 2),
                                expand=True
                            ))
                        if getattr(chunk, "candidates", None) and len(chunk.candidates) > 0 and getattr(chunk.candidates[0], "grounding_metadata", None):
                            grounding_detected = True
                except Exception:
                    # Fallback to standard request with animated spinner
                    gaming_spinner.update(text=" [bold gold1]Re-routing query to Gaming Knowledge Base...[/bold gold1]")
                    live.update(Panel(
                        gaming_spinner,
                        title="[bold gold1]🕹️ GAMING COMPANION[/bold gold1]",
                        subtitle="[dim]⚡ Retrieving response...[/dim]",
                        border_style=Config.COLOR_GAMING,
                        box=BORDERLESS_BOX,
                        padding=(1, 2),
                        expand=True
                    ))
                    response = await self.model.aio.models.generate_content(
                        model=Config.MODEL_NAME,
                        contents=system_prompt + "\nUser: " + prompt_text,
                        config=types.GenerateContentConfig(
                            tools=[self.grounding_tool]
                        )
                    )
                    full_text = extract_full_model_response(response, include_function_calls=False)
                    if not full_text:
                        full_text = "The game database is unresponsive."
                    if getattr(response, "candidates", None) and len(response.candidates) > 0 and getattr(response.candidates[0], "grounding_metadata", None):
                        grounding_detected = True

                final_subtitle = f"[dim]Mode: Collector Guide & Arch Gaming{' | [bold cyan]ᗧ Grounded[/bold cyan]' if grounding_detected else ''}[/dim]"
                live.update(Panel(
                    Markdown(full_text if full_text else "No response received from gaming companion."),
                    title="[bold gold1]🕹️ GAMING COMPANION[/bold gold1]",
                    border_style=Config.COLOR_GAMING,
                    subtitle=final_subtitle,
                    box=BORDERLESS_BOX,
                    padding=(1, 2),
                    expand=True
                ))

            if analyzer and full_text:
                await analyzer.log_chat(prompt_text, full_text)

            return full_text

        except Exception as e:
            log.error(f"Gaming Mode Error: {e}")
            self.console.print(f"[bold red]Critical Gaming Error:[/bold red] {escape(str(e))}")
            return None

    async def aclose(self):
        """Closes internal SteamClient session and cleans up resources."""
        if hasattr(self, "steam") and self.steam:
            await self.steam.aclose()

