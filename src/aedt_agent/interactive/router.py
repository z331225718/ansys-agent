from __future__ import annotations

from aedt_agent.interactive.catalog import CapabilityCatalog
from aedt_agent.interactive.contracts import RouteKind, TaskRoute


class TaskRouter:
    """Deterministic route selection after an LLM has produced a structured intent."""

    def __init__(
        self,
        catalog: CapabilityCatalog,
        *,
        workflow_templates: set[str] | None = None,
        code_fallback_enabled: bool = False,
        api_memory_ready: bool = False,
        exploration_policy_enabled: bool = False,
    ) -> None:
        self.catalog = catalog
        self.workflow_templates = set(workflow_templates or ())
        self.code_fallback_enabled = bool(
            code_fallback_enabled and api_memory_ready and exploration_policy_enabled
        )

    def route(
        self,
        *,
        requested_capability: str | None = None,
        requested_workflow: str | None = None,
    ) -> TaskRoute:
        if requested_workflow and requested_workflow in self.workflow_templates:
            return TaskRoute(RouteKind.WORKFLOW, requested_workflow, "known_workflow_template")
        if requested_capability and self.catalog.contains(requested_capability):
            return TaskRoute(RouteKind.CAPABILITY, requested_capability, "registered_capability")
        if self.code_fallback_enabled:
            return TaskRoute(RouteKind.CODE_FALLBACK, None, "capability_miss_with_safe_exploration_ready")
        return TaskRoute(RouteKind.UNSUPPORTED, None, "safe_exploration_not_ready")
