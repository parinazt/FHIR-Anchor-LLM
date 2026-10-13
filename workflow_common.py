"""Workflow result contract, JSON saving, and standalone entry points."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class WorkflowResult:
    bundle: dict | None
    valid: bool
    attempts: int
    agent_attempts: dict = field(default_factory=dict)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_cli(mode: str) -> None:
    from main import main
    main(["--mode", mode, "--num-of-execution", "1", *sys.argv[1:]])
