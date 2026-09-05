from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def load_dotenv(path: str | Path | None = None) -> None:
    """Minimal ``.env`` loader that populates missing environment variables."""
    dotenv_path = Path(path) if path is not None else Path(os.getcwd()) / ".env"
    if not dotenv_path.is_file():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_string(name: str, default: str, *, dotenv_path: str | Path | None = None) -> str:
    if dotenv_path is not None:
        load_dotenv(dotenv_path)
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def env_float(name: str, default: float, *, dotenv_path: str | Path | None = None) -> float:
    if dotenv_path is not None:
        load_dotenv(dotenv_path)
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(name: str, default: int, *, dotenv_path: str | Path | None = None) -> int:
    if dotenv_path is not None:
        load_dotenv(dotenv_path)
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default