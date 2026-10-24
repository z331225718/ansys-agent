from __future__ import annotations

import math
import re
from typing import Any

from aedt_agent.harness.common import (
    LiveBackendError,
    bounded_integer,
    digest as _digest,
    normalized_expression,
    required as _required,
    safe_attribute as _safe_attribute,
    simulation_running as _simulation_running,
)


def variable_product(arguments: dict[str, Any]) -> str:
    product = str(arguments.get("product") or "").strip().casefold()
    if product not in {"hfss", "layout"}:
        raise LiveBackendError("product must be hfss or layout")
    return product


def variable_name(arguments: dict[str, Any]) -> str:
    name = _required(arguments, "variable_name")
    if not re.fullmatch(r"\$?[A-Za-z_][A-Za-z0-9_]*", name):
        raise LiveBackendError("variable_name must be a valid AEDT identifier")
    return name


def variable_expression_input(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise LiveBackendError(f"{field} must be an AEDT expression string")
    expression = value.strip()
    if not expression:
        raise LiveBackendError(f"{field} must not be empty")
    if len(expression) > 512:
        raise LiveBackendError(f"{field} exceeds the maximum length of 512 characters")
    if re.search(r"[\x00-\x1f\x7f]", expression):
        raise LiveBackendError(f"{field} must not contain control characters")
    return expression


def normalize_variable_batch(value: Any, max_variables: int) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise LiveBackendError("variables must be a non-empty list")
    if len(value) > max_variables:
        raise LiveBackendError(f"variables exceeds max_variables={max_variables}")
    normalized = []
    seen: dict[str, str] = {}
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise LiveBackendError(f"variables[{index}] must be an object")
        unsupported = sorted(set(raw).difference({"name", "expression"}))
        if unsupported:
            raise LiveBackendError(
                f"variables[{index}] contains unsupported field: {unsupported[0]}"
            )
        name = variable_name({"variable_name": raw.get("name")})
        folded = name.casefold()
        if folded in seen:
            raise LiveBackendError(
                f"variables contains duplicate case-insensitive name: {name}"
            )
        seen[folded] = name
        normalized.append(
            {
                "name": name,
                "expression": variable_expression_input(
                    raw.get("expression"),
                    f"variables[{index}].expression",
                ),
            }
        )
    return normalized


def variable_expression(value: Any) -> str:
    expression = getattr(value, "expression", None)
    if expression is None:
        expression = getattr(value, "value", value)
    return str(expression)


def variable_records(app: Any) -> list[dict[str, str]]:
    return [
        {
            "name": str(name),
            "expression": variable_expression(value),
            "scope": "project" if str(name).startswith("$") else "design",
        }
        for name, value in sorted(
            dict(getattr(app.variable_manager, "variables", {}) or {}).items(),
            key=lambda item: str(item[0]),
        )
    ]


def variable_state(app: Any, product: str) -> dict[str, Any]:
    if _simulation_running(app):
        raise LiveBackendError("cannot change AEDT variables while a simulation is running")
    design_type = str(_safe_attribute(app, "design_type") or "").strip()
    normalized_type = re.sub(r"[\s_-]+", "", design_type).casefold()
    expected = "hfss" if product == "hfss" else "hfss3dlayoutdesign"
    if normalized_type != expected:
        raise LiveBackendError(
            f"product {product} does not match active design type: {design_type or 'unavailable'}"
        )
    return {
        "design_type": design_type,
        "solution_type": str(_safe_attribute(app, "solution_type") or "").strip(),
        "variables": variable_records(app),
    }


def variable_changes(
    before_records: list[dict[str, str]],
    requested: list[dict[str, str]],
) -> list[dict[str, Any]]:
    by_name = {item["name"]: item for item in before_records}
    by_folded = {item["name"].casefold(): item["name"] for item in before_records}
    changes = []
    for spec in requested:
        name = spec["name"]
        conflicting = by_folded.get(name.casefold())
        if conflicting is not None and conflicting != name:
            raise LiveBackendError(
                f"AEDT variable name differs only by case from existing variable: {conflicting}"
            )
        before = by_name.get(name)
        existed = before is not None
        before_expression = before["expression"] if before else None
        if existed and variable_expressions_equal(before_expression, spec["expression"]):
            action = "noop"
        else:
            action = "update" if existed else "create"
        changes.append(
            {
                "name": name,
                "scope": "project" if name.startswith("$") else "design",
                "action": action,
                "existed": existed,
                "before_expression": before_expression,
                "after_expression": spec["expression"],
            }
        )
    return changes


def variable_expressions_equal(actual: Any, expected: Any) -> bool:
    actual_text = str(actual).strip()
    expected_text = str(expected).strip()
    if normalized_expression(actual_text) == normalized_expression(expected_text):
        return True
    quantity_pattern = re.compile(
        r"([+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?)\s*([A-Za-z]*)"
    )
    actual_match = quantity_pattern.fullmatch(actual_text)
    expected_match = quantity_pattern.fullmatch(expected_text)
    if not actual_match or not expected_match:
        return False
    if actual_match.group(2).casefold() != expected_match.group(2).casefold():
        return False
    return math.isclose(
        float(actual_match.group(1)),
        float(expected_match.group(1)),
        rel_tol=1e-12,
        abs_tol=1e-15,
    )


def change_existing_variable_expression(app: Any, name: str, expression: str) -> None:
    variables = dict(getattr(app.variable_manager, "variables", {}) or {})
    variable = variables.get(name)
    if variable is None:
        raise LiveBackendError(f"AEDT variable disappeared before update: {name}")
    design_type = str(_safe_attribute(app, "design_type") or "").strip()
    is_layout = re.sub(r"[\s_-]+", "", design_type).casefold() == "hfss3dlayoutdesign"
    if name.startswith("$"):
        target = getattr(app, "oproject", None) or getattr(app, "_oproject", None)
        tab_name = "ProjectVariableTab"
        prop_server = "ProjectVariables"
    else:
        target = getattr(app, "odesign", None) or getattr(app, "_odesign", None)
        circuit_parameter = False
        try:
            circuit_parameter = bool(variable.is_circuit_parameter)
        except Exception:
            circuit_parameter = False
        if is_layout and circuit_parameter:
            tab_name = "DefinitionParameterTab"
        else:
            tab_name = "LocalVariableTab"
        if is_layout:
            get_name = getattr(target, "GetName", None)
            if not callable(get_name):
                raise LiveBackendError("AEDT layout design identifier is unavailable")
            prop_server = "Instance:" + str(get_name())
        else:
            prop_server = "LocalVariables"
    change_property = getattr(target, "ChangeProperty", None)
    if not callable(change_property):
        updated = app.variable_manager.set_variable(name, expression, sweep=True)
        if updated is False:
            raise LiveBackendError(f"failed to update AEDT variable: {name}")
        return
    change_property(
        [
            "NAME:AllTabs",
            [
                f"NAME:{tab_name}",
                ["NAME:PropServers", prop_server],
                ["NAME:ChangedProps", [f"NAME:{name}", "Value:=", expression]],
            ],
        ]
    )


def verify_variable_batch_readback(
    before_records: list[dict[str, str]],
    after_records: list[dict[str, str]],
    changes: list[dict[str, Any]],
) -> None:
    before_by_name = {item["name"]: item for item in before_records}
    after_by_name = {item["name"]: item for item in after_records}
    change_by_name = {item["name"]: item for item in changes}
    expected_names = set(before_by_name) | {
        item["name"] for item in changes if item["action"] == "create"
    }
    if set(after_by_name) != expected_names:
        raise LiveBackendError("unexpected AEDT variable inventory change")
    for name, record in after_by_name.items():
        change = change_by_name.get(name)
        expected_expression = (
            change["after_expression"] if change else before_by_name[name]["expression"]
        )
        if not variable_expressions_equal(record["expression"], expected_expression):
            raise LiveBackendError(f"AEDT variable batch readback failed: {name}")


def rollback_variable_changes(
    app: Any,
    *,
    attempted: list[dict[str, Any]],
    before_records: list[dict[str, str]],
) -> dict[str, Any]:
    errors = []
    for change in reversed(attempted):
        if not change["existed"]:
            continue
        try:
            change_existing_variable_expression(
                app,
                change["name"],
                change["before_expression"],
            )
        except Exception as exc:
            errors.append(f"restore {change['name']}: {type(exc).__name__}: {exc}")
    for change in reversed(attempted):
        if change["existed"]:
            continue
        try:
            current_names = {item["name"] for item in variable_records(app)}
            if change["name"] in current_names:
                app.variable_manager.delete_variable(change["name"])
        except Exception as exc:
            errors.append(f"delete {change['name']}: {type(exc).__name__}: {exc}")
    try:
        after_records = variable_records(app)
    except Exception as exc:
        after_records = []
        errors.append(f"readback: {type(exc).__name__}: {exc}")
    complete = variable_record_sets_equal(after_records, before_records)
    return {
        "complete": complete,
        "attempted_names": [item["name"] for item in attempted],
        "remaining_variables": [item["name"] for item in after_records],
        "errors": errors,
    }


def variable_record_sets_equal(
    actual: list[dict[str, str]],
    expected: list[dict[str, str]],
) -> bool:
    actual_by_name = {item["name"]: item for item in actual}
    expected_by_name = {item["name"]: item for item in expected}
    return set(actual_by_name) == set(expected_by_name) and all(
        actual_by_name[name].get("scope") == record.get("scope")
        and variable_expressions_equal(
            actual_by_name[name].get("expression"),
            record.get("expression"),
        )
        for name, record in expected_by_name.items()
    )


def apply_variable_changes(
    app: Any,
    *,
    changes: list[dict[str, Any]],
    before_records: list[dict[str, str]],
) -> dict[str, Any]:
    attempted: list[dict[str, Any]] = []
    try:
        for change in changes:
            if change["action"] == "noop":
                continue
            attempted.append(change)
            if change["existed"]:
                change_existing_variable_expression(
                    app,
                    change["name"],
                    change["after_expression"],
                )
            else:
                updated = app.variable_manager.set_variable(
                    change["name"],
                    change["after_expression"],
                    sweep=True,
                )
                if updated is False:
                    raise LiveBackendError(
                        f"failed to create AEDT variable: {change['name']}"
                    )
            current_by_name = {item["name"]: item for item in variable_records(app)}
            readback = current_by_name.get(change["name"])
            if readback is None or not variable_expressions_equal(
                readback["expression"],
                change["after_expression"],
            ):
                raise LiveBackendError(
                    f"AEDT variable readback verification failed: {change['name']}"
                )
        after_records = variable_records(app)
        verify_variable_batch_readback(before_records, after_records, changes)
    except Exception as exc:
        rollback = rollback_variable_changes(
            app,
            attempted=attempted,
            before_records=before_records,
        )
        if not rollback["complete"]:
            raise LiveBackendError(
                f"AEDT variable update failed and rollback is incomplete: {rollback}"
            ) from exc
        if isinstance(exc, LiveBackendError):
            raise
        raise LiveBackendError(
            f"AEDT variable update failed: {type(exc).__name__}: {exc}"
        ) from exc
    after_by_name = {item["name"]: item for item in after_records}
    readback_changes = []
    for change in changes:
        record = after_by_name[change["name"]]
        readback_changes.append(
            {
                **change,
                "readback_expression": record["expression"],
            }
        )
    return {"changes": readback_changes, "variables": after_records}
