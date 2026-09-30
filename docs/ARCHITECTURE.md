# Terminal AI Architecture

## Overview
Terminal AI is a modular, high-performance CLI autonomous agent engineered specifically for Arch Linux workstations.

## Core Subsystems
- **agent_terminal.py**: Asynchronous interaction engine powered by `prompt_toolkit` and Google GenAI.
- **mcp_manager.py**: Dynamic client connection manager implementing the Model Context Protocol (MCP).
- **sandbox.py**: Docker-isolated execution boundary preventing uncontained system state modification.
- **troubleshooter.py & analyzer.py**: System diagnostic and static AST analysis pipelines.
- **gaming.py & steam_utils.py**: Proton compatibility, Vulkan/NVML telemetry, MangoHud and GameMode orchestration.
