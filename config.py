import os
import logging
from dotenv import load_dotenv
from typing import Optional
from rich.box import Box

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ENV = os.path.join(PROJECT_DIR, ".env")
USER_ENV = os.path.expanduser("~/.agent_terminal/.env")

def load_environment(override: bool = True):
    """Loads environment variables from prioritized .env files."""
    # 1. Project directory .env (/home/jpx/Project/terminal/.env)
    if os.path.exists(PROJECT_ENV):
        load_dotenv(PROJECT_ENV, override=override)
    # 2. User directory ~/.agent_terminal/.env
    if os.path.exists(USER_ENV):
        load_dotenv(USER_ENV, override=override)
    # 3. Current working directory .env
    load_dotenv(override=override)

# Load environment on module import
load_environment(override=True)

# Borderless box style that omits all frame lines while preserving titles, subtitles, and padding
BORDERLESS_BOX = Box(
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
    "    \n"
)

class ConfigMeta(type):
    """Metaclass allowing dynamic retrieval and update of API keys and models."""
    _custom_gemini_key: Optional[str] = None
    _custom_model_name: Optional[str] = None

    @property
    def GEMINI_API_KEY(cls) -> Optional[str]:
        if cls._custom_gemini_key is not None:
            return cls._custom_gemini_key
        return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    @GEMINI_API_KEY.setter
    def GEMINI_API_KEY(cls, value: Optional[str]):
        cls._custom_gemini_key = value
        if value is not None:
            os.environ["GEMINI_API_KEY"] = value

    @property
    def MODEL_NAME(cls) -> str:
        if cls._custom_model_name is not None:
            return cls._custom_model_name
        return os.getenv("MODEL_NAME", "gemini-3.8-flash")

    @MODEL_NAME.setter
    def MODEL_NAME(cls, value: str):
        cls._custom_model_name = value
        if value:
            os.environ["MODEL_NAME"] = value

class Config(metaclass=ConfigMeta):
    """Production configuration for the Arch AI Terminal TUI."""
    
    # Core API Keys & Models
    STEAM_API_KEY = os.getenv("STEAM_API_KEY")
    STEAM_ID = os.getenv("STEAM_ID")
    IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gemini-2.5-flash-image")
    
    # UI Themes
    COLOR_ARCH = "cyan"
    COLOR_PACMAN = "yellow"
    COLOR_GHOST = "magenta"
    COLOR_GAMING = "gold1"
    COLOR_IMAGE = "orchid"
    
    # Data Paths
    APP_DIR = os.path.expanduser("~/.agent_terminal")
    DB_PATH = os.path.join(APP_DIR, "terminal_intelligence.db")
    LOG_FILE = os.path.join(APP_DIR, "agent_terminal.log")
    IMAGE_DIR = os.path.join(APP_DIR, "images")

    # Sandbox Configuration
    SANDBOX_ENGINE = os.getenv("SANDBOX_ENGINE", "auto")
    SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "archlinux:latest")
    SANDBOX_MEMORY = os.getenv("SANDBOX_MEMORY", "512m")
    SANDBOX_CPU_QUOTA = int(os.getenv("SANDBOX_CPU_QUOTA", "50000"))
    SANDBOX_PIDS_LIMIT = int(os.getenv("SANDBOX_PIDS_LIMIT", "256"))
    SANDBOX_NETWORK = os.getenv("SANDBOX_NETWORK", "bridge")
    SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "30"))
    SANDBOX_READ_ONLY = os.getenv("SANDBOX_READ_ONLY", "false").lower() in ("true", "1", "yes")

    @classmethod
    def mask_key(cls, key: Optional[str]) -> str:
        """Masks an API key for safe display in UI."""
        if not key:
            return "None (not configured)"
        if len(key) <= 10:
            return key[:2] + "..." + key[-2:]
        return key[:8] + "..." + key[-4:]

    @classmethod
    def get_api_key_info(cls) -> dict:
        """Returns metadata about the currently active API key and its source."""
        key = cls.GEMINI_API_KEY
        source = "Not configured"
        if os.environ.get("GEMINI_API_KEY"):
            source = "Environment variable (GEMINI_API_KEY)"
        elif os.environ.get("GOOGLE_API_KEY"):
            source = "Environment variable (GOOGLE_API_KEY)"
        elif os.path.exists(PROJECT_ENV):
            source = f"Project file ({PROJECT_ENV})"
        elif os.path.exists(USER_ENV):
            source = f"User file ({USER_ENV})"

        return {
            "key": key,
            "masked": cls.mask_key(key),
            "length": len(key) if key else 0,
            "source": source,
            "is_set": bool(key)
        }

    @classmethod
    def save_api_key(cls, new_key: str, persist_to_file: bool = True) -> bool:
        """Sets the new API key in memory and optionally writes it to .env files."""
        new_key = new_key.strip()
        cls.GEMINI_API_KEY = new_key
        os.environ["GEMINI_API_KEY"] = new_key

        if persist_to_file:
            cls._write_key_to_env_file(PROJECT_ENV, new_key)
            cls._write_key_to_env_file(USER_ENV, new_key)
        return True

    @classmethod
    def _write_key_to_env_file(cls, filepath: str, new_key: str):
        """Updates or appends GEMINI_API_KEY in the specified .env file."""
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            lines = []
            found = False
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip().startswith("GEMINI_API_KEY="):
                            lines.append(f"GEMINI_API_KEY={new_key}\n")
                            found = True
                        else:
                            lines.append(line)
            if not found:
                lines.append(f"GEMINI_API_KEY={new_key}\n")
            with open(filepath, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception as e:
            logging.getLogger("config").warning(f"Failed to write API key to {filepath}: {e}")

    @classmethod
    def validate(cls):
        """Validates that essential configuration is present and creates directories."""
        if not cls.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is required. Please set it in your .env file.")
        cls.ensure_directories()

    @classmethod
    def ensure_directories(cls):
        """Ensures app and media directories exist."""
        os.makedirs(cls.APP_DIR, exist_ok=True)
        os.makedirs(cls.IMAGE_DIR, exist_ok=True)

# Initialize paths on module load
Config.ensure_directories()


