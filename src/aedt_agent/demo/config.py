from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PlannerConfig:
    mode: str = "deterministic"
    provider: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(frozen=True)
class ExecutionConfig:
    default_adapter: str = "fake"
    run_dir: str = "benchmarks/runs/stage_c1_demo_latest"


@dataclass(frozen=True)
class DemoConfig:
    planner: PlannerConfig
    server: ServerConfig
    execution: ExecutionConfig


def load_demo_config(
    *,
    example_path: Path = Path("config/demo_config.example.json"),
    local_path: Path = Path("config/demo_config.local.json"),
) -> DemoConfig:
    data = _read_json_if_exists(example_path)
    local = _read_json_if_exists(local_path)
    merged = _deep_merge(data, local)
    return DemoConfig(
        planner=PlannerConfig(**_section(merged, "planner")),
        server=ServerConfig(**_section(merged, "server")),
        execution=ExecutionConfig(**_section(merged, "execution")),
    )


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be a JSON object")
    return dict(value)
