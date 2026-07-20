from __future__ import annotations

from typing import Any

from aedt_agent.interactive.catalog import CapabilityCatalog
from aedt_agent.interactive.layout import LayoutSessionManager, selector_from_payload
from aedt_agent.interactive.router import TaskRouter


class InteractiveKernel:
    def __init__(
        self,
        *,
        catalog: CapabilityCatalog | None = None,
        session_manager: LayoutSessionManager | None = None,
        workflow_templates: set[str] | None = None,
        code_fallback_enabled: bool = False,
        api_memory_ready: bool = False,
        exploration_policy_enabled: bool = False,
    ) -> None:
        self.catalog = catalog or CapabilityCatalog()
        self.sessions = session_manager or LayoutSessionManager()
        self.router = TaskRouter(
            self.catalog,
            workflow_templates=workflow_templates,
            code_fallback_enabled=code_fallback_enabled,
            api_memory_ready=api_memory_ready,
            exploration_policy_enabled=exploration_policy_enabled,
        )

    def list_capabilities(self) -> dict[str, Any]:
        return self.catalog.to_dict()

    def open_layout_session(
        self,
        project_path: str,
        *,
        writable: bool = False,
        workspace: str | None = None,
        version: str = "2026.1",
        edb_backend: str = "auto",
    ) -> dict[str, Any]:
        return self.sessions.open_session(
            project_path,
            writable=writable,
            workspace=workspace,
            version=version,
            edb_backend=edb_backend,
        )

    def close_layout_session(self, session_id: str) -> dict[str, Any]:
        return self.sessions.close_session(session_id)

    def execute_capability(self, name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        spec = self.catalog.get(name)
        _validate_inputs(spec.input_schema, inputs)
        if name == "layout.paths.list":
            return self.sessions.list_paths(
                str(inputs["session_id"]),
                selector_from_payload(inputs.get("selector")),
            )
        if name == "layout.path_width.parameterize.preview":
            preview = self.sessions.preview_parameterize_width(
                str(inputs["session_id"]),
                selector=selector_from_payload(inputs.get("selector")),
                variable_name=str(inputs["variable_name"]),
                variable_value=inputs["variable_value"],
            )
            return preview if isinstance(preview, dict) else preview.to_dict()
        if name == "layout.path_width.parameterize.apply":
            result = self.sessions.apply_parameterize_width(
                str(inputs["session_id"]),
                str(inputs["preview_id"]),
            )
            return result if isinstance(result, dict) else result.to_dict()
        raise NotImplementedError(f"capability has no executor: {name}")


def _validate_inputs(schema: dict[str, Any], inputs: dict[str, Any]) -> None:
    if not isinstance(inputs, dict):
        raise TypeError("capability inputs must be an object")
    required = set(schema.get("required") or ())
    missing = sorted(required.difference(inputs))
    if missing:
        raise ValueError(f"missing required capability input: {missing[0]}")
    if schema.get("additionalProperties") is False:
        supported = set((schema.get("properties") or {}).keys())
        extra = sorted(set(inputs).difference(supported))
        if extra:
            raise ValueError(f"unsupported capability input: {extra[0]}")
