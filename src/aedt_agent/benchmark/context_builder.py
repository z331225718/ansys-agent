from __future__ import annotations

from aedt_agent.benchmark.models import BenchmarkTask
from aedt_agent.knowledge.provider_interface import KnowledgeProvider
from aedt_agent.nodes.registry import NodeRegistry


def build_context(group: str, task: BenchmarkTask, provider: KnowledgeProvider, registry: NodeRegistry) -> str:
    requirement = f"Requirement:\n{task.requirement}"
    if group == "A":
        return requirement

    whitelist = registry.api_whitelist(task.allowed_nodes)
    api_lines: list[str] = []
    for api in whitelist:
        results = provider.search_api(api.split(".")[-1], limit=1)
        if results:
            item = results[0]
            api_lines.append(f"- {item.fqname}: {item.signature}")

    context_parts = [requirement, "API whitelist:\n" + "\n".join(api_lines or [f"- {api}" for api in whitelist])]

    if group == "C":
        cases = [
            case
            for case in provider.list_workflow_cases()
            if set(case.workflow_steps) & set(task.expected_workflow)
        ]
        traps = provider.list_common_traps(filter_ids=task.known_failure_modes)
        context_parts.append(
            "Allowed nodes:\n" + "\n".join(f"- {node_id}" for node_id in task.allowed_nodes)
        )
        context_parts.append(
            "Workflow cases:\n" + "\n".join(f"- {case.case_id}: {case.natural_language_task}" for case in cases)
        )
        context_parts.append(
            "Common traps:\n" + "\n".join(f"- {trap.trap_id}" for trap in traps)
        )

    return _truncate_to_token_limit("\n\n".join(context_parts))


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _truncate_to_token_limit(text: str, max_tokens: int = 8000) -> str:
    if _estimate_tokens(text) <= max_tokens:
        return text
    return text[: max_tokens * 4] + "\n\n[Context truncated to fit token limit]"
