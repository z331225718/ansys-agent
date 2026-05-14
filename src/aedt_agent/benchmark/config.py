from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aedt_agent.benchmark.generator import CodeGenerator, DefaultCodeGenerator, FileGenerator, OpenAIGenerator
from aedt_agent.benchmark.harness_generator import HarnessGenerator, HarnessGroupConfig, load_harness_group_config
from aedt_agent.benchmark.official_retriever import GitNexusOfficialRetriever, OfficialKnowledgeRetriever


@dataclass(frozen=True)
class OpenAIConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: int = 60
    temperature: float = 0.0
    max_retries: int = 2
    retry_delay: float = 2.0


@dataclass(frozen=True)
class FileGeneratorConfig:
    base_dir: str = "."


@dataclass(frozen=True)
class GeneratorConfig:
    backend: str = ""
    openai: OpenAIConfig = OpenAIConfig()
    file: FileGeneratorConfig = FileGeneratorConfig()


@dataclass(frozen=True)
class HarnessConfig:
    backend: str = "claude"
    command: str = "claude"
    timeout: int = 900
    group_a_config: str = "config/harness/group_a.json"
    group_b_config: str = "config/harness/group_b.json"
    group_c_config: str = "config/harness/group_c.json"
    work_dir: str = "benchmarks/harness_work"


@dataclass(frozen=True)
class OfficialRetrievalConfig:
    backend: str = "gitnexus_http"
    gitnexus_url: str = "http://127.0.0.1:4848"
    pyaedt_repo: str = "../pyaedt"
    pyaedt_examples: str = "../pyaedt-examples"
    top_k: int = 8
    timeout: int = 20


@dataclass(frozen=True)
class AEDTConfig:
    version: str = "2026.1"
    non_graphical: bool = True
    ansysem_root: str = "~/ansys_inc/v261/AnsysEM"
    awp_root: str = "~/ansys_inc/v261"
    timeout: int = 900


@dataclass(frozen=True)
class PathConfig:
    tasks: str
    generated: str
    nodes: str
    db: str
    report: str
    html_report: str = "benchmarks/reports/stage_a_sample_report.html"
    run_dir: str = "benchmarks/runs/stage_a_v2_latest"


@dataclass(frozen=True)
class BenchmarkConfig:
    generator: GeneratorConfig
    paths: PathConfig
    groups: list[str]
    harness: HarnessConfig = HarnessConfig()
    official_retrieval: OfficialRetrievalConfig = OfficialRetrievalConfig()
    aedt: AEDTConfig = AEDTConfig()

    def build_generator(self, repo_root: Path | None = None) -> CodeGenerator:
        backend = self.generator.backend.lower()
        root = Path(repo_root or Path.cwd())
        if backend == "harness":
            group_configs = {
                "A": load_harness_group_config(_resolve_config_path(root, self.harness.group_a_config)),
                "B": load_harness_group_config(_resolve_config_path(root, self.harness.group_b_config)),
                "C": _load_optional_harness_group_config(root, self.harness.group_c_config),
            }
            return HarnessGenerator(
                command=self.harness.command,
                timeout=self.harness.timeout,
                work_dir=_resolve_config_path(root, self.harness.work_dir),
                group_configs=group_configs,
                repo_root=root,
                variables={
                    "repo_root": str(root),
                    "pyaedt_repo": str(_resolve_config_path(root, self.official_retrieval.pyaedt_repo)),
                    "pyaedt_examples": str(_resolve_config_path(root, self.official_retrieval.pyaedt_examples)),
                },
            )
        if backend == "openai":
            return OpenAIGenerator(
                base_url=self.generator.openai.base_url,
                api_key=self.generator.openai.api_key,
                model=self.generator.openai.model,
                timeout=self.generator.openai.timeout,
                temperature=self.generator.openai.temperature,
                max_retries=self.generator.openai.max_retries,
                retry_delay=self.generator.openai.retry_delay,
            )
        if backend == "file":
            return FileGenerator(Path(self.generator.file.base_dir))
        return DefaultCodeGenerator()

    def build_retriever(self) -> OfficialKnowledgeRetriever:
        retrieval = self.official_retrieval
        if retrieval.backend in {"gitnexus_http", "gitnexus_cli"}:
            return GitNexusOfficialRetriever(
                pyaedt_repo=Path(retrieval.pyaedt_repo),
                examples_repo=Path(retrieval.pyaedt_examples) if retrieval.pyaedt_examples else None,
                backend=retrieval.backend,
                gitnexus_url=retrieval.gitnexus_url,
                top_k=retrieval.top_k,
                timeout=retrieval.timeout,
            )
        return OfficialKnowledgeRetriever()


def load_benchmark_config(path: Path) -> BenchmarkConfig:
    base_path = Path(path)
    data = _load_json(base_path)
    local_path = base_path.with_name(f"{base_path.stem}.local{base_path.suffix}")
    if local_path.exists():
        data = _deep_merge(data, _load_json(local_path))

    generator_data = data.get("generator", {})
    harness_data = data.get("harness", {})
    retrieval_data = data.get("official_retrieval", {})
    aedt_data = data.get("aedt", {})
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
                max_retries=int(openai_data.get("max_retries", 2)),
                retry_delay=float(openai_data.get("retry_delay", 2.0)),
            ),
            file=FileGeneratorConfig(base_dir=str(file_data.get("base_dir", "."))),
        ),
        paths=PathConfig(
            tasks=str(paths_data["tasks"]),
            generated=str(paths_data["generated"]),
            nodes=str(paths_data["nodes"]),
            db=str(paths_data["db"]),
            report=str(paths_data["report"]),
            html_report=str(paths_data.get("html_report", "benchmarks/reports/stage_a_sample_report.html")),
            run_dir=str(paths_data.get("run_dir", "benchmarks/runs/stage_a_v2_latest")),
        ),
        groups=[str(item) for item in data.get("groups", ["A", "B"])],
        harness=HarnessConfig(
            backend=str(harness_data.get("backend", "claude")),
            command=str(harness_data.get("command", "claude")),
            timeout=int(harness_data.get("timeout", 900)),
            group_a_config=str(harness_data.get("group_a_config", "config/harness/group_a.json")),
            group_b_config=str(harness_data.get("group_b_config", "config/harness/group_b.json")),
            group_c_config=str(harness_data.get("group_c_config", "config/harness/group_c.json")),
            work_dir=str(harness_data.get("work_dir", "benchmarks/harness_work")),
        ),
        official_retrieval=OfficialRetrievalConfig(
            backend=str(retrieval_data.get("backend", "gitnexus_http")),
            gitnexus_url=str(retrieval_data.get("gitnexus_url", "http://127.0.0.1:4848")),
            pyaedt_repo=str(retrieval_data.get("pyaedt_repo", "../pyaedt")),
            pyaedt_examples=str(retrieval_data.get("pyaedt_examples", "../pyaedt-examples")),
            top_k=int(retrieval_data.get("top_k", 8)),
            timeout=int(retrieval_data.get("timeout", 20)),
        ),
        aedt=AEDTConfig(
            version=str(aedt_data.get("version", "2026.1")),
            non_graphical=bool(aedt_data.get("non_graphical", True)),
            ansysem_root=str(aedt_data.get("ansysem_root", "~/ansys_inc/v261/AnsysEM")),
            awp_root=str(aedt_data.get("awp_root", "~/ansys_inc/v261")),
            timeout=int(aedt_data.get("timeout", 900)),
        ),
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


def _resolve_config_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return root / path


def _load_optional_harness_group_config(root: Path, value: str) -> HarnessGroupConfig:
    path = _resolve_config_path(root, value)
    if not path.exists():
        return HarnessGroupConfig()
    return load_harness_group_config(path)
