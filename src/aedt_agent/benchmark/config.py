from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aedt_agent.benchmark.generator import CodeGenerator, DefaultCodeGenerator, FileGenerator, OpenAIGenerator


@dataclass(frozen=True)
class OpenAIConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: int = 60
    temperature: float = 0.0


@dataclass(frozen=True)
class FileGeneratorConfig:
    base_dir: str = "."


@dataclass(frozen=True)
class GeneratorConfig:
    backend: str = ""
    openai: OpenAIConfig = OpenAIConfig()
    file: FileGeneratorConfig = FileGeneratorConfig()


@dataclass(frozen=True)
class PathConfig:
    tasks: str
    generated: str
    nodes: str
    db: str
    report: str


@dataclass(frozen=True)
class BenchmarkConfig:
    generator: GeneratorConfig
    paths: PathConfig
    groups: list[str]

    def build_generator(self) -> CodeGenerator:
        backend = self.generator.backend.lower()
        if backend == "openai":
            return OpenAIGenerator(
                base_url=self.generator.openai.base_url,
                api_key=self.generator.openai.api_key,
                model=self.generator.openai.model,
                timeout=self.generator.openai.timeout,
                temperature=self.generator.openai.temperature,
            )
        if backend == "file":
            return FileGenerator(Path(self.generator.file.base_dir))
        return DefaultCodeGenerator()


def load_benchmark_config(path: Path) -> BenchmarkConfig:
    base_path = Path(path)
    data = _load_json(base_path)
    local_path = base_path.with_name(f"{base_path.stem}.local{base_path.suffix}")
    if local_path.exists():
        data = _deep_merge(data, _load_json(local_path))

    generator_data = data.get("generator", {})
    openai_data = generator_data.get("openai", {})
    file_data = generator_data.get("file", {})
    paths_data = data["paths"]
    return BenchmarkConfig(
        generator=GeneratorConfig(
            backend=str(generator_data.get("backend", "")),
            openai=OpenAIConfig(
                base_url=str(openai_data.get("base_url", "")),
                api_key=str(openai_data.get("api_key", "")),
                model=str(openai_data.get("model", "")),
                timeout=int(openai_data.get("timeout", 60)),
                temperature=float(openai_data.get("temperature", 0.0)),
            ),
            file=FileGeneratorConfig(base_dir=str(file_data.get("base_dir", "."))),
        ),
        paths=PathConfig(
            tasks=str(paths_data["tasks"]),
            generated=str(paths_data["generated"]),
            nodes=str(paths_data["nodes"]),
            db=str(paths_data["db"]),
            report=str(paths_data["report"]),
        ),
        groups=[str(item) for item in data.get("groups", ["A", "B", "C"])],
    )


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
