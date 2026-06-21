from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from aedt_agent.agent.mission import ErrorClass, JobRecord
from aedt_agent.agent.workers.registry import (
    WorkerContext,
    WorkerReportedError,
)


BRD_GEOMETRY_VALIDATE_CAPABILITY = "brd.geometry.validate"

SUPPORTED_ACTIONS = {
    "anti_pad.enlarge",
    "non_functional_pad.add_or_enlarge",
}

DEFAULT_GEOMETRY_LIMITS = {
    "anti_pad": {
        "max_radius_mil": 22.0,
        "worker_constraints": {"max_diameter": "44mil"},
    },
    "non_functional_pad": {
        "min_radius_mil": 7.875,
        "max_radius_mil": 10.0,
        "worker_constraints": {
            "min_diameter": "15.75mil",
            "max_diameter": "20mil",
        },
    },
}


def build_brd_geometry_validate_job_input(
    *,
    project_path: str | Path,
    actions: list[dict[str, Any]],
    project_copy_mode: str = "working_project",
    geometry_limits: dict[str, Any] | None = None,
    loop_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "project_path": str(project_path),
        "actions": list(actions),
        "project_copy_mode": str(project_copy_mode),
        "geometry_limits": dict(geometry_limits or {}),
        "loop_context": dict(loop_context or {}),
    }


def run_brd_geometry_validate_worker(
    job: JobRecord,
    context: WorkerContext,
) -> dict[str, Any]:
    payload = dict(job.input_payload)
    actions = [dict(item) for item in payload.get("actions") or [] if isinstance(item, dict)]
    if not actions:
        raise WorkerReportedError(
            ErrorClass.INVALID_INPUT.value,
            "brd.geometry.validate requires at least one action",
            retryable=False,
        )

    limits = _geometry_limits(payload)
    checks: list[dict[str, Any]] = []
    approval_issues: list[dict[str, Any]] = []
    validated_actions: list[dict[str, Any]] = []

    if len(actions) == 1:
        _add_check(
            checks,
            "single_small_action",
            "passed",
            "one model edit action is proposed",
        )
    else:
        issue = _issue(
            "single_small_action",
            "approval_required",
            "more than one model edit action was proposed for one iteration",
        )
        checks.append(issue)
        approval_issues.append(issue)

    for index, action in enumerate(actions):
        action_type = str(action.get("action_type") or "").strip()
        if action_type not in SUPPORTED_ACTIONS:
            raise WorkerReportedError(
                ErrorClass.INVALID_INPUT.value,
                f"unsupported geometry action_type: {action_type}",
                retryable=False,
                details={"action_index": index, "action_type": action_type},
            )

        if action_type == "anti_pad.enlarge":
            action_checks, action_issues = _validate_antipad_action(
                index,
                action,
                limits,
            )
        else:
            action_checks, action_issues = _validate_nfp_action(
                index,
                action,
                limits,
            )
        checks.extend(action_checks)
        approval_issues.extend(action_issues)
        validated_actions.append(_with_worker_constraints(action, limits))

    status = "approval_required" if approval_issues else "succeeded"
    loop_context = _loop_context(payload)
    manifest_path = _manifest_path(context, payload)
    validation = {
        "status": status,
        "check_count": len(checks),
        "approval_issue_count": len(approval_issues),
        "checks": checks,
        "approval_issues": approval_issues,
        "raw_project": "artifact_only",
    }
    manifest = {
        "version": 1,
        "capability": BRD_GEOMETRY_VALIDATE_CAPABILITY,
        "job_id": job.job_id,
        "mission_id": job.mission_id,
        "input": {
            "project_path": str(payload.get("project_path") or ""),
            "action_count": len(actions),
        },
        "summary": validation,
    }
    _write_json(manifest_path, manifest)
    _append_unique(
        loop_context,
        "geometry_validation_manifest_paths",
        str(manifest_path),
    )
    loop_context["last_geometry_validation_manifest_path"] = str(manifest_path)
    loop_context["last_geometry_validation_status"] = status

    output = {
        **payload,
        "status": status,
        "actions": validated_actions,
        "geometry_validation": validation,
        "geometry_validation_manifest": str(manifest_path),
        "loop_context": loop_context,
        "evidence_summary": {
            "status": f"geometry_validation_{status}",
            "check_count": len(checks),
            "approval_issue_count": len(approval_issues),
            "raw_project": "artifact_only",
            "artifact_refs": [str(manifest_path)],
        },
        "artifact_refs": [str(manifest_path)],
    }
    if approval_issues:
        approval_options = [
            {"id": "approve", "label": "Approve validated edit"},
            {"id": "reject", "label": "Reject edit"},
        ]
        approval_reason = _approval_reason(approval_issues)
        output["edge_outcome"] = "approval_required"
        output["approval_reason"] = approval_reason
        output["approval_options"] = approval_options
        output["approval_required"] = {
            "reason": approval_reason,
            "issues": approval_issues,
            "options": approval_options,
        }
    return output


def _validate_antipad_action(
    index: int,
    action: Mapping[str, Any],
    limits: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    prefix = f"action_{index}_anti_pad"
    anti_limits = dict(limits.get("anti_pad") or {})
    max_radius = float(anti_limits.get("max_radius_mil") or 22.0)

    radius = _target_radius_mil(action)
    if radius is None:
        _approval(
            checks,
            issues,
            f"{prefix}_radius_present",
            "anti-pad action must provide target_radius or target_diameter",
        )
    elif radius <= 0:
        raise WorkerReportedError(
            ErrorClass.INVALID_INPUT.value,
            "anti-pad target radius must be positive",
            retryable=False,
            details={"action_index": index, "radius_mil": radius},
        )
    elif radius > max_radius:
        _approval(
            checks,
            issues,
            f"{prefix}_radius_limit",
            f"anti-pad radius {radius:g}mil exceeds max {max_radius:g}mil",
        )
    else:
        _add_check(
            checks,
            f"{prefix}_radius_limit",
            "passed",
            f"anti-pad radius {radius:g}mil is within max {max_radius:g}mil",
        )

    if _parameter_name(action):
        _add_check(
            checks,
            f"{prefix}_radius_parameterized",
            "passed",
            "anti-pad circular void radius is parameterized",
        )
    else:
        _approval(
            checks,
            issues,
            f"{prefix}_radius_parameterized",
            "anti-pad circular void radius should be parameterized",
        )

    layers = _layers(action)
    if not layers:
        _approval(
            checks,
            issues,
            f"{prefix}_layers_present",
            "anti-pad action must name target reference/power layers",
        )
    for layer in layers:
        if _is_reference_or_power_layer(layer) or bool(action.get("allow_non_plane_antipad", False)):
            _add_check(
                checks,
                f"{prefix}_layer_{_check_token(layer)}",
                "passed",
                f"target layer {layer} is allowed for anti-pad voids",
            )
        else:
            _approval(
                checks,
                issues,
                f"{prefix}_layer_{_check_token(layer)}",
                (
                    "anti-pad voids should target plane shapes, not routing "
                    f"layers without explicit override: {layer}"
                ),
            )

    if _has_shape_evidence(action):
        _add_check(
            checks,
            f"{prefix}_shape_evidence",
            "passed",
            "plane shape evidence is present for anti-pad void placement",
        )
    else:
        _approval(
            checks,
            issues,
            f"{prefix}_shape_evidence",
            "anti-pad edit must identify plane_shape_ids or equivalent shape evidence",
        )

    center_count = _center_count(action)
    if center_count > 0:
        _add_check(
            checks,
            f"{prefix}_center_source",
            "passed",
            "via centers are grounded in padstack instances or explicit centers",
        )
    else:
        _approval(
            checks,
            issues,
            f"{prefix}_center_source",
            "anti-pad action must identify the parasitic center via padstack instances or via_centers",
        )

    if _truthy(action.get("bridge_between_vias")) or _truthy(action.get("add_bridge_rectangle")):
        checks_, issues_ = _validate_antipad_bridge(index, action, center_count)
        checks.extend(checks_)
        issues.extend(issues_)

    return checks, issues


def _validate_antipad_bridge(
    index: int,
    action: Mapping[str, Any],
    center_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    prefix = f"action_{index}_anti_pad_bridge"
    bridge_center_count = _bridge_center_count(action, center_count)

    if bridge_center_count == 2:
        _add_check(
            checks,
            f"{prefix}_center_count",
            "passed",
            "bridge rectangle has exactly two bridge centers",
        )
    else:
        _approval(
            checks,
            issues,
            f"{prefix}_center_count",
            "bridge rectangle requires exactly two bridge centers",
        )

    if _parameter_name(action):
        _add_check(
            checks,
            f"{prefix}_parameterized",
            "passed",
            "bridge rectangle follows the parameterized anti-pad radius",
        )
    else:
        _approval(
            checks,
            issues,
            f"{prefix}_parameterized",
            "bridge rectangle requires parameter_name so its +/- radius edges are parameterized",
        )
    return checks, issues


def _validate_nfp_action(
    index: int,
    action: Mapping[str, Any],
    limits: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    prefix = f"action_{index}_nfp"
    nfp_limits = dict(limits.get("non_functional_pad") or {})
    min_radius = float(nfp_limits.get("min_radius_mil") or 7.875)
    max_radius = float(nfp_limits.get("max_radius_mil") or 10.0)

    implementation = str(
        _first_present(action, "implementation", "edit_mode") or "shape"
    ).casefold()
    if implementation in {"shape", "circle_shape", "primitive"}:
        _add_check(
            checks,
            f"{prefix}_shape_implementation",
            "passed",
            "non-functional pad will be added as signal circle shapes",
        )
    else:
        _approval(
            checks,
            issues,
            f"{prefix}_shape_implementation",
            "non-functional pads should be created as circle shapes; padstack edits are known to be removed by AEDT",
        )

    radius = _target_radius_mil(action)
    if radius is None:
        _approval(
            checks,
            issues,
            f"{prefix}_radius_present",
            "non-functional pad action must provide target_radius or target_diameter",
        )
    elif radius <= 0:
        raise WorkerReportedError(
            ErrorClass.INVALID_INPUT.value,
            "non-functional pad target radius must be positive",
            retryable=False,
            details={"action_index": index, "radius_mil": radius},
        )
    elif radius < min_radius or radius > max_radius:
        _approval(
            checks,
            issues,
            f"{prefix}_radius_window",
            (
                f"non-functional pad radius {radius:g}mil is outside "
                f"[{min_radius:g}, {max_radius:g}]mil"
            ),
        )
    else:
        _add_check(
            checks,
            f"{prefix}_radius_window",
            "passed",
            (
                f"non-functional pad radius {radius:g}mil is within "
                f"[{min_radius:g}, {max_radius:g}]mil"
            ),
        )

    if _parameter_name(action):
        _add_check(
            checks,
            f"{prefix}_radius_parameterized",
            "passed",
            "non-functional pad radius is parameterized",
        )
    else:
        _approval(
            checks,
            issues,
            f"{prefix}_radius_parameterized",
            "non-functional pad radius should be parameterized",
        )

    if _layers(action):
        _add_check(
            checks,
            f"{prefix}_layers_present",
            "passed",
            "non-functional pad target layer list is present",
        )
    else:
        _approval(
            checks,
            issues,
            f"{prefix}_layers_present",
            "non-functional pad action must name target layers",
        )

    center_count = _center_count(action)
    if center_count > 0:
        _add_check(
            checks,
            f"{prefix}_center_source",
            "passed",
            "non-functional pad centers are grounded in padstack instances or explicit centers",
        )
    else:
        _approval(
            checks,
            issues,
            f"{prefix}_center_source",
            "non-functional pad action must identify through/blind/buried via centers",
        )

    if action.get("via_centers") and not action.get("net_names"):
        _approval(
            checks,
            issues,
            f"{prefix}_net_names",
            "explicit via_centers require one signal net name per non-functional pad",
        )
    else:
        _add_check(
            checks,
            f"{prefix}_net_names",
            "passed",
            "signal net evidence is present or can be resolved from padstack instances",
        )
    return checks, issues


def _with_worker_constraints(
    action: Mapping[str, Any],
    limits: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(action)
    action_type = str(result.get("action_type") or "")
    if action_type == "anti_pad.enlarge":
        limit_key = "anti_pad"
    else:
        limit_key = "non_functional_pad"
    worker_constraints = dict(
        (dict(limits.get(limit_key) or {}).get("worker_constraints") or {})
    )
    constraints = dict(result.get("constraints") or {})
    for key, value in worker_constraints.items():
        constraints.setdefault(key, value)
    if constraints:
        result["constraints"] = constraints
    return result


def _geometry_limits(payload: Mapping[str, Any]) -> dict[str, Any]:
    limits = json.loads(json.dumps(DEFAULT_GEOMETRY_LIMITS))
    supplied = payload.get("geometry_limits")
    if not isinstance(supplied, Mapping):
        constraints = payload.get("constraints")
        supplied = (
            constraints.get("geometry_limits")
            if isinstance(constraints, Mapping)
            else {}
        )
    if isinstance(supplied, Mapping):
        for key, value in supplied.items():
            if not isinstance(value, Mapping):
                continue
            merged = dict(limits.get(str(key)) or {})
            merged.update(dict(value))
            limits[str(key)] = merged
    return limits


def _target_radius_mil(action: Mapping[str, Any]) -> float | None:
    radius_spec = _first_present(action, "target_radius", "void_radius", "radius")
    if radius_spec is not None:
        return _dimension_to_mil(radius_spec)
    diameter_spec = _first_present(
        action,
        "target_diameter",
        "void_diameter",
        "diameter",
        "new_diameter",
    )
    if diameter_spec is not None:
        return _dimension_to_mil(diameter_spec) / 2.0
    return None


def _dimension_to_mil(value: Any) -> float:
    if isinstance(value, Mapping):
        raw = value.get("value")
        unit = str(value.get("unit") or "mil")
        return _convert_to_mil(float(raw), unit)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    match = re.fullmatch(r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([A-Za-zµ]*)", text)
    if not match:
        raise WorkerReportedError(
            ErrorClass.INVALID_INPUT.value,
            f"cannot parse dimension: {value}",
            retryable=False,
        )
    number = float(match.group(1))
    unit = match.group(2) or "mil"
    return _convert_to_mil(number, unit)


def _convert_to_mil(number: float, unit: str) -> float:
    normalized = unit.strip().casefold()
    if normalized in {"mil", "mils"}:
        return number
    if normalized in {"in", "inch", "inches"}:
        return number * 1000.0
    if normalized == "mm":
        return number * 39.37007874015748
    if normalized in {"um", "µm"}:
        return number * 0.03937007874015748
    if normalized == "m":
        return number * 39370.07874015748
    raise WorkerReportedError(
        ErrorClass.INVALID_INPUT.value,
        f"unsupported dimension unit: {unit}",
        retryable=False,
    )


def _layers(action: Mapping[str, Any]) -> list[str]:
    value = action.get("layers")
    if value is None and isinstance(action.get("target"), Mapping):
        value = action["target"].get("layers")
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value or [] if str(item).strip()]


def _center_count(action: Mapping[str, Any]) -> int:
    for key in ("center_padstack_instance_ids", "padstack_instance_ids"):
        value = action.get(key)
        if isinstance(value, list):
            return len(value)
    centers = action.get("via_centers")
    if isinstance(centers, list):
        return len(centers)
    return 0


def _bridge_center_count(
    action: Mapping[str, Any],
    fallback_count: int,
) -> int:
    for key in (
        "bridge_center_padstack_instance_ids",
        "bridge_padstack_instance_ids",
        "bridge_center_instance_ids",
    ):
        value = action.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            return len(value)
        if isinstance(value, (str, int)):
            return 1
    for key in ("bridge_via_centers", "bridge_centers"):
        value = action.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            return 1
        if isinstance(value, list):
            return len(value)
    return fallback_count


def _has_shape_evidence(action: Mapping[str, Any]) -> bool:
    for key in ("plane_shape_ids", "selected_shape_ids"):
        value = action.get(key)
        if isinstance(value, list) and value:
            return True
    target = action.get("target")
    if isinstance(target, Mapping):
        value = target.get("plane_shape_ids") or target.get("selected_shape_ids")
        if isinstance(value, list) and value:
            return True
    if str(action.get("shape_presence_check") or "").casefold() == "passed":
        return True
    evidence = action.get("shape_evidence")
    return bool(evidence)


def _parameter_name(action: Mapping[str, Any]) -> str:
    value = _first_present(
        action,
        "parameter_name",
        "radius_parameter",
        "void_radius_parameter",
    )
    return str(value or "").strip()


def _is_reference_or_power_layer(layer: str) -> bool:
    normalized = layer.upper()
    return any(
        token in normalized
        for token in ("GND", "GROUND", "VCC", "VDD", "VSS", "PWR", "POWER")
    )


def _first_present(action: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key not in action:
            continue
        value = action[key]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _approval(
    checks: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    check_id: str,
    message: str,
) -> None:
    issue = _issue(check_id, "approval_required", message)
    checks.append(issue)
    issues.append(issue)


def _issue(check_id: str, status: str, message: str) -> dict[str, Any]:
    return {"id": check_id, "status": status, "message": message}


def _add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    status: str,
    message: str,
) -> None:
    checks.append(_issue(check_id, status, message))


def _manifest_path(context: WorkerContext, payload: Mapping[str, Any]) -> Path:
    base = (
        Path(context.artifacts_dir)
        if context.artifacts_dir
        else Path(str(payload.get("artifact_dir") or "."))
    )
    base.mkdir(parents=True, exist_ok=True)
    return base / "geometry_validation_manifest.json"


def _loop_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = payload.get("loop_context")
    return dict(value) if isinstance(value, dict) else {}


def _append_unique(payload: dict[str, Any], key: str, value: str) -> None:
    values = list(payload.get(key) or [])
    if value and value not in values:
        values.append(value)
    payload[key] = values


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _approval_reason(issues: list[dict[str, Any]]) -> str:
    first = issues[0]["message"] if issues else "geometry validation review"
    extra = len(issues) - 1
    if extra <= 0:
        return f"geometry_validation:{first}"
    return f"geometry_validation:{first}; plus {extra} more issue(s)"


def _check_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return token or "layer"
