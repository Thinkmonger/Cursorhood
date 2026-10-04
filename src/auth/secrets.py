"""OS keyring for dashboard API keys. Process env still wins for CI and Cloud."""

from __future__ import annotations

import os

import keyring

from src.paths import ENV_PATH

SERVICE_NAME = "robinhood-agentic-bot"
ACCOUNTS = {
    "CURSOR_API_KEY": "cursor-api-key",
    "MASSIVE_API_KEY": "massive-api-key",
}


def _account(name: str) -> str:
    account = ACCOUNTS.get(name)
    if not account:
        raise ValueError(f"Unknown secret {name}")
    return account


def _read_env_file(name: str) -> str | None:
    if not ENV_PATH.exists():
        return None
    prefix = f"{name}="
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            stored = line.split("=", 1)[1].strip()
            return stored or None
    return None


def _write_env_file(name: str, value: str) -> None:
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    prefix = f"{name}="
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{prefix}{value}"
            updated = True
            break
    if not updated:
        lines.append(f"{prefix}{value}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _strip_env_file(name: str) -> None:
    if not ENV_PATH.exists():
        return
    prefix = f"{name}="
    lines = [
        line
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines()
        if not line.startswith(prefix)
    ]
    ENV_PATH.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def get_secret(name: str) -> str | None:
    env = (os.environ.get(name) or "").strip()
    if env:
        return env
    try:
        stored = keyring.get_password(SERVICE_NAME, _account(name))
        if stored and stored.strip():
            return stored.strip()
    except Exception:
        pass
    file_val = _read_env_file(name)
    if file_val:
        try:
            keyring.set_password(SERVICE_NAME, _account(name), file_val)
        except Exception:
            pass
        return file_val
    return None


def set_secret(name: str, value: str) -> None:
    cleaned = value.strip()
    os.environ[name] = cleaned
    try:
        keyring.set_password(SERVICE_NAME, _account(name), cleaned)
        _strip_env_file(name)
    except Exception:
        _write_env_file(name, cleaned)
