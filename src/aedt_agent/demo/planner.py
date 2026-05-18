from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib import request as urlrequest

from aedt_agent.chat.workflow_planner import ChatPlannerInput, ChatWorkflowPlanner
from aedt_agent.demo.config import PlannerConfig
from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.workflow.models import Workflow
from aedt_agent.workflow.templates import WorkflowTemplateCatalog
from aedt_agent.workflow.validator import WorkflowValidator


class WorkflowProposalClient(Protocol):
    def propose_workflow(self, request: str, context: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PlannerAttempt:
    attempt: int
    mode: str
    workflow: dict[str, Any] | None
    validation: dict[str, Any]
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "mode": self.mode,
            "workflow": self.workflow,
            "validation": self.validation,
            "error": self.error,
        }


@dataclass(frozen=True)
class PlannerRunResult:
    planner_mode: str
    selected_template: str | None
    generated_workflow: Workflow | None
    missing_information: list[str]
    assumptions: list[str]
    confidence: float
    validation_errors: list[dict[str, Any]]
    attempts: list[PlannerAttempt] = field(default_factory=list)
    fallback_reason: str = ""

    @property
    def repair_count(self) -> int:
        return max(0, len(self.attempts) - 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "planner_mode": self.planner_mode,
            "selected_template": self.selected_template,
            "generated_workflow": self.generated_workflow.to_dict() if self.generated_workflow else None,
            "missing_information": list(self.missing_information),
            "assumptions": list(self.assumptions),
            "confidence": self.confidence,
            "validation_errors": list(self.validation_errors),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "repair_count": self.repair_count,
            "fallback_reason": self.fallback_reason,
        }


class PlannerRunner:
    def __init__(
        self,
        *,
        config: PlannerConfig,
        node_catalog: NodeCatalog,
        workflow_templates: WorkflowTemplateCatalog,
        llm_client: WorkflowProposalClient | None = None,
    ) -> None:
        self.config = config
        self.node_catalog = node_catalog
        self.workflow_templates = workflow_templates
        self.llm_client = llm_client

    def plan(self, user_request: str, *, requested_mode: str | None = None, retrieved_context: list[str] | None = None) -> PlannerRunResult:
        mode = requested_mode or self.config.mode
        if mode == "llm":
            if self.llm_client is not None or self.config.api_key:
                return self._llm_plan(user_request, retrieved_context or [])
            return self._deterministic_plan(user_request, retrieved_context or [], fallback_reason="llm_not_configured")
        return self._deterministic_plan(user_request, retrieved_context or [])

    def _deterministic_plan(self, user_request: str, retrieved_context: list[str], fallback_reason: str = "") -> PlannerRunResult:
        output = ChatWorkflowPlanner().plan(
            ChatPlannerInput(
                user_request=user_request,
                node_catalog=self.node_catalog,
                workflow_templates=self.workflow_templates,
                retrieved_context=retrieved_context,
            )
        )
        workflow = output.generated_workflow
        validation = _validate_workflow(workflow, self.node_catalog) if workflow else {"passed": False, "errors": [], "warnings": []}
        return PlannerRunResult(
            planner_mode="deterministic",
            selected_template=output.selected_template,
            generated_workflow=workflow,
            missing_information=output.missing_information,
            assumptions=output.assumptions,
            confidence=output.confidence,
            validation_errors=list(validation.get("errors", [])),
            attempts=[PlannerAttempt(1, "deterministic", workflow.to_dict() if workflow else None, validation)],
            fallback_reason=fallback_reason,
        )

    def _llm_plan(self, user_request: str, retrieved_context: list[str]) -> PlannerRunResult:
        client = self.llm_client or OpenAICompatibleWorkflowClient(self.config)
        attempts: list[PlannerAttempt] = []
        previous_errors: list[dict[str, Any]] = []
        last_workflow: Workflow | None = None
        max_attempts = max(1, int(self.config.max_repair_attempts))
        for index in range(1, max_attempts + 1):
            try:
                proposal = client.propose_workflow(
                    user_request,
                    _planner_context(self.workflow_templates, self.node_catalog, previous_errors, retrieved_context),
                )
                workflow_data = proposal.get("workflow", proposal)
                workflow = Workflow.from_dict(workflow_data)
                validation = _validate_workflow(workflow, self.node_catalog)
                attempts.append(PlannerAttempt(index, "llm", workflow.to_dict(), validation))
                last_workflow = workflow
                previous_errors = list(validation.get("errors", []))
                if validation.get("passed") is True:
                    return PlannerRunResult(
                        planner_mode="llm",
                        selected_template=str(workflow.metadata.get("template_id", "")) or None,
                        generated_workflow=workflow,
                        missing_information=[],
                        assumptions=["LLM proposed workflow JSON; backend validator accepted it."],
                        confidence=0.74,
                        validation_errors=[],
                        attempts=attempts,
                    )
            except Exception as exc:
                attempts.append(PlannerAttempt(index, "llm", None, {"passed": False, "errors": [], "warnings": []}, error=f"{type(exc).__name__}: {exc}"))
                previous_errors = [{"code": type(exc).__name__, "message": str(exc)}]
        return PlannerRunResult(
            planner_mode="llm",
            selected_template=None,
            generated_workflow=last_workflow,
            missing_information=[],
            assumptions=["LLM repair loop stopped before a valid workflow was produced."],
            confidence=0.2,
            validation_errors=previous_errors,
            attempts=attempts,
        )


class OpenAICompatibleWorkflowClient:
    def __init__(self, config: PlannerConfig) -> None:
        self.config = config

    def propose_workflow(self, request: str, context: dict[str, Any]) -> dict[str, Any]:
        if not self.config.api_key:
            raise ValueError("planner api_key is required for llm mode")
        base_url = (self.config.base_url or "").rstrip("/")
        if not base_url:
            raise ValueError("planner base_url is required for llm mode")
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": json.dumps({"request": request, "context": context}, ensure_ascii=False)},
            ],
            "temperature": 0,
        }
        req = urlrequest.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json", "authorization": f"Bearer {self.config.api_key}"},
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return _parse_json_content(content)


def _validate_workflow(workflow: Workflow | None, catalog: NodeCatalog) -> dict[str, Any]:
    if workflow is None:
        return {"passed": False, "errors": [], "warnings": []}
    return WorkflowValidator(catalog).validate(workflow).to_dict()


def _planner_context(
    templates: WorkflowTemplateCatalog,
    catalog: NodeCatalog,
    previous_errors: list[dict[str, Any]],
    retrieved_context: list[str],
) -> dict[str, Any]:
    return {
        "templates": {template.template_id: template.to_dict() for template in templates.list_templates()},
        "node_catalog": catalog.to_dict(),
        "previous_errors": previous_errors,
        "retrieved_context": retrieved_context,
        "rules": [
            "Return workflow JSON only.",
            "Do not return executable PyAEDT Python.",
            "Every workflow is validated before execution.",
        ],
    }


def _system_prompt() -> str:
    return (
        "You generate AEDT workflow JSON for a controlled node executor. "
        "Return one JSON object. Do not include markdown. Do not emit Python code."
    )


def _parse_json_content(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise TypeError("LLM planner response must be a JSON object")
    return data
