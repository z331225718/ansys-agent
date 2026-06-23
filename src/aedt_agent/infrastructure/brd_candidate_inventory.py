from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from aedt_agent.infrastructure.brd_model_edit import (
    _close_edb,
    _edb_layer_names,
    _grpc_mode,
    _modeler_primitives,
    _normalize_layer_name,
    _padstack_instance_center,
    _point_inside_shape,
    _primitive_id,
    _primitive_is_void,
    _primitive_layer,
    _primitive_net,
    _sidecar_edb,
)
from aedt_agent.infrastructure.brd_real_build import RealAedtEnvironment


REFERENCE_NET_PREFIXES = (
    "gnd",
    "pgnd",
    "agnd",
    "dgnd",
    "vcc",
    "vdd",
    "vss",
    "pwr",
    "power",
)


@dataclass(frozen=True)
class BrdCandidateInventoryRequest:
    project_path: Path
    artifact_dir: Path
    seed_inventory: dict[str, Any] = field(default_factory=dict)
    inventory_output_path: Path | None = None
    signal_nets: list[str] = field(default_factory=list)
    reference_nets: list[str] = field(default_factory=list)
    geometry_constraints: dict[str, Any] = field(default_factory=dict)
    environment: RealAedtEnvironment = field(default_factory=RealAedtEnvironment)


@dataclass(frozen=True)
class BrdCandidateInventoryResult:
    inventory_path: str
    manifest_path: str
    inventory: dict[str, Any]
    summary: dict[str, Any]


class BrdCandidateInventoryAdapter:
    def __init__(self, *, edb_factory: Callable[..., Any] | None = None) -> None:
        self._edb_factory = edb_factory

    def run(
        self,
        request: BrdCandidateInventoryRequest,
    ) -> BrdCandidateInventoryResult:
        _validate_request(request)
        artifact_dir = request.artifact_dir.resolve()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        edb_path = _sidecar_edb(request.project_path.resolve())
        edb = self._edb_class()(
            edbpath=str(edb_path),
            version=request.environment.version,
            grpc=_grpc_mode(request.environment.edb_backend),
        )
        try:
            inventory = _build_inventory(edb, request)
        finally:
            _close_edb(edb)

        inventory_path = request.inventory_output_path or artifact_dir / "candidate_action_inventory.json"
        inventory_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(inventory_path, inventory)
        manifest_path = artifact_dir / "candidate_inventory_manifest.json"
        summary = _inventory_summary(inventory)
        _write_json(
            manifest_path,
            {
                "version": 1,
                "input": {
                    "project_path": str(request.project_path),
                    "seed_inventory": request.seed_inventory,
                    "signal_nets": request.signal_nets,
                    "reference_nets": request.reference_nets,
                },
                "outputs": {
                    "candidate_action_inventory": str(inventory_path),
                },
                "summary": summary,
            },
        )
        return BrdCandidateInventoryResult(
            inventory_path=str(inventory_path),
            manifest_path=str(manifest_path),
            inventory=inventory,
            summary=summary,
        )

    def _edb_class(self) -> Callable[..., Any]:
        if self._edb_factory is not None:
            return self._edb_factory
        from pyedb import Edb

        return Edb


def _validate_request(request: BrdCandidateInventoryRequest) -> None:
    if request.project_path.suffix.casefold() != ".aedt":
        raise ValueError("project_path must end with .aedt")
    if not request.project_path.is_file():
        raise FileNotFoundError(f"project_path not found: {request.project_path}")
    if not _sidecar_edb(request.project_path).is_dir():
        raise FileNotFoundError(f"sidecar AEDB not found: {_sidecar_edb(request.project_path)}")
    if not request.environment.version.strip():
        raise ValueError("AEDT version is required")


def _build_inventory(
    edb: Any,
    request: BrdCandidateInventoryRequest,
) -> dict[str, Any]:
    seed = dict(request.seed_inventory or {})
    instances = _signal_padstack_instances(
        edb,
        signal_nets=request.signal_nets or _as_str_list(seed.get("signal_nets")),
        reference_nets=request.reference_nets or _as_str_list(seed.get("reference_nets")),
    )
    layers = _edb_layer_names(edb)
    centers = [_instance_center_record(instance) for instance in instances]
    anti_pad_entries = _discover_anti_pad_entries(
        edb,
        seed,
        instances,
        request.geometry_constraints,
    )
    nfp_entries = _discover_nfp_entries(
        seed,
        instances,
        layers,
        request.geometry_constraints,
    )
    return {
        "source": "aedt_model_discovery",
        "tdr_observation_port": seed.get("tdr_observation_port") or "Diff1",
        "tdr_port_orientation_evidence": seed.get("tdr_port_orientation_evidence") or "unknown",
        "tdr_feature_time": seed.get("tdr_feature_time"),
        "signal_nets": sorted({record["net"] for record in centers if record["net"]}),
        "reference_nets": request.reference_nets or _as_str_list(seed.get("reference_nets")),
        "discovered_center_count": len(centers),
        "discovered_centers": centers,
        "anti_pad_shape_layers": anti_pad_entries,
        "non_functional_pad_layers": nfp_entries,
    }


def _signal_padstack_instances(
    edb: Any,
    *,
    signal_nets: list[str],
    reference_nets: list[str],
) -> list[Any]:
    instances = getattr(getattr(edb, "padstacks", None), "instances", None)
    if not isinstance(instances, dict):
        raise ValueError("EDB padstack instances are required for inventory discovery")
    signal_filter = {net.casefold() for net in signal_nets if net.strip()}
    reference_filter = {net.casefold() for net in reference_nets if net.strip()}
    selected = []
    for instance in instances.values():
        net = str(getattr(instance, "net_name", "") or "")
        if signal_filter:
            if net.casefold() in signal_filter:
                selected.append(instance)
            continue
        if net and not _is_reference_net(net, reference_filter):
            selected.append(instance)
    return sorted(selected, key=lambda item: str(getattr(item, "id", getattr(item, "name", ""))))


def _is_reference_net(net: str, reference_filter: set[str]) -> bool:
    normalized = net.strip().casefold()
    if normalized in reference_filter:
        return True
    return any(normalized.startswith(prefix) for prefix in REFERENCE_NET_PREFIXES)


def _discover_anti_pad_entries(
    edb: Any,
    seed: Mapping[str, Any],
    instances: list[Any],
    geometry_constraints: Mapping[str, Any],
) -> list[dict[str, Any]]:
    requested = _seed_items(seed, "anti_pad_shape_layers", "anti_pad_candidates", "shape_backed_layers")
    requested_layers = [layer for item in requested for layer in _item_layers(item)]
    layers = requested_layers or _primitive_layers_with_signal_centers(edb, instances)
    entries = []
    for layer in layers:
        seed_item = _seed_item_for_layer(requested, layer)
        centers = [_padstack_instance_center(instance) for instance in instances]
        shapes = _plane_shapes_containing_centers(edb, layer, centers)
        if not shapes:
            continue
        center_instances = [
            instance
            for instance in instances
            if any(_point_inside_shape(shape, _padstack_instance_center(instance)) for shape in shapes)
        ]
        if not center_instances:
            continue
        bridge_ids = _nearest_pair_instance_ids(center_instances)
        entry = {
            "layer": layer,
            "plane_shape_ids": [str(_primitive_id(shape)) for shape in shapes],
            "center_padstack_instance_ids": [_instance_id(instance) for instance in center_instances],
            "bridge_center_padstack_instance_ids": bridge_ids,
            "parasitic_target": str(
                seed_item.get("parasitic_target")
                or f"auto_discovered_shape_backed_parasitic_on_{layer}"
            ),
            "target_radius": _target_radius(
                seed_item,
                geometry_constraints,
                "anti_pad",
                default_value=22.0,
            ),
            "target_region": str(seed_item.get("target_region") or "reviewed_other"),
            "center_source": "padstack_instances",
            "bridge_between_vias": True,
            "discovery_method": "plane_shapes_containing_signal_padstack_centers",
        }
        entries.append(entry)
    return entries


def _discover_nfp_entries(
    seed: Mapping[str, Any],
    instances: list[Any],
    layer_order: list[str],
    geometry_constraints: Mapping[str, Any],
) -> list[dict[str, Any]]:
    requested = _seed_items(
        seed,
        "non_functional_pad_layers",
        "non_functional_pad_candidates",
        "mechanical_hole_layers",
    )
    layers = [layer for item in requested for layer in _item_layers(item)]
    if not layers:
        layers = _layers_spanned_by_instances(instances, layer_order)
    entries = []
    for layer in layers:
        seed_item = _seed_item_for_layer(requested, layer)
        if not instances:
            continue
        entries.append(
            {
                "layer": layer,
                "center_padstack_instance_ids": [_instance_id(instance) for instance in instances],
                "signal_nets": sorted(
                    {
                        str(getattr(instance, "net_name", "") or "")
                        for instance in instances
                        if str(getattr(instance, "net_name", "") or "")
                    }
                ),
                "parasitic_target": str(
                    seed_item.get("parasitic_target")
                    or f"auto_discovered_mechanical_hole_barrel_on_{layer}"
                ),
                "target_radius": _target_radius(
                    seed_item,
                    geometry_constraints,
                    "non_functional_pad",
                    default_value=7.875,
                ),
                "target_region": str(seed_item.get("target_region") or "via_barrel"),
                "center_source": "padstack_instances",
                "discovery_method": "signal_padstack_instances_spanning_layer",
            }
        )
    return entries


def _primitive_layers_with_signal_centers(edb: Any, instances: list[Any]) -> list[str]:
    centers = [_padstack_instance_center(instance) for instance in instances]
    layers = []
    modeler = getattr(edb, "modeler", None)
    for primitive in _modeler_primitives(modeler) if modeler is not None else []:
        if _primitive_is_void(primitive):
            continue
        layer = _primitive_layer(primitive)
        if not layer or layer in layers:
            continue
        try:
            if any(_point_inside_shape(primitive, center) for center in centers):
                layers.append(layer)
        except ValueError:
            continue
    return layers


def _plane_shapes_containing_centers(
    edb: Any,
    layer: str,
    centers: list[tuple[float, float]],
) -> list[Any]:
    modeler = getattr(edb, "modeler", None)
    if modeler is None:
        return []
    candidates = []
    for primitive in _modeler_primitives(modeler):
        if _primitive_is_void(primitive):
            continue
        if _normalize_layer_name(_primitive_layer(primitive)) != _normalize_layer_name(layer):
            continue
        try:
            if any(_point_inside_shape(primitive, center) for center in centers):
                candidates.append(primitive)
        except ValueError:
            continue
    return candidates


def _layers_spanned_by_instances(instances: list[Any], layer_order: list[str]) -> list[str]:
    layers = []
    for instance in instances:
        for layer in _layers_for_instance(instance, layer_order):
            if layer not in layers:
                layers.append(layer)
    return layers


def _layers_for_instance(instance: Any, layer_order: list[str]) -> list[str]:
    start = str(getattr(instance, "start_layer", "") or "")
    stop = str(getattr(instance, "stop_layer", "") or "")
    if not start and not stop:
        return []
    if not layer_order:
        return [layer for layer in (start, stop) if layer]
    index_by_normalized = {
        _normalize_layer_name(layer): index
        for index, layer in enumerate(layer_order)
    }
    start_index = index_by_normalized.get(_normalize_layer_name(start))
    stop_index = index_by_normalized.get(_normalize_layer_name(stop))
    if start_index is None or stop_index is None:
        return [layer for layer in (start, stop) if layer]
    low, high = sorted((start_index, stop_index))
    return layer_order[low : high + 1]


def _seed_items(seed: Mapping[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = seed.get(key)
        if isinstance(value, list):
            result = []
            for item in value:
                if isinstance(item, Mapping):
                    result.append(dict(item))
                elif str(item).strip():
                    result.append({"layer": str(item).strip()})
            return result
    return []


def _seed_item_for_layer(items: list[dict[str, Any]], layer: str) -> dict[str, Any]:
    for item in items:
        if layer in _item_layers(item):
            return item
    return {}


def _item_layers(item: Mapping[str, Any]) -> list[str]:
    raw = item.get("layers")
    if raw is None:
        raw = item.get("layer")
    if isinstance(raw, list):
        return [str(layer) for layer in raw if str(layer).strip()]
    if raw is not None and str(raw).strip():
        return [str(raw)]
    return []


def _nearest_pair_instance_ids(instances: list[Any]) -> list[str]:
    if len(instances) <= 2:
        return [_instance_id(instance) for instance in instances]
    best_pair: tuple[Any, Any] | None = None
    best_distance = math.inf
    for left_index, left in enumerate(instances):
        left_center = _padstack_instance_center(left)
        for right in instances[left_index + 1 :]:
            right_center = _padstack_instance_center(right)
            distance = math.dist(left_center, right_center)
            if distance < best_distance:
                best_distance = distance
                best_pair = (left, right)
    return [] if best_pair is None else [_instance_id(best_pair[0]), _instance_id(best_pair[1])]


def _instance_id(instance: Any) -> str:
    value = getattr(instance, "id", None)
    if value is None:
        value = getattr(instance, "name", None)
    return str(value)


def _instance_center_record(instance: Any) -> dict[str, Any]:
    x, y = _padstack_instance_center(instance)
    return {
        "id": _instance_id(instance),
        "name": str(getattr(instance, "name", "") or ""),
        "net": str(getattr(instance, "net_name", "") or ""),
        "padstack": str(getattr(instance, "padstack_definition", "") or ""),
        "start_layer": str(getattr(instance, "start_layer", "") or ""),
        "stop_layer": str(getattr(instance, "stop_layer", "") or ""),
        "position": {"x": x, "y": y, "unit": "m"},
    }


def _target_radius(
    item: Mapping[str, Any],
    geometry_constraints: Mapping[str, Any],
    constraint_key: str,
    *,
    default_value: float,
) -> dict[str, Any]:
    value = item.get("target_radius")
    if isinstance(value, Mapping):
        return dict(value)
    if value is not None:
        return {"value": float(value), "unit": "mil"}
    constraints = geometry_constraints.get(constraint_key)
    if isinstance(constraints, Mapping):
        if constraint_key == "anti_pad" and constraints.get("max_radius_mil") is not None:
            return {"value": float(constraints["max_radius_mil"]), "unit": "mil"}
        if constraint_key == "non_functional_pad" and constraints.get("min_radius_mil") is not None:
            return {"value": float(constraints["min_radius_mil"]), "unit": "mil"}
    return {"value": default_value, "unit": "mil"}


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in list(value or []) if str(item).strip()]


def _inventory_summary(inventory: Mapping[str, Any]) -> dict[str, Any]:
    anti_pad = list(inventory.get("anti_pad_shape_layers") or [])
    nfp = list(inventory.get("non_functional_pad_layers") or [])
    return {
        "status": "succeeded",
        "source": inventory.get("source"),
        "discovered_center_count": inventory.get("discovered_center_count", 0),
        "anti_pad_candidate_count": len(anti_pad),
        "non_functional_pad_candidate_count": len(nfp),
        "candidate_action_count": len(anti_pad) + len(nfp),
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
