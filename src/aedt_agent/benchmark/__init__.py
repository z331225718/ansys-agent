"""Compatibility imports for the preserved v0 benchmark package."""

from aedt_agent._compat import install_package_aliases

_target = install_package_aliases(
    __name__,
    "aedt_agent.v0.benchmark",
    [
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
)

__all__ = getattr(_target, "__all__", [])


def __getattr__(name: str):
    return getattr(_target, name)
