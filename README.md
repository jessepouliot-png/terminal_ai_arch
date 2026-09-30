# ᗧ Arch AI Terminal

> **Production-grade AI Terminal & Autonomous System Assistant for Arch Linux**  
> Powered by Google Gemini 3.8 Flash, Imagen, Docker Container Sandboxing, ProtonDB, and Steam Web API.

---

## 🚀 Overview

**Arch AI Terminal** (`arch-terminal`) is an intelligent, multi-mode terminal interface designed specifically for Arch Linux power users and gamers. It seamlessly bridges native host execution, sandboxed experiment isolation, autonomous tool calling, Linux gaming diagnostics, and AI image generation inside a high-performance borderless TUI.

---

## ✨ Features

- 🧠 **Autonomous AI Agent (`/chat`)**: Multi-turn reasoning with native Google Search Grounding and direct system function calling (file read/write/patch, process inspection, host diagnostics, sandboxed execution, and gaming optimization).
- 🎮 **PC Gaming Hardware & Vulkan Scanner (`/gaming scan`, `/gaming optimize`)**: Deep audit of Linux gaming readiness (CPU scaling governor & EPP, `vm.max_map_count`, `split_lock_mitigate`, `swappiness`, GPU drivers, 32-bit/64-bit Vulkan ICDs, GameMode daemon, MangoHud, Gamescope). Computes a 0-100 score and applies one-click sysctl and governor optimizations.
- 🩺 **Arch System Health Audit (`/audit`)**: One-command diagnostic of failed systemd units, pacman cache size, orphan packages, RAM, and disk utilization with actionable cleanup suggestions.
- 💬 **Natural Language Shell Synthesis (`# <query>`)**: Type `# <what you want to do>` from any prompt (e.g. `# find files larger than 100MB`) to synthesize the exact bash command via Gemini, complete with a safe interactive execution prompt.
- 💡 **Fish-Style Ghost Autosuggestions**: Inline history auto-completion as you type, accepted with the right arrow key.
- ⏱️ **Execution Telemetry in Status Bar**: Real-time tracking of command execution latency and exit codes in the bottom toolbar.
- 🛡️ **Predictive Risk Classification (`/cli`)**: Intercepts potentially destructive commands (e.g. `rm -rf`, disk operations, sudo hazards) and prompts for confirmation before execution.
- 📦 **Fused Stateful Docker Sandbox (`/sandbox`)**: Spawns an isolated Arch Linux container with strict memory (512MB), CPU (50%), and network caps. Features persistent `cd` and `export` state tracking, live container prompt paths, raw subshell drop-ins (`/sandbox shell` or `shell`), and host escape (`!<cmd>`).
- 🎨 **AI Image Generation (`/image`)**: Generates high-resolution images via Gemini Imagen models, automatically saves them to `~/.agent_terminal/images/`, supports inline `chafa` terminal previews, and opens them in default image viewers.
- 📋 **Unrestricted Terminal Copying**: Seamless, native terminal selection and copying without mouse hijacking, buttons, or prompt spacing issues.
- ⚡ **Auto-Troubleshooting**: Detects non-zero exit codes in native CLI mode and autonomously suggests actionable Arch Linux remediation commands.
- 🕹️ **Linux Gaming & Steam Companion**: Live ProtonDB integration, Steam library audits, Arch package verification, and launch configuration tuning.
- 📊 **SQLite Persistence & Telemetry**: WAL-mode SQLite database tracking latency, token usage, tool metrics, and command logs.

---

## 📋 Commands & Keybindings

### Mode Switching & Quick Shortcuts

| Hotkey | Command | Mode | Description |
| :--- | :--- | :--- | :--- |
| **`F1`** | `/cli` | **CLI** | Native host terminal with risk classification and auto-troubleshooting. |
| **`F2`** | `/chat` | **CHAT** | AI Assistant with Google Search Grounding and Autonomous System Tools. |
| **`F3`** | `/gaming` | **GAMING** | Linux Gaming Companion, ProtonDB ratings, and Steam library stats. |
| **`F4`** | `/sandbox` | **SANDBOX** | Fused Stateful Sandbox with persistent `cd`/`export` and host escape. |
| **`F5`** | `/image` | **IMAGE** | Generates and saves AI images from text prompts. |

### Command Reference

| Command | Usage | Description |
| :--- | :--- | :--- |
| `/gaming scan` | `/gaming scan` | Deep audit of CPU governors, sysctl, Vulkan ICDs, and gaming score. |
| `/gaming optimize` | `/gaming optimize` | Autonomously tunes kernel sysctls, GameMode, and CPU performance. |
| `/audit` | `/audit` | Arch health audit: failed systemd units, orphan packages, pacman cache. |
| `# <query>` | `# extract tar.gz to /tmp` | Synthesizes natural language into executable bash with confirmation prompt. |
| `/sandbox` | `/sandbox` | Enters the stateful sandbox mode (syncs `./` to `/workspace`). |
| `/sandbox <cmd>` | `/sandbox python test.py` | One-shot execution of a sandbox command from any mode. |
| `/sandbox shell` | `/sandbox shell` or `shell` | Drops into an interactive bash/sh subshell inside the container. |
| `/sandbox status` | `/sandbox status` | Displays container specs, limits, mounts, and runtime status. |
| `/sandbox stop` | `/sandbox stop` | Gracefully stops the active sandbox container. |
| `/sandbox reset` | `/sandbox reset` | Restarts the sandbox container with a fresh state. |
| `!<cmd>` | `!git status` *(in Sandbox)* | **Host Escape**: Runs `<cmd>` on the host machine without leaving sandbox mode. |
| `/image <prompt>` | `/image cyberpunk arch logo` | One-shot image generation and save. |
| `/steam` | `/steam` | Displays Steam library overview and playtime rankings. |
| `/steam <game>` | `/steam baldur` or `/steam 1086940` | ProtonDB compatibility, Arch launch options, and local install status. |
| `/steam installed` | `/steam installed` | Lists locally installed Steam games with disk usage (GB). |
| `/steam status` | `/steam status` | Steam process status, installed Proton versions, and Arch gaming packages. |
| `/steam search <query>` | `/steam search witcher` | Live search Steam Store for games, pricing, and AppIDs. |
| `/steam launch <game>` | `/steam launch baldur` | Launches game directly into Steam client. |
| `/steam news <game>` | `/steam news baldur` | Fetches latest official patch notes and announcements. |
| `/steam refresh` | `/steam refresh` | Clears local SQLite cache for fresh library and Proton data. |
| `/stats` | `/stats` | Displays AI latency metrics, command history, and tool telemetry. |
| `/index` | `/index` | Crawls and indexes current directory into SQLite FTS5 local knowledge base. |
| `/clear` | `/clear` | Clears console and redraws header. |
| `/help` | `/help` | Displays help cheat sheet. |
| `exit` | `exit` | Closes terminal session and gracefully cleans up containers. |

---

## 🛠️ Installation & Setup

### 1. Prerequisites (Arch Linux)

```bash
# Core system tools and optional gaming/image utilities
sudo pacman -S python docker gamemode mangohud chafa xdg-utils
sudo systemctl enable --now docker.service
sudo usermod -aG docker $USER
```

### 2. Clone & Virtual Environment

```bash
git clone https://github.com/your-username/arch-ai-terminal.git
cd arch-ai-terminal

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment Configuration (`.env`)

Copy the template and fill in your API keys:

```bash
cp .env.example .env
```

Edit `.env`:
```env
GEMINI_API_KEY=your_gemini_api_key_here
MODEL_NAME=gemini-3.8-flash
IMAGE_MODEL=gemini-2.5-flash-image
STEAM_API_KEY=your_steam_web_api_key_here
STEAM_ID=your_steam_64_id_here
```

### 4. Global Installation (Optional)

Install in editable mode to use the `arch-terminal` binary from anywhere:

```bash
pip install -e .
arch-terminal
```

Or build with Arch Linux `PKGBUILD`:
```bash
makepkg -si
```

---

## 🏗️ Architecture

```
arch-ai-terminal/
├── agent_terminal.py     # Main TUI engine, prompt-toolkit loop & mode dispatch
├── analyzer.py           # Behavioral risk classification & SQLite FTS5 local index
├── config.py             # Configuration loader, theme constants & borderless box style
├── gaming.py             # Linux gaming companion & ProtonDB diagnostic coordinator
├── image_generator.py    # AI image generation, terminal preview & viewer integration
├── logger_utils.py       # JSON structured logging & 10MB rotating file handler
├── sandbox.py            # Docker container manager with CPU/RAM/security caps
├── searcher.py           # Google search engine scraper & AI web synthesis
├── steam_utils.py        # Steam Web API client, HTML sanitizer & 24h SQLite cache
├── tools.py              # Autonomous Gemini function schemas & host diagnostic tools
├── troubleshooter.py     # Command failure root cause & AI fix generator
└── tests/                # Comprehensive unit test suite (pytest)
```

---

## 🧪 Running Tests

```bash
.venv/bin/pytest
```

---

## 📄 License

Licensed under the [MIT License](LICENSE).
