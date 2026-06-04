from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
WEB_DIR = PROJECT_ROOT / "web"
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "bot.db"
ENV_PATH = PROJECT_ROOT / ".env"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_dotenv() -> None:
    from dotenv import load_dotenv as _load

    _load(ENV_PATH)


def get_env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)
