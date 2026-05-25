from __future__ import annotations

from aedt_agent.benchmark.models import BenchmarkTask
from aedt_agent.knowledge.models import ApiSemantic, CommonTrap
from aedt_agent.knowledge.provider_interface import KnowledgeProvider
from aedt_agent.nodes.registry import NodeRegistry


def build_context(group: str, task: BenchmarkTask, provider: KnowledgeProvider, registry: NodeRegistry) -> str:
    requirement = f"Requirement:\n{task.requirement}"
    if group == "A":
        return requirement

    whitelist = registry.api_whitelist(task.allowed_nodes)
    api_lines: list[str] = []
    for api in whitelist:
        item = _find_api_semantic(provider, api)
        if item is not None:
            api_lines.extend(_format_api_semantic(item, include_details=group in {"B", "C"}))

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
            "Common traps:\n" + "\n".join(_format_common_trap(trap) for trap in traps)
        )

    return _truncate_to_token_limit("\n\n".join(context_parts))


def _find_api_semantic(provider: KnowledgeProvider, fqname: str) -> ApiSemantic | None:
    results = provider.search_api(fqname, limit=20)
    for item in results:
        if item.fqname == fqname:
            return item
    results = provider.search_api(fqname.split(".")[-1], limit=20)
    for item in results:
        if item.fqname == fqname:
            return item
    return results[0] if results else None


def _format_api_semantic(item: ApiSemantic, *, include_details: bool) -> list[str]:
    lines = [f"- {item.fqname}: {item.signature}"]
    if not include_details:
        return lines
    if item.constraints:
        lines.append("  constraints: " + "; ".join(item.constraints[:3]))
    if item.common_errors:
        lines.append("  common errors: " + "; ".join(item.common_errors[:3]))
    if item.common_traps:
        lines.append("  related traps: " + ", ".join(item.common_traps[:3]))
    return lines


def _format_common_trap(trap: CommonTrap) -> str:
    parts = [
        f"- {trap.trap_id}: {trap.symptom}",
        f"  detection: {trap.detection}",
        f"  prevention: {trap.prevention}",
    ]
    if trap.validation_rule:
        parts.append(f"  validation: {trap.validation_rule}")
    return "\n".join(parts)


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _truncate_to_token_limit(text: str, max_tokens: int = 8000) -> str:
    if _estimate_tokens(text) <= max_tokens:
        return text
    return text[: max_tokens * 4] + "\n\n[Context truncated to fit token limit]"
