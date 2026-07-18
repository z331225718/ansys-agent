from __future__ import annotations

import hashlib
import inspect
import json
import re
from typing import Any


def build_preview(
    app: Any,
    validation: dict[str, Any],
    *,
    target_identity: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = validation["plan"]
    evidence_bindings = validation.get("evidence_bindings")
    if not isinstance(evidence_bindings, dict):
        raise ValueError("validated exploration plan is missing evidence bindings")
    before = _preflight(app, plan, evidence_bindings)
    binding = {
        "plan_digest": validation["plan_digest"],
        "policy_digest": validation["policy_digest"],
        "target": target_identity,
        "before": before,
        "evidence": [
            {
                "query_id": item["query_id"],
                "package": item["package"],
                "package_version": item["package_version"],
                "snippet_digest": item["snippet_digest"],
            }
            for item in plan["evidence"]
        ],
    }
    snapshot_digest = _digest(binding)
    preview_id = "explore-preview-" + snapshot_digest[:24]
    public = {
        "preview_id": preview_id,
        "snapshot_digest": snapshot_digest,
        "plan_digest": validation["plan_digest"],
        "policy_digest": validation["policy_digest"],
        "risk": plan["risk"],
        "target": target_identity,
        "intent": plan["intent"],
        "mutations": [
            {"step_id": step["id"], "path": step["path"], "new_value": step["value"], "old_value": before[step["id"]]}
            for step in plan["steps"]
            if step["op"] == "set_attr"
        ],
        "readback": plan["readback"],
        "rollback_strategy": validation["rollback_strategy"],
        "approval_required": plan["risk"] == "reversible_edit",
        "project_saved": False,
    }
    internal = {
        "kind": "exploration",
        "preview_id": preview_id,
        "snapshot_digest": snapshot_digest,
        "validation": validation,
        "target_identity": target_identity,
        "before": before,
    }
    _bounded_result(public)
    return public, internal


def apply_preview(app: Any, state: dict[str, Any]) -> dict[str, Any]:
    validation = state["validation"]
    plan = validation["plan"]
    evidence_bindings = validation.get("evidence_bindings")
    if not isinstance(evidence_bindings, dict):
        raise ValueError("validated exploration plan is missing evidence bindings")
    current = _preflight(app, plan, evidence_bindings)
    binding = {
        "plan_digest": validation["plan_digest"],
        "policy_digest": validation["policy_digest"],
        "target": state["target_identity"],
        "before": current,
        "evidence": [
            {
                "query_id": item["query_id"],
                "package": item["package"],
                "package_version": item["package_version"],
                "snippet_digest": item["snippet_digest"],
            }
            for item in plan["evidence"]
        ],
    }
    if _digest(binding) != state["snapshot_digest"]:
        return {
            "status": "stale_preview",
            "preview_id": state["preview_id"],
            "mutation_applied": False,
            "project_saved": False,
        }

    applied = []
    try:
        for step in plan["steps"]:
            bindings = _bindings_for(evidence_bindings, "steps", step["id"])
            if step["op"] == "set_attr":
                _set_path(app, step["path"], step["value"], bindings)
                applied.append(step)
            elif step["op"] == "read_attr":
                _resolve_path(app, step["path"], bindings)
            else:
                _call_path(app, step["path"], step["args"], step["kwargs"], bindings)
        readback = [
            _evaluate_check(
                app,
                check,
                _bindings_for(evidence_bindings, "readback", check["id"]),
            )
            for check in plan["readback"]
        ]
        failures = [item for item in readback if not item["passed"]]
        if failures:
            raise RuntimeError(f"readback failed: {failures[0]['id']}")
    except Exception as exc:
        rollback = _rollback(app, applied, state["before"], evidence_bindings)
        result = {
            "status": "rolled_back" if rollback["verified"] else "rollback_failed",
            "preview_id": state["preview_id"],
            "mutation_applied": bool(applied),
            "error": {"code": "exploration_execution_failed", "message": str(exc)[:1000]},
            "rollback": rollback,
            "project_saved": False,
        }
        _bounded_result(result)
        return result

    result = {
        "status": "verified",
        "preview_id": state["preview_id"],
        "mutation_count": len(applied),
        "readback": readback,
        "rollback_available": bool(applied),
        "project_dirty": bool(applied),
        "project_saved": False,
    }
    _bounded_result(result)
    return result


def _preflight(
    app: Any,
    plan: dict[str, Any],
    evidence_bindings: dict[str, Any],
) -> dict[str, Any]:
    observations = {}
    for step in plan["steps"]:
        bindings = _bindings_for(evidence_bindings, "steps", step["id"])
        if step["op"] in {"read_attr", "set_attr"}:
            value = _resolve_path(app, step["path"], bindings)
        else:
            value = _inspect_call_path(app, step["path"], bindings)
        observations[step["id"]] = _safe_json(value)
    for check in plan["readback"]:
        bindings = _bindings_for(evidence_bindings, "readback", check["id"])
        parent, member = _resolve_bound_parent(app, check["path"], bindings)
        observations[f"readback:{check['id']}"] = {
            "owner_type": type(parent).__name__,
            "member": member,
        }
    _bounded_result(observations)
    return observations


def _resolve_path(root: Any, path: str, evidence_bindings: list[dict[str, str]]) -> Any:
    parent, member = _resolve_bound_parent(root, path, evidence_bindings)
    if isinstance(parent, dict):
        if member not in parent:
            raise AttributeError(f"mapping has no key {member}")
        return parent[member]
    return getattr(parent, member)


def _resolve_parent(root: Any, path: str) -> tuple[Any, str]:
    segments = path.split(".")
    parent = root
    for segment in segments[:-1]:
        parent = parent[segment] if isinstance(parent, dict) else getattr(parent, segment)
    return parent, segments[-1]


def _set_path(
    root: Any,
    path: str,
    value: Any,
    evidence_bindings: list[dict[str, str]],
) -> None:
    parent, member = _resolve_bound_parent(root, path, evidence_bindings)
    if isinstance(parent, dict):
        parent[member] = value
    else:
        setattr(parent, member, value)


def _call_path(
    root: Any,
    path: str,
    args: list[Any],
    kwargs: dict[str, Any],
    evidence_bindings: list[dict[str, str]],
) -> Any:
    value = _resolve_path(root, path, evidence_bindings)
    if not callable(value):
        raise TypeError(f"operation path is not callable: {path}")
    return value(*args, **kwargs)


def _evaluate_check(
    app: Any,
    check: dict[str, Any],
    evidence_bindings: list[dict[str, str]],
) -> dict[str, Any]:
    actual = _safe_json(_resolve_path(app, check["path"], evidence_bindings))
    expected = check["expected"]
    operator = check["operator"]
    if operator == "equals":
        passed = actual == expected
    elif operator == "not_equals":
        passed = actual != expected
    elif operator == "contains":
        try:
            passed = expected in actual
        except TypeError:
            passed = False
    else:
        passed = bool(actual)
    return {"id": check["id"], "path": check["path"], "operator": operator, "expected": expected, "actual": actual, "passed": passed}


def _rollback(
    app: Any,
    applied: list[dict[str, Any]],
    before: dict[str, Any],
    evidence_bindings: dict[str, Any],
) -> dict[str, Any]:
    restored = []
    errors = []
    for step in reversed(applied):
        try:
            bindings = _bindings_for(evidence_bindings, "steps", step["id"])
            _set_path(app, step["path"], before[step["id"]], bindings)
            restored.append(step["id"])
        except Exception as exc:
            errors.append({"step_id": step["id"], "message": str(exc)[:500]})
    checks = []
    for step in applied:
        try:
            bindings = _bindings_for(evidence_bindings, "steps", step["id"])
            actual = _safe_json(_resolve_path(app, step["path"], bindings))
            checks.append({"step_id": step["id"], "restored": actual == before[step["id"]]})
        except Exception:
            checks.append({"step_id": step["id"], "restored": False})
    return {
        "attempted": bool(applied),
        "restored_steps": restored,
        "checks": checks,
        "errors": errors,
        "verified": not errors and all(item["restored"] for item in checks),
    }


def _inspect_call_path(
    root: Any,
    path: str,
    evidence_bindings: list[dict[str, str]],
) -> dict[str, Any]:
    """Validate a call target without invoking it during preview or stale checks."""
    parent, member = _resolve_bound_parent(root, path, evidence_bindings)
    if isinstance(parent, dict):
        if member not in parent or not callable(parent[member]):
            raise TypeError(f"operation path is not callable: {path}")
    else:
        try:
            descriptor = inspect.getattr_static(parent, member)
        except AttributeError as exc:
            raise AttributeError(f"object has no member {member}") from exc
        if not callable(descriptor) and not isinstance(descriptor, (staticmethod, classmethod)):
            raise TypeError(f"operation path is not a declared callable: {path}")
    return {"callable": True, "owner_type": type(parent).__name__, "member": member}


def _resolve_bound_parent(
    root: Any,
    path: str,
    evidence_bindings: list[dict[str, str]],
) -> tuple[Any, str]:
    parent, member = _resolve_parent(root, path)
    runtime_owners = {
        _normalized_owner(item.__name__)
        for item in getattr(type(parent), "__mro__", (type(parent),))
    }
    evidenced_owners = {
        _normalized_owner(str(item.get("owner", "")))
        for item in evidence_bindings
        if str(item.get("member", "")).lower() == member.lower()
    }
    if not runtime_owners.intersection(evidenced_owners):
        expected = sorted(str(item.get("owner", "")) for item in evidence_bindings)
        raise ValueError(
            f"API evidence owner {expected} does not match runtime owner {type(parent).__name__} for {path}"
        )
    return parent, member


def _bindings_for(
    evidence_bindings: dict[str, Any],
    section: str,
    item_id: str,
) -> list[dict[str, str]]:
    section_value = evidence_bindings.get(section)
    bindings = section_value.get(item_id) if isinstance(section_value, dict) else None
    if not isinstance(bindings, list) or not bindings:
        raise ValueError(f"validated exploration item has no API evidence binding: {section}.{item_id}")
    return bindings


def _normalized_owner(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _safe_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in list(value.items())[:1000]}
    if isinstance(value, (list, tuple)):
        return [_safe_json(item) for item in value[:1000]]
    for name in ("name", "id"):
        attribute = getattr(value, name, None)
        if isinstance(attribute, (str, int, float, bool)):
            return {"object_type": type(value).__name__, name: attribute}
    return {"object_type": type(value).__name__}


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _bounded_result(value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8")
    if len(encoded) > 1024 * 1024:
        raise ValueError("exploration result exceeds 1 MiB")
