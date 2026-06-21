from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.graph_template import GraphNode, GraphTemplate
from aedt_agent.agent.handoff import HandoffValidationError, validate_handoff
from aedt_agent.agent.mission import (
    ApprovalDecision,
    EvidencePackage,
    GraphRunRecord,
    JobStatus,
    NodeRunRecord,
    NodeRunStatus,
)
from aedt_agent.agent.scorecard import score_mission


@dataclass(frozen=True)
class GraphNodeExecutionContext:
    runtime: Any
    graph_run: GraphRunRecord
    node_run: NodeRunRecord
    node: GraphNode
    template: GraphTemplate
    input_payload: dict[str, Any]
    run_index: int
    worker_id: str


@dataclass(frozen=True)
class GraphNodeExecutionResult:
    status: NodeRunStatus
    outcome: str
    output_payload: dict[str, Any]
    artifact_refs: list[str]
    evidence_package_id: str | None = None
    error: dict[str, Any] | None = None


GraphHandler = Callable[[GraphNodeExecutionContext], GraphNodeExecutionResult | dict[str, Any]]


class GraphNodeExecutorRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, GraphHandler] = {}

    def register(self, handler_id: str, handler: GraphHandler) -> None:
        if handler_id in self._handlers:
            raise ValueError(f"graph handler already registered: {handler_id}")
        self._handlers[handler_id] = handler

    def execute(self, handler_id: str, context: GraphNodeExecutionContext) -> GraphNodeExecutionResult:
        handler = self._handlers.get(handler_id)
        if handler is None:
            raise KeyError(f"graph handler not found: {handler_id}")
        return _normalize_result(handler(context))


_DEFAULT_EXECUTOR_REGISTRY: GraphNodeExecutorRegistry | None = None


def default_graph_executor_registry() -> GraphNodeExecutorRegistry:
    global _DEFAULT_EXECUTOR_REGISTRY
    if _DEFAULT_EXECUTOR_REGISTRY is None:
        registry = GraphNodeExecutorRegistry()
        from aedt_agent.agent.optimization_handlers import (
            register_optimization_handlers,
        )

        register_optimization_handlers(registry)
        _DEFAULT_EXECUTOR_REGISTRY = registry
    return _DEFAULT_EXECUTOR_REGISTRY


def execute_graph_node(
    context: GraphNodeExecutionContext,
    *,
    registry: GraphNodeExecutorRegistry | None = None,
) -> GraphNodeExecutionResult:
    try:
        if context.node.kind == "agent":
            result = execute_agent_node(context, registry=registry)
        elif context.node.handler:
            result = (registry or default_graph_executor_registry()).execute(
                context.node.handler,
                context,
            )
        elif context.node.kind == "worker":
            result = _execute_worker(context)
        elif context.node.role == "planner":
            result = _execute_planner(context)
        elif context.node.role == "validator":
            result = _execute_validator(context)
        elif context.node.role == "scorecard":
            result = _execute_scorecard(context)
        elif context.node.role == "approval_gate":
            result = _execute_approval_gate(context)
        else:
            raise ValueError(f"unsupported graph node executor: {context.node.node_id}")
        if result.status == NodeRunStatus.SUCCEEDED:
            _validate_output(context, result.output_payload)
        return result
    except HandoffValidationError as exc:
        return _failed_result("handoff_validation", str(exc))
    except Exception as exc:
        return _failed_result(
            "graph_node_execution",
            str(exc),
            details={"error_type": type(exc).__name__},
        )


def execute_agent_node(
    context: GraphNodeExecutionContext,
    *,
    registry: GraphNodeExecutorRegistry | None = None,
) -> GraphNodeExecutionResult:
    """Execute a kind=agent node by calling an LLM with the node's system_prompt.

    Resolves prompt text from template.prompts (if system_prompt is a key)
    or uses the system_prompt value directly as inline prompt text.
    """
    import json as _json

    # 1. Resolve constraints (merge profile defaults + node overrides)
    constraints = _resolve_agent_constraints(context)

    # 2. Resolve prompt
    prompt_text = _resolve_prompt(context)
    if not prompt_text:
        fallback = _execute_agent_fallback(
            context,
            registry=registry,
            constraints=constraints,
            reason="agent node has no system_prompt",
        )
        if fallback is not None:
            return fallback
        return GraphNodeExecutionResult(
            NodeRunStatus.FAILED, "failed", {},
            error={"error_class": "agent_no_prompt", "message": "agent node has no system_prompt"},
        )

    # 3. Build user message with knowledge context
    user_parts: dict[str, Any] = {
        "handoff": context.input_payload,
        "output_schema": context.node.output_schema,
        "constraints": constraints,
    }
    # Inject knowledge for code_writer agents
    if context.node.capability == "code_writer":
        knowledge = _get_agent_knowledge(context)
        if knowledge:
            user_parts["knowledge"] = knowledge
    user_msg = _json.dumps(user_parts, ensure_ascii=False, indent=2)

    # 4. Call LLM
    try:
        from aedt_agent.agent.llm import LlmConfig, llm_complete

        config = LlmConfig.from_env(profile=context.node.profile)
        if not config.api_key:
            raise RuntimeError("LLM not configured")
        model = constraints.get("model") or config.model
        temperature = float(constraints.get("temperature", 0.2))
        raw = llm_complete(
            prompt_text, user_msg,
            config=LlmConfig(
                model=model,
                api_key=config.api_key,
                base_url=config.base_url,
                temperature=temperature,
                max_tokens=int(constraints.get("max_tokens", 2048)),
            ),
        )
    except (ImportError, RuntimeError) as e:
        fallback = _execute_agent_fallback(
            context,
            registry=registry,
            constraints=constraints,
            reason=str(e),
        )
        if fallback is not None:
            return fallback
        return GraphNodeExecutionResult(
            NodeRunStatus.FAILED, "failed", {},
            error={"error_class": "agent_llm_unavailable", "message": str(e)},
        )
    except Exception as e:
        return GraphNodeExecutionResult(
            NodeRunStatus.FAILED, "failed", {},
            error={"error_class": "agent_llm_error", "message": str(e)},
        )

    # 5. Parse JSON from LLM output
    try:
        output = _json.loads(raw)
    except _json.JSONDecodeError:
        # Try to extract from markdown code fence
        output = _extract_json_from_markdown(raw)
    if not isinstance(output, dict):
        return GraphNodeExecutionResult(
            NodeRunStatus.FAILED, "failed", {},
            error={"error_class": "agent_output_not_json", "message": "agent output is not valid JSON"},
        )

    if context.node.capability == "code_writer":
        from aedt_agent.agent.code_agent import validate_code_agent_output

        code, errors = validate_code_agent_output(
            str(output.get("code") or ""),
            allowed_imports=list(constraints.get("allowed_imports") or []),
            forbidden_patterns=list(constraints.get("forbidden_patterns") or []),
        )
        if errors:
            return GraphNodeExecutionResult(
                NodeRunStatus.FAILED,
                "failed",
                output,
                [],
                error={
                    "error_class": "code_agent_validation",
                    "message": "; ".join(errors),
                },
            )
        output["code"] = code
        output["code_validation"] = {
            "syntax": "passed",
            "imports": "passed",
            "forbidden_patterns": "passed",
        }

    # 6. Validate handoff schema
    if context.node.output_schema:
        try:
            schema = context.template.handoffs[context.node.output_schema]
            validate_handoff(schema, output)
        except HandoffValidationError as e:
            return GraphNodeExecutionResult(
                NodeRunStatus.FAILED, "failed", output,
                error={"error_class": "agent_handoff_invalid", "message": str(e)},
            )

    outcome = _agent_edge_outcome(output, constraints)
    return GraphNodeExecutionResult(NodeRunStatus.SUCCEEDED, outcome, output, [])


def _execute_agent_fallback(
    context: GraphNodeExecutionContext,
    *,
    registry: GraphNodeExecutorRegistry | None,
    constraints: dict[str, Any],
    reason: str,
) -> GraphNodeExecutionResult | None:
    if not context.node.handler:
        return None
    if constraints.get("deterministic_fallback", True) is False:
        return None
    result = (registry or default_graph_executor_registry()).execute(
        context.node.handler,
        context,
    )
    output = dict(result.output_payload)
    output.setdefault(
        "agent_fallback",
        {
            "status": "used",
            "reason": reason,
            "handler": context.node.handler,
        },
    )
    return GraphNodeExecutionResult(
        result.status,
        result.outcome,
        output,
        list(result.artifact_refs),
        evidence_package_id=result.evidence_package_id,
        error=result.error,
    )


def _agent_edge_outcome(
    output: dict[str, Any],
    constraints: dict[str, Any],
) -> str:
    raw = output.get("edge_outcome") or output.get("decision")
    if raw is None:
        return "succeeded"
    outcome = str(raw).strip()
    if not outcome:
        return "succeeded"
    allowed = constraints.get("allowed_decisions")
    if isinstance(allowed, list) and allowed:
        allowed_values = {str(item) for item in allowed}
        if outcome not in allowed_values:
            return "failed"
    return outcome


def _resolve_prompt(context: GraphNodeExecutionContext) -> str:
    """Resolve the system_prompt for an agent node."""
    prompt_key = context.node.system_prompt
    if not prompt_key:
        return ""
    # If prompt_key matches a key in template.prompts, use that
    if prompt_key in context.template.prompts:
        return context.template.prompts[prompt_key]
    # Otherwise use as inline prompt text
    return prompt_key


def _resolve_agent_constraints(context: GraphNodeExecutionContext) -> dict[str, Any]:
    """Merge profile defaults with node-level constraint overrides."""
    profile_name = context.node.profile or "standard"
    profile = context.template.profiles.get(profile_name, {})
    merged = dict(profile)
    if context.node.constraints:
        merged.update(context.node.constraints)
    if context.node.model:
        merged["model"] = context.node.model
    return merged


def _extract_json_from_markdown(text: str) -> dict[str, Any]:
    """Extract JSON from markdown code fences."""
    import json as _json
    import re as _re
    # Try ```json ... ``` first
    match = _re.search(r'```(?:json)?\s*\n(.*?)\n```', text, _re.DOTALL)
    if match:
        try:
            return _json.loads(match.group(1))
        except _json.JSONDecodeError:
            pass
    # Try first { ... } block
    match = _re.search(r'\{.*\}', text, _re.DOTALL)
    if match:
        try:
            return _json.loads(match.group(0))
        except _json.JSONDecodeError:
            pass
    return {}


# ── Agent Knowledge Injection ──

_knowledge_provider: Any = None  # set at startup


def set_agent_knowledge_provider(provider: Any) -> None:
    global _knowledge_provider
    _knowledge_provider = provider


def _get_agent_knowledge(context: GraphNodeExecutionContext) -> dict[str, Any] | None:
    """Get relevant API docs, examples, and common traps for an agent node."""
    if _knowledge_provider is None:
        return None

    try:
        capability = context.node.capability
        handoff = context.input_payload

        # Extract search terms from handoff
        search_terms = []
        for key in ("signal_nets", "target_metrics", "plan_summary", "goal", "_goal"):
            val = handoff.get(key, "")
            if isinstance(val, str) and val:
                search_terms.append(val)
            elif isinstance(val, list):
                search_terms.extend(str(v) for v in val[:3])

        query = " ".join(search_terms[:3]) if search_terms else "hfss 3d layout build"
        apis = _knowledge_provider.search_api(query, limit=8)

        if not apis:
            return None

        # Build knowledge context
        api_docs = []
        for api in apis:
            doc = f"## {api.fqname}\n```python\n{api.signature}\n```\n"
            if api.docstring:
                doc += f"{api.docstring[:300]}\n"
            if api.common_errors:
                doc += f"⚠️ Common errors: {', '.join(api.common_errors[:3])}\n"
            if api.constraints:
                doc += f"🔒 Constraints: {', '.join(api.constraints[:3])}\n"
            api_docs.append(doc)

        traps = _knowledge_provider.list_common_traps(None)
        trap_text = ""
        if traps:
            trap_text = "\n## ⚠️ Common Pitfalls\n" + "\n".join(
                f"- **{t.trap_id}**: {t.description[:200]}" for t in traps[:5]
            )

        cases = _knowledge_provider.list_workflow_cases()
        case_text = ""
        if cases:
            case_text = "\n## 📋 Reference Workflows\n" + "\n".join(
                f"- **{c.case_id}**: {c.description[:200]}" for c in cases[:3]
            )

        return {
            "api_reference": "\n\n".join(api_docs),
            "common_traps": trap_text,
            "workflow_examples": case_text,
        }
    except Exception:
        return None
    """Extract JSON from markdown code fences."""
    import json as _json
    import re as _re
    # Try ```json ... ``` first
    match = _re.search(r'```(?:json)?\s*\n(.*?)\n```', text, _re.DOTALL)
    if match:
        try:
            return _json.loads(match.group(1))
        except _json.JSONDecodeError:
            pass
    # Try first { ... } block
    match = _re.search(r'\{.*\}', text, _re.DOTALL)
    if match:
        try:
            return _json.loads(match.group(0))
        except _json.JSONDecodeError:
            pass
    return {}


def _execute_planner(context: GraphNodeExecutionContext) -> GraphNodeExecutionResult:
    output = dict(context.input_payload)
    output["planning_source"] = "graph_initial_payload"

    # BRD local-cut request planning: fill defaults and add plan summary
    if context.node.output_schema == "brd_local_cut_request":
        _plan_brd_local_cut_request(output)

    # LLM-powered planning: call the model if kind is llm and input exists
    if context.node.kind == "llm":
        output = _llm_plan(context, output)

    return GraphNodeExecutionResult(NodeRunStatus.SUCCEEDED, "succeeded", output, [])


def _llm_plan(context: GraphNodeExecutionContext, output: dict[str, Any]) -> dict[str, Any]:
    """Use LLM to generate/refine the plan from the user's goal."""
    import json as _json

    try:
        from aedt_agent.agent.llm import LlmConfig, llm_complete_json
    except ImportError:
        return output

    config = LlmConfig.from_env()
    if not config.api_key:
        return output  # No LLM configured — deterministic fallback

    goal = context.input_payload.get("_goal", context.input_payload.get("goal", ""))
    if not goal:
        goal = str(context.input_payload.get("plan_summary", "optimize BRD channel"))

    user_msg = _json.dumps(context.input_payload, ensure_ascii=False, indent=2)

    system = (
        "You are an Ansys AEDT electromagnetic simulation agent. "
        "Your job is to analyze the user's engineering request and produce a "
        "structured plan for BRD/MCM channel optimization.\n\n"
        "Output a JSON object with these fields:\n"
        '- plan_summary: one-sentence summary of what will be done\n'
        '- signal_nets: list of signal net names (from user input)\n'
        '- reference_nets: list of reference/GND nets\n'
        '- target_metrics: list of {type, target_db, freq_ghz} objects\n'
        '- suggested_actions: list of candidate optimization actions with '
        '{action_type, reason, priority} (1=highest, 3=lowest)\n'
        '- port_recommendation: {count, style, notes}\n'
        '- risks: list of potential issues to watch for\n\n'
        "Be specific. Use the actual net names, frequencies, and geometry "
        "from the user's input. Do NOT invent unrelated actions."
    )

    try:
        plan = llm_complete_json(system, user_msg, config=config)
    except Exception:
        return output  # LLM call failed → deterministic fallback

    # Merge LLM plan into output, preserving user-supplied fields
    for key in ("plan_summary", "suggested_actions", "port_recommendation", "risks"):
        if key in plan:
            output[key] = plan[key]
    if "target_metrics" in plan and plan["target_metrics"]:
        output.setdefault("target_metrics", []).extend(plan["target_metrics"])

    output["planning_source"] = "llm"
    output["llm_model"] = config.model
    return output


def _plan_brd_local_cut_request(payload: dict[str, Any]) -> None:
    """Fill in defaults and add plan summary for a BRD local-cut build request."""
    payload.setdefault("reference_nets", ["GND"])
    payload.setdefault("adapter_mode", "real_build")
    payload.setdefault("target_metrics", [])
    payload.setdefault("uniform_line_port_hint", {"count": 2, "style": "uniform_line"})
    # Override empty port hints
    if not payload.get("uniform_line_port_hint"):
        payload["uniform_line_port_hint"] = {"count": 2, "style": "uniform_line"}
    payload.setdefault("port_candidates", {"status": "unresolved", "recommended_endpoints": []})
    if not payload.get("port_candidates"):
        payload["port_candidates"] = {"status": "unresolved", "recommended_endpoints": []}
    payload.setdefault("solve_enabled", False)

    # Derive artifact_dir if not present
    if "artifact_dir" not in payload:
        mission_id = payload.get("mission_id", "")
        layout = payload.get("layout_file", "unknown")
        base = str(Path(str(layout)).parent) if layout else "."
        payload["artifact_dir"] = str(Path(base) / f"brd_build_{mission_id[:8] if mission_id else 'adhoc'}")

    # Build plan summary
    signal_nets = payload.get("signal_nets", [])
    region = payload.get("local_cut_region", {})
    payload["plan_summary"] = (
        f"BRD local-cut build: {', '.join(signal_nets) if signal_nets else 'no nets'} "
        f"in region {region.get('x1', '?')},{region.get('y1', '?')}-{region.get('x2', '?')},{region.get('y2', '?')}"
        f" (mode={payload['adapter_mode']})"
    )


def _execute_validator(context: GraphNodeExecutionContext) -> GraphNodeExecutionResult:
    _validate_input(context)
    output = dict(context.input_payload)

    # BRD local-cut request validation: semantic checks + approval signal
    if context.node.input_schema == "brd_local_cut_request":
        outcome, warnings = _validate_brd_local_cut_request(output)
        output.setdefault("validation_warnings", []).extend(warnings)
        if outcome == "approval_required":
            output["approval_required"] = True
            output["approval_reason"] = "; ".join(warnings)
            output["approval_options"] = [
                {"id": "approve", "label": "Proceed with auto-detected ports"},
                {"id": "reject", "label": "Specify ports manually"},
            ]
            return GraphNodeExecutionResult(
                NodeRunStatus.SUCCEEDED, "approval_required", output, [],
            )

    return GraphNodeExecutionResult(NodeRunStatus.SUCCEEDED, "succeeded", output, [])


def _validate_brd_local_cut_request(payload: dict[str, Any]) -> tuple[str, list[str]]:
    """Validate a BRD local-cut request semantically.

    Returns (outcome, warnings). outcome is "succeeded" or "approval_required".
    """
    warnings: list[str] = []

    layout_file = payload.get("layout_file", "")
    if layout_file and not Path(str(layout_file)).exists():
        warnings.append(f"layout_file not found: {layout_file}")

    signal_nets = payload.get("signal_nets", [])
    if not signal_nets:
        warnings.append("signal_nets is empty")

    region = payload.get("local_cut_region")
    if isinstance(region, dict):
        # Support both x1/y1/x2/y2 and x_min/y_min/x_max/y_max notation
        x1 = region.get("x1", region.get("x_min"))
        y1 = region.get("y1", region.get("y_min"))
        x2 = region.get("x2", region.get("x_max"))
        y2 = region.get("y2", region.get("y_max"))
        if x1 is None or y1 is None or x2 is None or y2 is None:
            warnings.append("local_cut_region missing coordinates")
        elif float(x1) >= float(x2) or float(y1) >= float(y2):
            warnings.append("local_cut_region has non-positive area")

    port_hint = payload.get("uniform_line_port_hint", {})
    if isinstance(port_hint, dict) and port_hint.get("count", 0) < 2:
        warnings.append("port count is less than 2, signal path may be incomplete")

    target_metrics = payload.get("target_metrics", [])
    for metric in target_metrics if isinstance(target_metrics, list) else []:
        if isinstance(metric, dict):
            if metric.get("type") == "rl" and float(metric.get("target_db", 0)) >= 0:
                warnings.append(f"RL target must be negative: {metric['target_db']}")

    outcome = "approval_required" if warnings else "succeeded"
    return outcome, warnings


def _execute_worker(context: GraphNodeExecutionContext) -> GraphNodeExecutionResult:
    _validate_input(context)
    runtime = context.runtime
    store = runtime.store
    bound_job_id = store.get_graph_node_job(
        context.graph_run.graph_run_id,
        context.node.node_id,
        context.run_index,
    )
    if bound_job_id is None:
        already_bound = set(store.list_graph_bound_job_ids(context.graph_run.graph_run_id))
        matching = [
            job
            for job in runtime.list_jobs(context.graph_run.mission_id)
            if job.capability == context.node.capability
            and job.status == JobStatus.QUEUED
            and job.job_id not in already_bound
        ]
        if matching:
            job = matching[0]
        else:
            job = runtime.create_job(
                context.graph_run.mission_id,
                context.node.capability,
                f"graph:{context.graph_run.graph_run_id}:{context.node.node_id}:{context.run_index}",
                _worker_input(context.input_payload),
            )
        bound_job_id = store.bind_graph_node_job(
            context.graph_run.graph_run_id,
            context.node.node_id,
            context.run_index,
            job.job_id,
        )
    result = runtime.execute_job(bound_job_id, context.worker_id)
    if result.status in {JobStatus.FAILED, JobStatus.CANCELED}:
        return GraphNodeExecutionResult(
            NodeRunStatus.FAILED,
            "canceled" if result.status == JobStatus.CANCELED else "failed",
            {},
            list(result.artifact_refs),
            error=None if result.error is None else result.error.to_json_dict(),
        )
    output = dict(result.output_payload)
    output["artifact_refs"] = list(result.artifact_refs)
    evidence_package_id = None
    evidence_summary = output.get("evidence_summary")
    if isinstance(evidence_summary, dict):
        evidence = store.create_evidence_package(
            EvidencePackage.create(
                evidence_package_id=str(uuid4()),
                mission_id=context.graph_run.mission_id,
                producer_kind="node",
                producer_id=context.node_run.node_run_id,
                summary=dict(evidence_summary),
                artifact_refs=list(result.artifact_refs),
                token_budget={
                    "raw_trace_policy": "artifact_only",
                },
            )
        )
        evidence_package_id = evidence.evidence_package_id
    explicit_outcome = output.pop("edge_outcome", None)
    if explicit_outcome:
        outcome = str(explicit_outcome)
    elif isinstance(output.get("approval_required"), dict):
        outcome = "approval_required"
    else:
        outcome = "succeeded"
    return GraphNodeExecutionResult(
        NodeRunStatus.SUCCEEDED,
        outcome,
        output,
        list(result.artifact_refs),
        evidence_package_id=evidence_package_id,
    )


def _execute_scorecard(context: GraphNodeExecutionContext) -> GraphNodeExecutionResult:
    _validate_input(context)
    report = score_mission(
        context.runtime,
        context.graph_run.mission_id,
        template_id=context.graph_run.template_id,
    )
    artifact_refs = [
        artifact_ref
        for job in context.runtime.list_jobs(context.graph_run.mission_id)
        if job.status == JobStatus.SUCCEEDED
        for artifact_ref in job.artifact_refs
    ]
    evidence = context.runtime.store.create_evidence_package(
        EvidencePackage.create(
            evidence_package_id=str(uuid4()),
            mission_id=context.graph_run.mission_id,
            producer_kind="node",
            producer_id=context.node_run.node_run_id,
            summary={"scorecard": report},
            artifact_refs=artifact_refs,
            token_budget={"raw_trace_policy": "artifact_only"},
        )
    )
    status = NodeRunStatus.SUCCEEDED if report["status"] == "passed" else NodeRunStatus.FAILED
    return GraphNodeExecutionResult(
        status,
        report["status"],
        report,
        artifact_refs,
        evidence_package_id=evidence.evidence_package_id,
        error=None if status == NodeRunStatus.SUCCEEDED else {"scorecard_status": report["status"]},
    )


def _execute_approval_gate(context: GraphNodeExecutionContext) -> GraphNodeExecutionResult:
    approval_id = str(
        context.node_run.output_payload.get("approval_id")
        or context.input_payload.get("approval_id")
        or ""
    )
    approval = None
    if approval_id:
        approval = context.runtime.store.get_approval(approval_id)
    else:
        reason = str(
            context.input_payload.get("approval_reason")
            or (
                f"graph_gate:{context.graph_run.graph_run_id}:"
                f"{context.node.node_id}:{context.run_index}"
            )
        )
        options = list(
            context.input_payload.get("approval_options")
            or [
                {"id": "approve", "label": "Approve"},
                {"id": "reject", "label": "Reject"},
            ]
        )
        approval = ApprovalService(context.runtime.store).request_approval(
            context.graph_run.mission_id,
            reason,
            options,
        )
    output = _approval_output(context.input_payload, approval)
    if approval.decision == ApprovalDecision.PENDING:
        return GraphNodeExecutionResult(
            NodeRunStatus.WAITING_APPROVAL,
            "waiting_approval",
            output,
            [],
        )
    if approval.decision == ApprovalDecision.APPROVED:
        return GraphNodeExecutionResult(NodeRunStatus.SUCCEEDED, "approved", output, [])
    return GraphNodeExecutionResult(
        NodeRunStatus.FAILED,
        "rejected",
        output,
        [],
        error={"error_class": "approval_rejected", "approval_id": approval.approval_id},
    )


def _approval_output(input_payload: dict[str, Any], approval) -> dict[str, Any]:
    output = {
        key: value
        for key, value in input_payload.items()
        if key
        not in {
            "_handoffs",
            "approval_reason",
            "approval_options",
        }
    }
    output["approval_id"] = approval.approval_id
    output["decision"] = approval.decision.value
    for option in approval.options:
        if not isinstance(option, dict):
            continue
        for key in ("action_id", "action_digest"):
            if key in option and key not in output:
                output["digest" if key == "action_digest" else key] = option[key]
    return output


def _validate_input(context: GraphNodeExecutionContext) -> None:
    if not context.node.input_schema:
        return
    schema = context.template.handoffs[context.node.input_schema]
    validate_handoff(schema, context.input_payload)


def _validate_output(context: GraphNodeExecutionContext, payload: dict[str, Any]) -> None:
    if not context.node.output_schema:
        return
    schema = context.template.handoffs[context.node.output_schema]
    validate_handoff(schema, payload)


def _worker_input(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "_handoffs"}


def _failed_result(error_class: str, message: str, *, details: dict[str, Any] | None = None):
    return GraphNodeExecutionResult(
        NodeRunStatus.FAILED,
        "failed",
        {},
        [],
        error={
            "error_class": error_class,
            "message": message,
            "details": details or {},
        },
    )


def _normalize_result(value: GraphNodeExecutionResult | dict[str, Any]) -> GraphNodeExecutionResult:
    if isinstance(value, GraphNodeExecutionResult):
        return value
    return GraphNodeExecutionResult(
        status=NodeRunStatus(value["status"]),
        outcome=str(value["outcome"]),
        output_payload=dict(value.get("output_payload") or {}),
        artifact_refs=list(value.get("artifact_refs") or []),
        evidence_package_id=value.get("evidence_package_id"),
        error=value.get("error"),
    )
