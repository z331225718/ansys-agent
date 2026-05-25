from pathlib import Path

from aedt_agent.benchmark.context_builder import build_context
from aedt_agent.benchmark.models import BenchmarkTask
from aedt_agent.knowledge.build_sqlite import build_api_semantics_db
from aedt_agent.knowledge.sqlite_provider import SQLiteKnowledgeProvider
from aedt_agent.nodes.registry import NodeRegistry


def _provider(tmp_path):
    db_path = tmp_path / "api_semantics.sqlite"
    build_api_semantics_db(
        Path("knowledge/api_semantics/api_semantics.schema.sql"),
        Path("knowledge/api_semantics/api_semantics.seed.jsonl"),
        db_path,
    )
    return SQLiteKnowledgeProvider(
        db_path,
        workflow_cases_dir=Path("knowledge/workflow_cases"),
        common_traps_dir=Path("knowledge/common_traps"),
    )


def test_group_a_context_contains_only_requirement(tmp_path):
    task = BenchmarkTask.from_yaml(Path("benchmarks/tasks/L1_create_substrate.yaml"))
    context = build_context(
        group="A",
        task=task,
        provider=_provider(tmp_path),
        registry=NodeRegistry.from_directory(Path("nodes/catalog")),
    )
    assert task.requirement in context
    assert "API whitelist" not in context


def test_group_c_context_contains_nodes_api_and_traps(tmp_path):
    task = BenchmarkTask.from_yaml(Path("benchmarks/tasks/L3_patch_antenna_sparameter.yaml"))
    context = build_context(
        group="C",
        task=task,
        provider=_provider(tmp_path),
        registry=NodeRegistry.from_directory(Path("nodes/catalog")),
    )
    assert "API whitelist" in context
    assert "Common traps" in context
    assert "create_substrate" in context
    assert "sizes must be positive" in context
    assert "negative or zero size creates invalid geometry" in context
    assert "airbox_too_small" in context
    assert "frequency-aware" in context


def test_group_c_context_contains_sweep_trap_details(tmp_path):
    task = BenchmarkTask.from_yaml(Path("benchmarks/tasks/Trap_sweep_misses_freq.yaml"))
    context = build_context(
        group="C",
        task=task,
        provider=_provider(tmp_path),
        registry=NodeRegistry.from_directory(Path("nodes/catalog")),
    )

    assert "target/report/tuning frequency lies inside the sweep" in context
    assert "sweep_range_misses_target_frequency" in context


def test_group_c_context_within_token_limit(tmp_path):
    task = BenchmarkTask.from_yaml(Path("benchmarks/tasks/L3_patch_antenna_sparameter.yaml"))
    context = build_context(
        group="C",
        task=task,
        provider=_provider(tmp_path),
        registry=NodeRegistry.from_directory(Path("nodes/catalog")),
    )
    estimated_tokens = len(context) // 4
    assert estimated_tokens <= 8000
