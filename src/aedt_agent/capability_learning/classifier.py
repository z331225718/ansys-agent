from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal, Mapping


PromotionKind = Literal["harness", "skill", "workflow"]

_KINDS = {"harness", "skill", "workflow"}
_WORKFLOW_KEYS = {
    "approval_points",
    "branch",
    "branches",
    "budget",
    "exit_condition",
    "fan_out",
    "loop",
    "max_rounds",
    "retry_policy",
}
_WORKFLOW_EVENT = re.compile(r"(?:^|_)(?:branch|handoff|loop|retry|round)(?:_|$)", re.I)
_JUDGMENT_INTENT = re.compile(
    r"\b(?:choose|compare|diagnose|evaluate|inspect|recommend|review|select)\b|"
    r"(?:分析|比较|诊断|评审|推荐|选择)",
    re.I,
)


@dataclass(frozen=True)
class ClassificationDecision:
    kind: PromotionKind
    confidence: str
    reasons: tuple[str, ...]
    signals: dict[str, int | bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "signals": dict(self.signals),
        }


def classify_trace(trace: Mapping[str, Any], requested_kind: str = "auto") -> ClassificationDecision:
    """Classify a sealed trace without treating the trace as executable input."""

    if requested_kind != "auto" and requested_kind not in _KINDS:
        raise ValueError("requested_kind must be auto, harness, skill, or workflow")

    plan = trace.get("plan") if isinstance(trace.get("plan"), Mapping) else {}
    events = trace.get("events") if isinstance(trace.get("events"), list) else []
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    evidence = plan.get("evidence") if isinstance(plan.get("evidence"), list) else []
    approval_count = sum(
        1
        for event in events
        if isinstance(event, Mapping)
        and (event.get("state") == "approved" or "approv" in str(event.get("event", "")).lower())
    )
    workflow_structure = _contains_key(plan, _WORKFLOW_KEYS) or any(
        isinstance(event, Mapping) and _WORKFLOW_EVENT.search(str(event.get("event", "")))
        for event in events
    )
    mutation_count = sum(
        1 for step in steps if isinstance(step, Mapping) and step.get("op") == "set_attr"
    )
    read_only = plan.get("risk") == "read_only" and mutation_count == 0
    judgment_intent = bool(_JUDGMENT_INTENT.search(str(trace.get("intent", ""))))
    signals: dict[str, int | bool] = {
        "workflow_structure": workflow_structure,
        "approval_count": approval_count,
        "step_count": len(steps),
        "mutation_count": mutation_count,
        "evidence_count": len(evidence),
        "read_only": read_only,
        "judgment_intent": judgment_intent,
    }

    if requested_kind != "auto":
        return ClassificationDecision(
            requested_kind,  # type: ignore[arg-type]
            "requested",
            ("reviewer_requested_target_kind",),
            signals,
        )

    if workflow_structure or approval_count > 1:
        reasons = ["stateful_control_flow_detected"]
        if approval_count > 1:
            reasons.append("multiple_approval_points_detected")
        return ClassificationDecision("workflow", "high", tuple(reasons), signals)

    if read_only and (len(steps) > 1 or len(evidence) > 1 or judgment_intent):
        reasons = ["read_only_composite_method"]
        if judgment_intent:
            reasons.append("engineering_judgment_is_primary")
        return ClassificationDecision("skill", "medium", tuple(reasons), signals)

    return ClassificationDecision(
        "harness",
        "high" if len(steps) == 1 else "medium",
        ("bounded_deterministic_operation",),
        signals,
    )


def _contains_key(value: Any, names: set[str]) -> bool:
    if isinstance(value, Mapping):
        if any(str(key) in names for key in value):
            return True
        return any(_contains_key(item, names) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, names) for item in value)
    return False
