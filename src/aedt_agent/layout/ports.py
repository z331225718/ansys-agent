from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_BALL_PADSTACK_RE = re.compile(r"\bBALL\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


@dataclass(frozen=True)
class ComponentConnection:
    name: str
    partname: str
    component_type: str
    layer: str
    bbox: list[float]
    pins: list[dict[str, Any]]


def locate_layout_port_candidates(
    edb_path: str | Path,
    signal_nets: list[str],
    reference_nets: list[str],
    *,
    aedt_version: str,
    edb_backend: str = "grpc",
) -> dict[str, Any]:
    from pyedb import Edb

    grpc = {"auto": None, "grpc": True, "dotnet": False}.get(edb_backend)
    if edb_backend not in {"auto", "grpc", "dotnet"}:
        raise ValueError(f"unsupported edb_backend: {edb_backend}")
    edb = Edb(edbpath=str(edb_path), version=aedt_version, grpc=grpc)
    try:
        components = _component_connections_from_edb(edb, signal_nets, reference_nets)
        report = score_layout_port_candidates(components, signal_nets, reference_nets)
        report["edb_path"] = str(edb_path)
        report["component_count"] = len(components)
        return report
    finally:
        try:
            edb.close()
        except Exception:
            pass


def score_layout_port_candidates(
    components: list[ComponentConnection],
    signal_nets: list[str],
    reference_nets: list[str],
) -> dict[str, Any]:
    signals = _normalize_set(signal_nets)
    references = _normalize_set(reference_nets)
    candidates = [_component_candidate(component, signals, references) for component in components]
    candidates.extend(_paired_component_candidates(components, signals, references))
    candidates = [candidate for candidate in candidates if candidate["score"] > 0]
    candidates.sort(key=lambda item: (-item["score"], item["name"]))
    endpoints = _select_spatially_distinct_endpoints(candidates)
    return {
        "status": "ready" if len(endpoints) >= 2 else "needs_user_hint",
        "signal_nets": list(signal_nets),
        "reference_nets": list(reference_nets),
        "recommended_endpoints": endpoints,
        "candidates": candidates,
    }


def find_uniform_line_edge_candidates(
    primitives: list[Any],
    *,
    signal_nets: list[str],
    local_cut_region: dict[str, Any],
    hint: dict[str, Any],
) -> dict[str, Any]:
    from aedt_agent.layout.local_cut import parse_local_cut_region

    region = parse_local_cut_region(local_cut_region)
    side = str(hint.get("side") or "right")
    layer = str(hint.get("layer") or "")
    signal_order = {net.casefold(): index for index, net in enumerate(signal_nets)}
    signals = set(signal_order)
    candidates: list[dict[str, Any]] = []
    for primitive in primitives:
        if str(getattr(primitive, "net_name", "")).casefold() not in signals:
            continue
        if layer and str(getattr(primitive, "layer", "")) != layer:
            continue
        for edge_number, edge in enumerate(getattr(primitive, "edges", []) or []):
            midpoint = _edge_midpoint(edge)
            distance = _distance_to_bbox_side(midpoint, region, side)
            candidates.append(
                {
                    "primitive": str(getattr(primitive, "name", "")),
                    "edge_number": edge_number,
                    "net": str(getattr(primitive, "net_name", "")),
                    "layer": str(getattr(primitive, "layer", "")),
                    "side": side,
                    "midpoint": midpoint,
                    "distance_to_side": round(distance, 6),
                }
            )
    candidates.sort(key=lambda item: (signal_order.get(str(item["net"]).casefold(), 999), item["distance_to_side"], item["primitive"], item["edge_number"]))
    if not candidates:
        status = "needs_user_hint"
    elif _has_ambiguous_uniform_edge_candidate(candidates):
        status = "ambiguous"
    elif {str(candidate["net"]).casefold() for candidate in candidates} >= signals:
        status = "ready"
    else:
        status = "needs_user_hint"
    return {"status": status, "candidates": candidates}


def plan_layout_port_actions(
    candidate_report: dict[str, Any],
    *,
    impedance: int | float | str = 50,
    solderball: dict[str, Any] | None = None,
) -> dict[str, Any]:
    endpoints = list(candidate_report.get("recommended_endpoints") or [])
    signal_nets = _normalize_set(list(candidate_report.get("signal_nets") or []))
    reference_nets = _normalize_set(list(candidate_report.get("reference_nets") or []))
    actions = [
        _plan_endpoint_port_action(index + 1, endpoint, signal_nets, reference_nets, impedance=impedance, solderball=solderball)
        for index, endpoint in enumerate(endpoints)
    ]
    uniform_line_action = _plan_uniform_line_edge_action(candidate_report, len(actions) + 1, impedance=impedance)
    if uniform_line_action:
        actions.append(uniform_line_action)
    status = "ready" if actions and all(action["strategy"] != "needs_reference_pin" for action in actions) else "needs_user_hint"
    if len(actions) < 2:
        status = "needs_user_hint"
    return {
        "status": status,
        "impedance": impedance,
        "endpoint_count": len(actions),
        "port_actions": actions,
    }


def _has_ambiguous_uniform_edge_candidate(candidates: list[dict[str, Any]]) -> bool:
    by_net: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_net.setdefault(str(candidate.get("net", "")).casefold(), []).append(candidate)
    for net_candidates in by_net.values():
        if len(net_candidates) >= 2 and abs(float(net_candidates[0]["distance_to_side"]) - float(net_candidates[1]["distance_to_side"])) <= 0.05:
            return True
    return False


def _plan_uniform_line_edge_action(
    candidate_report: dict[str, Any],
    index: int,
    *,
    impedance: int | float | str,
) -> dict[str, Any] | None:
    uniform_report = candidate_report.get("uniform_line_edge_candidates") or {}
    if uniform_report.get("status") != "ready":
        return None
    edges = _best_uniform_line_edges(
        list(uniform_report.get("candidates") or []),
        list(candidate_report.get("signal_nets") or []),
    )
    if not edges:
        return None
    return {
        "endpoint": "uniform_line",
        "component": "",
        "port_name": f"P{index}_uniform_line",
        "strategy": "uniform_line_edge_port",
        "api": "Hfss3dLayout.create_edge_port",
        "requires_solder_ball_cylinders": False,
        "edges": edges,
        "impedance": impedance,
        "reason": "Uniform-line endpoint is represented by explicit trace edges near the user-defined local cut bbox boundary.",
    }


def _best_uniform_line_edges(candidates: list[dict[str, Any]], signal_nets: list[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for net in signal_nets:
        matches = [candidate for candidate in candidates if str(candidate.get("net", "")).casefold() == str(net).casefold()]
        if not matches:
            continue
        best = min(matches, key=lambda item: (float(item.get("distance_to_side", 0.0)), str(item.get("primitive", "")), int(item.get("edge_number", 0))))
        output.append(
            {
                "primitive": str(best.get("primitive", "")),
                "edge_number": int(best.get("edge_number", 0)),
                "net": str(best.get("net", "")),
                "layer": str(best.get("layer", "")),
            }
        )
    return output


def _edge_midpoint(edge: Any) -> list[float]:
    return [(float(edge[0][0]) + float(edge[1][0])) / 2.0, (float(edge[0][1]) + float(edge[1][1])) / 2.0]


def _distance_to_bbox_side(point: list[float], region: dict[str, Any], side: str) -> float:
    if side == "left":
        return abs(point[0] - region["x_min"])
    if side == "right":
        return abs(point[0] - region["x_max"])
    if side == "bottom":
        return abs(point[1] - region["y_min"])
    if side == "top":
        return abs(point[1] - region["y_max"])
    raise ValueError("uniform_line_port_hint.side must be one of left, right, bottom, top")


def apply_layout_port_actions(app: Any, port_action_plan: dict[str, Any]) -> dict[str, Any]:
    created_ports: list[str] = []
    deferred_actions: list[dict[str, Any]] = []
    failed_actions: list[dict[str, Any]] = []
    for action in port_action_plan.get("port_actions") or []:
        strategy = action.get("strategy")
        if strategy == "toggle_via_pin_gap_port":
            toggle_failed = False
            last_error = ""
            for pin_pair in action.get("pin_pairs") or []:
                port_name = _pin_pair_port_name(action, pin_pair)
                toggle_result = _create_toggle_via_pin_gap_port(app, action, pin_pair, name=port_name)
                if toggle_result["deferred_action"]:
                    deferred_actions.append(toggle_result["deferred_action"])
                    toggle_failed = True
                    break
                if toggle_result["error"]:
                    toggle_failed = True
                    last_error = toggle_result["error"]
                    break
                created_ports.append(toggle_result["port_name"])
            if toggle_failed and last_error:
                failed_actions.append({"action": action, "error": last_error})
            continue
        if strategy == "edge_port_at_pin":
            edge_port_failed = False
            last_error = ""
            for pin_pair in action.get("pin_pairs") or []:
                port_name = _pin_pair_port_name(action, pin_pair)
                edge_result = _create_edge_port_at_pin(app, action, pin_pair, name=port_name)
                if edge_result["deferred_action"]:
                    deferred_actions.append(edge_result["deferred_action"])
                    edge_port_failed = True
                    break
                if edge_result["error"]:
                    edge_port_failed = True
                    last_error = edge_result["error"]
                    break
                created_ports.append(edge_result["port_name"])
            if edge_port_failed:
                fallback = _create_component_net_ports(app, action)
                if fallback["created_ports"]:
                    created_ports.extend(fallback["created_ports"])
                    continue
                if fallback["deferred_action"]:
                    deferred_actions.append(fallback["deferred_action"])
                    continue
                if last_error:
                    failed_actions.append({"action": action, "error": last_error})
            continue
        if strategy == "uniform_line_edge_port":
            edge_result = _create_uniform_line_edge_ports(app, action)
            created_ports.extend(edge_result["created_ports"])
            deferred_actions.extend(edge_result["deferred_actions"])
            failed_actions.extend(edge_result["failed_actions"])
            continue
        if strategy == "pin_to_ground_lumped_port":
            deferred_actions.append(
                {
                    "component": action.get("component", ""),
                    "strategy": strategy,
                    "reason": "pin-to-ground lumped ports are disabled for this workflow; use toggle_via_pin_gap_port",
                }
            )
            continue
        if strategy == "vertical_circuit_port_at_pin":
            continue
        if strategy == "component_cylinder_port":
            cylinder_result = _enable_component_solderball_cylinders(app, action)
            if not cylinder_result["enabled"]:
                deferred_actions.append(
                    {
                        "component": action.get("component", ""),
                        "strategy": strategy,
                        "reason": cylinder_result["reason"],
                    }
                )
                continue
            fallback = _create_component_net_ports(app, action)
            if fallback["created_ports"]:
                created_ports.extend(fallback["created_ports"])
                continue
            if fallback["deferred_action"]:
                deferred_actions.append(fallback["deferred_action"])
                continue
            if fallback["error"]:
                failed_actions.append({"action": action, "error": fallback["error"]})
                continue
            continue
        deferred_actions.append(
            {
                "component": action.get("component", ""),
                "strategy": str(strategy),
                "reason": action.get("reason", "unsupported port action strategy"),
            }
        )
    status = "succeeded"
    if failed_actions:
        status = "failed"
    elif deferred_actions and created_ports:
        status = "partial"
    elif deferred_actions:
        status = "deferred"
    return {
        "status": status,
        "created_ports": created_ports,
        "deferred_actions": deferred_actions,
        "failed_actions": failed_actions,
    }


def apply_edb_layout_port_actions(edb: Any, port_action_plan: dict[str, Any]) -> dict[str, Any]:
    created_ports: list[str] = []
    deferred_actions: list[dict[str, Any]] = []
    failed_actions: list[dict[str, Any]] = []
    excitation_manager = getattr(edb, "excitation_manager", None)
    if excitation_manager is None or not hasattr(excitation_manager, "create_port_between_pin_and_layer"):
        return {
            "status": "deferred",
            "created_ports": [],
            "deferred_actions": [
                {
                    "strategy": "vertical_circuit_port_at_pin",
                    "reason": "Edb.excitation_manager.create_port_between_pin_and_layer is unavailable",
                }
            ],
            "failed_actions": [],
        }
    for action in port_action_plan.get("port_actions") or []:
        if action.get("strategy") != "vertical_circuit_port_at_pin":
            continue
        component = str(action.get("component") or "")
        impedance = action.get("impedance") or 50
        for pin_pair in action.get("pin_pairs") or []:
            signal_pin = str(pin_pair.get("signal_pin") or "")
            reference_net = str(pin_pair.get("reference_net") or "")
            reference_layer = str(pin_pair.get("reference_layer") or pin_pair.get("reference_start_layer") or "")
            if not component or not signal_pin or not reference_net or not reference_layer:
                deferred_actions.append(
                    {
                        "component": component,
                        "pin": signal_pin,
                        "strategy": action.get("strategy", ""),
                        "reason": "component, signal pin, reference net, and reference layer are required",
                    }
                )
                continue
            try:
                terminal = excitation_manager.create_port_between_pin_and_layer(
                    component_name=component,
                    pins_name=signal_pin,
                    layer_name=reference_layer,
                    reference_net=reference_net,
                    impedance=impedance,
                )
            except Exception as exc:
                failed_actions.append({"action": action, "pin": signal_pin, "error": str(exc)})
                continue
            if terminal is False:
                failed_actions.append({"action": action, "pin": signal_pin, "error": "create_port_between_pin_and_layer returned False"})
                continue
            created_ports.extend(_terminal_names(terminal, fallback=f"{component}_{signal_pin}"))
    status = "succeeded"
    if failed_actions:
        status = "failed"
    elif deferred_actions and created_ports:
        status = "partial"
    elif deferred_actions:
        status = "deferred"
    elif not created_ports:
        status = "skipped"
    return {
        "status": status,
        "created_ports": created_ports,
        "deferred_actions": deferred_actions,
        "failed_actions": failed_actions,
    }


def _terminal_names(terminal: Any, *, fallback: str) -> list[str]:
    if isinstance(terminal, (list, tuple)):
        terminals = list(terminal)
    else:
        terminals = [terminal]
    names: list[str] = []
    for index, item in enumerate(terminals):
        try:
            setattr(item, "is_circuit_port", True)
        except Exception:
            pass
        name = str(getattr(item, "name", "") or fallback)
        if index and name == fallback:
            name = f"{fallback}_{index + 1}"
        names.append(name)
    return names


def _component_connections_from_edb(edb: Any, signal_nets: list[str], reference_nets: list[str]) -> list[ComponentConnection]:
    relevant = _normalize_set(signal_nets + reference_nets)
    output: list[ComponentConnection] = []
    for name, component in edb.components.instances.items():
        pins: list[dict[str, Any]] = []
        for pin_name, pin in getattr(component, "pins", {}).items():
            net_name = _pin_net_name(pin)
            if net_name.casefold() not in relevant:
                continue
            pins.append(
                {
                    "pin": str(pin_name),
                    "net": net_name,
                    "position": _pin_position(pin),
                    "padstack": str(getattr(pin, "padstack_definition", "")),
                    "start_layer": str(getattr(pin, "start_layer", "")),
                    "stop_layer": str(getattr(pin, "stop_layer", "")),
                }
            )
        if not pins:
            continue
        output.append(
            ComponentConnection(
                name=str(name),
                partname=str(getattr(component, "partname", "")),
                component_type=str(getattr(component, "type", "")),
                layer=str(getattr(component, "placement_layer", "")),
                bbox=[float(value) for value in getattr(component, "bounding_box", [0.0, 0.0, 0.0, 0.0])],
                pins=pins,
            )
        )
    return output


def _plan_endpoint_port_action(
    index: int,
    endpoint: dict[str, Any],
    signal_nets: set[str],
    reference_nets: set[str],
    *,
    impedance: int | float | str,
    solderball: dict[str, Any] | None = None,
) -> dict[str, Any]:
    component = _first_component_name(endpoint)
    if _endpoint_needs_cylinders(endpoint):
        return {
            "endpoint": endpoint.get("name", component),
            "component": component,
            "port_name": f"P{index}_{component}",
            "strategy": "component_cylinder_port",
            "api": "Hfss3dLayout.create_ports_on_component_by_nets",
            "requires_solder_ball_cylinders": True,
            "die_type": _solderball_value(solderball, "die_type", "1"),
            "die_orientation": _solderball_value(solderball, "die_orientation", "1"),
            "die_type_name": "FlipChip",
            "die_orientation_name": "Chip Bottom",
            "solderball_type": _solderball_value(solderball, "type", "Cyl"),
            "solderball_diameter": _solderball_value(solderball, "diameter", _infer_solderball_diameter(endpoint)),
            "solderball_mid_diameter": _solderball_value(
                solderball,
                "mid_diameter",
                _solderball_value(solderball, "diameter", _infer_solderball_diameter(endpoint)),
            ),
            "solderball_height": _solderball_value(solderball, "height", _infer_solderball_height(endpoint)),
            "solderball_material": _solderball_value(solderball, "material", "solder"),
            "signal_nets": sorted(signal_nets),
            "reference_nets": sorted(reference_nets),
            "impedance": impedance,
            "reason": "BGA/ball/bump endpoint should expose solder-ball cylinders before component-net ports are created.",
        }
    pin_pairs = _nearest_signal_reference_pin_pairs(endpoint, signal_nets, reference_nets)
    if not pin_pairs:
        return {
            "endpoint": endpoint.get("name", component),
            "component": component,
            "port_name": f"P{index}_{component}",
            "strategy": "needs_reference_pin",
            "api": "",
            "requires_solder_ball_cylinders": False,
            "signal_nets": sorted(signal_nets),
            "reference_nets": sorted(reference_nets),
            "impedance": impedance,
            "reason": "Signal pins were found but no reference pin was available on this endpoint.",
        }
    return {
        "endpoint": endpoint.get("name", component),
        "component": component,
        "port_name": f"P{index}_{component}",
        "strategy": "toggle_via_pin_gap_port",
        "api": "Hfss3dLayout.oeditor.ToggleViaPin",
        "requires_solder_ball_cylinders": False,
        "pin_pairs": pin_pairs,
        "signal_nets": sorted(signal_nets),
        "reference_nets": sorted(reference_nets),
        "impedance": impedance,
        "reason": "Endpoint has explicit signal pins, so toggle the signal pin as a vertical layout port and set HFSS Type to Gap.",
    }


def _first_component_name(endpoint: dict[str, Any]) -> str:
    components = endpoint.get("components") or []
    if components:
        return str(components[0])
    return str(endpoint.get("name") or "Endpoint")


def _endpoint_needs_cylinders(endpoint: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(endpoint.get("name", "")),
            str(endpoint.get("partname", "")),
            str(endpoint.get("component_type", "")),
            " ".join(str(pin.get("padstack", "")) for pin in endpoint.get("pins") or []),
        ]
    ).lower()
    return any(token in text for token in ["bga", "ball", "bump"])


def _nearest_signal_reference_pin_pairs(
    endpoint: dict[str, Any],
    signal_nets: set[str],
    reference_nets: set[str],
) -> list[dict[str, Any]]:
    pins = list(endpoint.get("pins") or [])
    signal_pins = [pin for pin in pins if str(pin.get("net", "")).casefold() in signal_nets]
    reference_pins = [pin for pin in pins if str(pin.get("net", "")).casefold() in reference_nets]
    pairs = []
    for signal_pin in signal_pins:
        reference_pin = _nearest_pin(signal_pin, reference_pins)
        if not reference_pin:
            continue
        pairs.append(
            {
                "signal_pin": str(signal_pin.get("pin", "")),
                "signal_net": str(signal_pin.get("net", "")),
                "signal_position": list(signal_pin.get("position") or [0.0, 0.0]),
                "signal_start_layer": str(signal_pin.get("start_layer") or "") or None,
                "signal_stop_layer": str(signal_pin.get("stop_layer") or "") or None,
                "reference_pin": str(reference_pin.get("pin", "")),
                "reference_net": str(reference_pin.get("net", "")),
                "reference_position": list(reference_pin.get("position") or [0.0, 0.0]),
                "reference_start_layer": str(reference_pin.get("start_layer") or "") or None,
                "reference_stop_layer": str(reference_pin.get("stop_layer") or "") or None,
                "reference_layer": _reference_layer_for_vertical_port(signal_pin, reference_pin),
            }
        )
    return pairs


def _reference_layer_for_vertical_port(signal_pin: dict[str, Any], reference_pin: dict[str, Any]) -> str:
    for key in ("start_layer", "stop_layer"):
        value = str(reference_pin.get(key) or "")
        if value:
            return value
    for key in ("stop_layer", "start_layer"):
        value = str(signal_pin.get(key) or "")
        if value:
            return value
    return ""


def _pin_pair_port_name(action: dict[str, Any], pin_pair: dict[str, Any]) -> str:
    base_name = str(action.get("port_name") or action.get("component") or "P")
    signal_pin = str(pin_pair.get("signal_pin") or "").replace(" ", "_")
    return f"{base_name}_{signal_pin}" if signal_pin else base_name


def _create_toggle_via_pin_gap_port(app: Any, action: dict[str, Any], pin_pair: dict[str, Any], *, name: str) -> dict[str, Any]:
    editor = getattr(app, "oeditor", None) or getattr(getattr(app, "modeler", None), "oeditor", None)
    if editor is None or not hasattr(editor, "ToggleViaPin"):
        return {
            "port_name": "",
            "deferred_action": {
                "component": action.get("component", ""),
                "strategy": action.get("strategy", ""),
                "reason": "Hfss3dLayout.oeditor.ToggleViaPin is unavailable",
            },
            "error": "",
        }
    component = str(action.get("component") or "")
    signal_pin = str(pin_pair.get("signal_pin") or "")
    if not component or not signal_pin:
        return {"port_name": "", "deferred_action": None, "error": "component and signal pin are required for ToggleViaPin port"}
    before_ports = set(_port_list(app))
    try:
        editor.ToggleViaPin(["NAME:elements", f"{component}-{signal_pin}"])
    except Exception as exc:
        return {"port_name": "", "deferred_action": None, "error": str(exc)}
    after_ports = set(_port_list(app))
    new_ports = [port for port in _port_list(app) if port in (after_ports - before_ports)]
    port_name = new_ports[0] if new_ports else _expected_toggle_via_pin_port_name(component, signal_pin, pin_pair, fallback=name)
    try:
        _change_layout_port_hfss_type(app, port_name, "Gap")
    except Exception as exc:
        return {"port_name": "", "deferred_action": None, "error": str(exc)}
    return {"port_name": str(port_name), "deferred_action": None, "error": ""}


def _port_list(app: Any) -> list[str]:
    try:
        return [str(port) for port in getattr(app, "port_list")]
    except Exception:
        return []


def _expected_toggle_via_pin_port_name(component: str, signal_pin: str, pin_pair: dict[str, Any], *, fallback: str) -> str:
    signal_net = str(pin_pair.get("signal_net") or "")
    if signal_net:
        return f"{component}.{signal_pin}.{signal_net}"
    return fallback


def _change_layout_port_hfss_type(app: Any, port_name: str, value: str) -> None:
    modeler = getattr(app, "modeler", None)
    if modeler is not None and hasattr(modeler, "change_property"):
        modeler.change_property(f"Excitations:{port_name}", "HFSS Type", value, "EM Design")
        return
    design = getattr(app, "odesign", None)
    if design is not None and hasattr(design, "ChangeProperty"):
        design.ChangeProperty(
            [
                "NAME:AllTabs",
                [
                    "NAME:EM Design",
                    ["NAME:PropServers", f"Excitations:{port_name}"],
                    ["NAME:ChangedProps", ["NAME:HFSS Type", "Value:=", value]],
                ],
            ]
        )
        return
    raise RuntimeError("unable to set HFSS Type on created layout pin port")


def _create_edge_port_at_pin(app: Any, action: dict[str, Any], pin_pair: dict[str, Any], *, name: str) -> dict[str, Any]:
    if not hasattr(app, "create_edge_port"):
        return {
            "port_name": "",
            "deferred_action": {
                "component": action.get("component", ""),
                "strategy": action.get("strategy", ""),
                "reason": "Hfss3dLayout.create_edge_port is unavailable",
            },
            "error": "",
        }
    signal_pin = str(pin_pair.get("signal_pin") or "")
    signal_edge = _find_nearest_layout_edge(
        app,
        net=str(pin_pair.get("signal_net") or ""),
        position=list(pin_pair.get("signal_position") or [0.0, 0.0]),
    )
    if not signal_edge:
        return {
            "port_name": "",
            "deferred_action": {
                "component": action.get("component", ""),
                "strategy": action.get("strategy", ""),
                "reason": f"no signal primitive edge found near pin {signal_pin or name}",
            },
            "error": "",
        }
    reference_edge = _find_nearest_layout_edge(
        app,
        net=str(pin_pair.get("reference_net") or ""),
        position=list(pin_pair.get("reference_position") or [0.0, 0.0]),
    )
    kwargs: dict[str, Any] = {"is_circuit_port": True, "is_wave_port": False}
    if action.get("use_reference_edge") is True and reference_edge:
        kwargs["reference_primitive"] = reference_edge["primitive"]
        kwargs["reference_edge_number"] = reference_edge["edge_number"]
    try:
        port = app.create_edge_port(signal_edge["primitive"], signal_edge["edge_number"], **kwargs)
    except Exception as exc:
        return {"port_name": "", "deferred_action": None, "error": str(exc)}
    if port is False:
        return {"port_name": "", "deferred_action": None, "error": "create_edge_port returned False"}
    return {"port_name": str(getattr(port, "name", name)), "deferred_action": None, "error": ""}


def _create_uniform_line_edge_ports(app: Any, action: dict[str, Any]) -> dict[str, Any]:
    if not hasattr(app, "create_edge_port"):
        return {
            "created_ports": [],
            "deferred_actions": [
                {
                    "strategy": action.get("strategy", ""),
                    "reason": "Hfss3dLayout.create_edge_port is unavailable for uniform-line endpoint",
                }
            ],
            "failed_actions": [],
        }
    created_ports: list[str] = []
    deferred_actions: list[dict[str, Any]] = []
    failed_actions: list[dict[str, Any]] = []
    for edge in action.get("edges") or []:
        primitive = str(edge.get("primitive") or "")
        if not primitive:
            deferred_actions.append(
                {
                    "strategy": action.get("strategy", ""),
                    "reason": "uniform-line edge candidate is missing primitive name",
                    "edge": edge,
                }
            )
            continue
        try:
            edge_number = int(edge.get("edge_number"))
        except (TypeError, ValueError):
            deferred_actions.append(
                {
                    "strategy": action.get("strategy", ""),
                    "reason": "uniform-line edge candidate is missing edge_number",
                    "edge": edge,
                }
            )
            continue
        try:
            port = app.create_edge_port(primitive, edge_number, is_circuit_port=True, is_wave_port=False)
        except Exception as exc:
            failed_actions.append({"action": action, "edge": edge, "error": str(exc)})
            continue
        if port is False:
            failed_actions.append({"action": action, "edge": edge, "error": "create_edge_port returned False"})
            continue
        created_ports.append(str(getattr(port, "name", f"{primitive}_{edge_number}")))
    return {"created_ports": created_ports, "deferred_actions": deferred_actions, "failed_actions": failed_actions}


def _find_nearest_layout_edge(app: Any, *, net: str, position: list[Any]) -> dict[str, Any] | None:
    if not net:
        return None
    point = [_meters_to_layout_units(app, float(position[0])), _meters_to_layout_units(app, float(position[1]))]
    candidates: list[dict[str, Any]] = []
    for primitive in _layout_geometries(app, net=net):
        edge = _nearest_non_degenerate_edge(primitive, point)
        if edge:
            candidates.append(edge)
    if not candidates:
        return None
    return min(candidates, key=lambda item: item["distance"])


def _layout_geometries(app: Any, *, net: str | None = None) -> list[Any]:
    geometries = getattr(getattr(app, "modeler", None), "geometries", None)
    if net:
        names = _layout_object_names_by_net(app, net)
        if names and isinstance(geometries, dict):
            return [geometries[name] for name in names if name in geometries]
    if isinstance(geometries, dict):
        values = list(geometries.values())
    if geometries is None:
        return []
    if not isinstance(geometries, dict):
        try:
            values = list(geometries)
        except TypeError:
            return []
    if not net:
        return values
    return [primitive for primitive in values if _primitive_net_name(primitive).casefold() == net.casefold()]


def _layout_object_names_by_net(app: Any, net: str) -> list[str]:
    editor = getattr(getattr(app, "modeler", None), "oeditor", None)
    if editor is None or not hasattr(editor, "FindObjects"):
        return []
    try:
        return [str(name) for name in editor.FindObjects("Net", net)]
    except Exception:
        return []


def _primitive_net_name(primitive: Any) -> str:
    try:
        net_name = getattr(primitive, "net_name", None)
    except Exception:
        net_name = None
    if net_name not in (None, ""):
        return str(net_name)
    if hasattr(primitive, "get_property_value"):
        try:
            return str(primitive.get_property_value("Net"))
        except Exception:
            return ""
    return ""


def _nearest_non_degenerate_edge(primitive: Any, point: list[float]) -> dict[str, Any] | None:
    try:
        edges = list(getattr(primitive, "edges"))
    except Exception:
        edges = []
    candidates: list[dict[str, Any]] = []
    for edge_number, edge in enumerate(edges):
        try:
            start = [float(edge[0][0]), float(edge[0][1])]
            end = [float(edge[1][0]), float(edge[1][1])]
        except Exception:
            continue
        if start == end:
            continue
        candidates.append(
            {
                "primitive": str(getattr(primitive, "name", "")),
                "edge_number": edge_number,
                "distance": _point_to_segment_distance(point, start, end),
            }
        )
    if candidates:
        return min(candidates, key=lambda item: item["distance"])
    if not hasattr(primitive, "edge_by_point"):
        return None
    try:
        edge_number = primitive.edge_by_point(point)
    except Exception:
        return None
    if edge_number is None:
        return None
    return {
        "primitive": str(getattr(primitive, "name", "")),
        "edge_number": int(edge_number),
        "distance": _edge_distance(primitive, int(edge_number), point),
    }


def _edge_distance(primitive: Any, edge_number: int, point: list[float]) -> float:
    try:
        edges = list(getattr(primitive, "edges"))
        edge = edges[edge_number]
        return _point_to_segment_distance(point, [float(edge[0][0]), float(edge[0][1])], [float(edge[1][0]), float(edge[1][1])])
    except Exception:
        return 0.0


def _point_to_segment_distance(point: list[float], start: list[float], end: list[float]) -> float:
    px, py = point
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    if dx == 0 and dy == 0:
        return _distance(point, start)
    t = max(0.0, min(1.0, ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)))
    closest = [sx + t * dx, sy + t * dy]
    return _distance(point, closest)


def _create_component_net_ports(app: Any, action: dict[str, Any]) -> dict[str, Any]:
    if not hasattr(app, "create_ports_on_component_by_nets"):
        return {
            "created_ports": [],
            "deferred_action": {
                "component": action.get("component", ""),
                "strategy": action.get("strategy", ""),
                "reason": "Hfss3dLayout.create_ports_on_component_by_nets is unavailable; solder-ball cylinder fallback is required",
            },
            "error": "",
        }
    try:
        ports = app.create_ports_on_component_by_nets(action.get("component", ""), action.get("signal_nets") or [])
    except Exception as exc:
        return {"created_ports": [], "deferred_action": None, "error": str(exc)}
    if not ports:
        return {
            "created_ports": [],
            "deferred_action": {
                "component": action.get("component", ""),
                "strategy": action.get("strategy", ""),
                "reason": "component-net port API returned no ports; solder-ball cylinder fallback is required",
            },
            "error": "",
        }
    return {
        "created_ports": [str(getattr(port, "name", port)) for port in ports],
        "deferred_action": None,
        "error": "",
    }


def _enable_component_solderball_cylinders(app: Any, action: dict[str, Any]) -> dict[str, Any]:
    component_name = str(action.get("component") or "")
    component = _layout_component(app, component_name)
    if not component or not hasattr(component, "set_solderball"):
        return {"enabled": False, "reason": "component solderball API is unavailable"}
    try:
        if not hasattr(component, "set_die_type"):
            return {"enabled": False, "reason": "component die-type API is unavailable"}
        die_enabled = component.set_die_type(
            die_type=int(action.get("die_type") or 1),
            orientation=int(action.get("die_orientation") or 1),
        )
        if die_enabled is False:
            return {"enabled": False, "reason": "flip-chip die type configuration failed"}
        enabled = component.set_solderball(
            solderball_type=str(action.get("solderball_type") or "Cyl"),
            diameter=str(action.get("solderball_diameter") or "0.1mm"),
            mid_diameter=str(action.get("solderball_mid_diameter") or action.get("solderball_diameter") or "0.1mm"),
            height=str(action.get("solderball_height") or "0.2mm"),
            material=str(action.get("solderball_material") or "solder"),
        )
    except Exception as exc:
        return {"enabled": False, "reason": f"solderball cylinder creation failed: {exc}"}
    if not enabled:
        return {"enabled": False, "reason": "solderball cylinder creation failed"}
    return {"enabled": True, "reason": ""}


def _layout_component(app: Any, component_name: str) -> Any:
    components = getattr(getattr(app, "modeler", None), "components", None)
    if isinstance(components, dict):
        return components.get(component_name)
    try:
        return components[component_name]
    except Exception:
        return None


def _infer_solderball_diameter(endpoint: dict[str, Any]) -> str:
    for pin in endpoint.get("pins") or []:
        padstack = str(pin.get("padstack", ""))
        match = _BALL_PADSTACK_RE.search(padstack)
        if match:
            return f"{match.group(1)}mil"
    return "0.1mm"


def _infer_solderball_height(endpoint: dict[str, Any]) -> str:
    for pin in endpoint.get("pins") or []:
        padstack = str(pin.get("padstack", ""))
        match = _BALL_PADSTACK_RE.search(padstack)
        if match:
            return f"{max(float(match.group(1)) / 2.0, 1.0):g}mil"
    return "0.2mm"


def _solderball_value(settings: dict[str, Any] | None, key: str, fallback: str) -> str:
    if not settings:
        return fallback
    value = settings.get(key)
    return str(value) if value not in (None, "") else fallback


def _meters_to_layout_units(app: Any, value: float) -> float:
    units = str(getattr(getattr(app, "modeler", None), "model_units", "") or "m").casefold()
    factors = {
        "m": 1.0,
        "meter": 1.0,
        "meters": 1.0,
        "mm": 1000.0,
        "millimeter": 1000.0,
        "millimeters": 1000.0,
        "um": 1_000_000.0,
        "micrometer": 1_000_000.0,
        "micrometers": 1_000_000.0,
        "mil": 39370.07874015748,
        "mils": 39370.07874015748,
        "in": 39.37007874015748,
        "inch": 39.37007874015748,
        "inches": 39.37007874015748,
    }
    return value * factors.get(units, 1.0)


def _nearest_pin(pin: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    position = list(pin.get("position") or [0.0, 0.0])
    return min(candidates, key=lambda item: _distance(position, list(item.get("position") or [0.0, 0.0])))


def _pin_net_name(pin: Any) -> str:
    try:
        return str(pin.net_name)
    except Exception:
        return ""


def _pin_position(pin: Any) -> list[float]:
    try:
        position = pin.position
        return [float(position[0]), float(position[1])]
    except Exception:
        return [0.0, 0.0]


def _component_candidate(component: ComponentConnection, signals: set[str], references: set[str]) -> dict[str, Any]:
    nets = _component_nets(component)
    signal_hits = sorted(nets & signals)
    reference_hits = sorted(nets & references)
    reasons: list[str] = []
    score = 0.0
    if signal_hits:
        score += 30.0 * len(signal_hits)
        reasons.append(f"connects signal nets: {', '.join(signal_hits)}")
    if signals and signals.issubset(nets):
        score += 35.0
        reasons.append("connects all signal nets")
    if reference_hits:
        score += 10.0
        reasons.append(f"has nearby reference nets: {', '.join(reference_hits[:3])}")
    lower_text = f"{component.name} {component.partname} {component.component_type}".lower()
    if any(token in lower_text for token in ["bga", "ball", "bump"]):
        score += 15.0
        reasons.append("component/package name suggests BGA/ball/bump")
    if component.component_type.lower() in {"ic", "io", "connector"}:
        score += 12.0
        reasons.append(f"component type is {component.component_type}")
    if component.component_type.lower() in {"capacitor", "resistor"} and len(signal_hits) == 1:
        score -= 8.0
        reasons.append("single-ended passive is likely one side of a differential endpoint")
    confidence = min(max(score / 130.0, 0.0), 1.0)
    return {
        "kind": "component",
        "name": component.name,
        "components": [component.name],
        "partname": component.partname,
        "component_type": component.component_type,
        "layer": component.layer,
        "signal_nets": signal_hits,
        "reference_nets": reference_hits,
        "pins": [pin for pin in component.pins if str(pin.get("net", "")).casefold() in signals | references],
        "bbox": component.bbox,
        "centroid": _bbox_centroid(component.bbox),
        "score": round(score, 3),
        "confidence": round(confidence, 3),
        "reasons": reasons,
    }


def _paired_component_candidates(
    components: list[ComponentConnection],
    signals: set[str],
    references: set[str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for left, right in itertools.combinations(components, 2):
        left_nets = _component_nets(left)
        right_nets = _component_nets(right)
        if not (left_nets & signals) or not (right_nets & signals):
            continue
        covered = (left_nets | right_nets) & signals
        if not signals or covered != signals:
            continue
        if left_nets & right_nets & signals:
            continue
        distance = _distance(_bbox_centroid(left.bbox), _bbox_centroid(right.bbox))
        if distance > 0.003:
            continue
        score = 78.0
        reasons = [
            "nearby components together cover all signal nets",
            f"component spacing {distance * 1000:.3f} mm",
        ]
        if any(item.component_type.lower() in {"capacitor", "resistor"} for item in [left, right]):
            score += 8.0
            reasons.append("paired passives often mark the opposite endpoint of a high-speed channel")
        bbox = [
            min(left.bbox[0], right.bbox[0]),
            min(left.bbox[1], right.bbox[1]),
            max(left.bbox[2], right.bbox[2]),
            max(left.bbox[3], right.bbox[3]),
        ]
        output.append(
            {
                "kind": "component_group",
                "name": "+".join(sorted([left.name, right.name])),
                "components": sorted([left.name, right.name]),
                "partname": "",
                "component_type": "paired_passives",
                "layer": left.layer if left.layer == right.layer else "mixed",
                "signal_nets": sorted(covered),
                "reference_nets": sorted((left_nets | right_nets) & references),
                "pins": [
                    pin
                    for component in [left, right]
                    for pin in component.pins
                    if str(pin.get("net", "")).casefold() in signals | references
                ],
                "bbox": bbox,
                "centroid": _bbox_centroid(bbox),
                "score": round(score, 3),
                "confidence": round(min(score / 110.0, 1.0), 3),
                "reasons": reasons,
            }
        )
    return output


def _select_spatially_distinct_endpoints(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    used_components: set[str] = set()
    for candidate in candidates:
        components = set(candidate.get("components") or [])
        if components & used_components:
            continue
        if endpoints and _distance(candidate["centroid"], endpoints[0]["centroid"]) < 0.01:
            continue
        endpoints.append(candidate)
        used_components.update(components)
        if len(endpoints) == 2:
            break
    return endpoints


def _component_nets(component: ComponentConnection) -> set[str]:
    return {str(pin.get("net", "")).casefold() for pin in component.pins if pin.get("net")}


def _normalize_set(values: list[str]) -> set[str]:
    return {str(value).casefold() for value in values}


def _bbox_centroid(bbox: list[float]) -> list[float]:
    if len(bbox) < 4:
        return [0.0, 0.0]
    return [(float(bbox[0]) + float(bbox[2])) / 2.0, (float(bbox[1]) + float(bbox[3])) / 2.0]


def _distance(left: list[float], right: list[float]) -> float:
    return math.hypot(float(left[0]) - float(right[0]), float(left[1]) - float(right[1]))
