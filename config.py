"""Project configuration: process environment overrides the project .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
MODES = ("single-agent", "multiagent")


@dataclass(frozen=True)
class Settings:
    openai_token: str = field(default="", repr=False)
    openrouter_token: str = field(default="", repr=False)
    num_of_execution: int = 5
    mode: str = "single-agent"
    max_attempts: int = 3
    development: bool = False

    def __post_init__(self):
        if self.max_attempts < 1:
            raise ValueError("MAX_ATTEMPTS must be a positive integer")
        if self.num_of_execution < 1:
            raise ValueError("NUM_OF_EXECUTION must be a positive integer")
        if self.mode not in MODES:
            raise ValueError("MODE must be single-agent or multiagent")

    def api_key(self, provider: str) -> str:
        if provider not in ("openai", "openrouter"):
            raise ValueError("Provider must be openai or openrouter")
        key = self.openai_token if provider == "openai" else self.openrouter_token
        if not key:
            name = "OPENAI_API_KEY" if provider == "openai" else "OPENROUTER_API_KEY"
            raise ValueError(f"Set {name} in .env or the process environment")
        return key


def load_settings(env_file: Optional[Path] = None) -> Settings:
    path = ROOT / ".env" if env_file is None else Path(env_file)
    if env_file is not None and not path.is_file():
        raise ValueError(f"Configuration file does not exist: {path}")
    # Do not mutate os.environ or interpolate token contents.
    values = {**dotenv_values(path, interpolate=False), **os.environ}
    try:
        count = int(values.get("NUM_OF_EXECUTION", "5"))
    except (TypeError, ValueError):
        raise ValueError("NUM_OF_EXECUTION must be a positive integer") from None
    try:
        max_attempts = int(values.get("MAX_ATTEMPTS", "3"))
    except (TypeError, ValueError):
        raise ValueError("MAX_ATTEMPTS must be a positive integer") from None
    development = (values.get("DEVELOPMENT", "false") or "").strip().lower()
    if development not in ("true", "false"):
        raise ValueError("DEVELOPMENT must be true or false")
    return Settings(
        development=development == "true",
        max_attempts=max_attempts,
        openai_token=(values.get("OPENAI_API_KEY") or "").strip(),
        openrouter_token=(values.get("OPENROUTER_API_KEY") or "").strip(),
        num_of_execution=count,
        mode=(values.get("MODE") or "single-agent").strip(),
    )
