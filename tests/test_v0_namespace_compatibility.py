from __future__ import annotations

import importlib

import pytest


PACKAGE_MODULES = {
    "benchmark": [
        "aedt_executor",
        "config",
        "context_builder",
        "generator",
        "go_nogo",
        "graders",
        "harness_generator",
        "models",
        "node_plan_parser",
        "node_readiness",
        "official_retriever",
        "prompt_templates",
        "repair",
        "report_html",
        "report_html_stage_b",
        "report_html_v2",
        "runner",
        "runner_stage_b",
        "runner_v2",
        "semantic_lite",
        "stage_b_models",
        "stage_b_presentation",
        "stage_b_validation",
        "task_sets",
        "tool_usage",
        "v2_models",
    ],
    "chat": ["repair_context", "workflow_planner"],
    "demo": [
        "config",
        "import_cutout",
        "layout_ports",
        "planner",
        "planner_benchmark",
        "preflight",
        "service",
        "tuning",
        "web",
    ],
    "evolution": ["evaluator", "miner", "models", "policy", "proposer"],
}

MODULE_PAIRS = [
    (f"aedt_agent.{package}.{module}", f"aedt_agent.v0.{package}.{module}")
    for package, modules in PACKAGE_MODULES.items()
    for module in modules
]


@pytest.mark.parametrize(("legacy_name", "v0_name"), MODULE_PAIRS)
def test_legacy_import_resolves_to_same_v0_module(legacy_name: str, v0_name: str):
    legacy_module = importlib.import_module(legacy_name)
    v0_module = importlib.import_module(v0_name)

    assert legacy_module is v0_module


def test_shared_domain_packages_remain_at_existing_paths():
    for module_name in [
        "aedt_agent.workflow.executor",
        "aedt_agent.nodes.registry",
        "aedt_agent.layout.local_cut",
        "aedt_agent.validation.rules",
        "aedt_agent.mcp.node_executor",
        "aedt_agent.knowledge.sqlite_provider",
        "aedt_agent.reporting.channel_scoring_report",
    ]:
        assert importlib.import_module(module_name) is not None
