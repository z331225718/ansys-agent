from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from aedt_agent.infrastructure.brd_real_build import RealAedtEnvironment


EDITED_PROJECT_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.aedt$", re.IGNORECASE)
SUPPORTED_ACTIONS = {
    "anti_pad.enlarge",
    "non_functional_pad.add_or_enlarge",
}


@dataclass(frozen=True)
class BrdModelEditRequest:
    project_path: Path
    artifact_dir: Path
    actions: list[dict[str, Any]]
    environment: RealAedtEnvironment = field(default_factory=RealAedtEnvironment)
    edited_project_name: str = ""
    project_copy_mode: str = "checkpoint_copy"


@dataclass(frozen=True)
class BrdModelEditResult:
    edited_project_path: str
    edited_edb_path: str
    manifest_path: str
    summary: dict[str, Any]


class BrdModelEditAdapter:
    def __init__(
        self,
        *,
        edb_factory: Callable[..., Any] | None = None,
        hfss3dlayout_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._edb_factory = edb_factory
        self._hfss3dlayout_factory = hfss3dlayout_factory

    def run(self, request: BrdModelEditRequest) -> BrdModelEditResult:
        _validate_request(request)
        _preflight_actions(request.actions)
        project_path = request.project_path.resolve()
        artifact_dir = request.artifact_dir.resolve()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        source_digest = _sha256_file(project_path)
        source_project_record = _artifact_record(project_path)
        source_edb_record = _artifact_record(_sidecar_edb(project_path))
        manifest_path = artifact_dir / "model_edit_manifest.json"

        if request.project_copy_mode == "working_project":
            edited_project = project_path
            edited_edb = _sidecar_edb(project_path)
            check_source_unchanged = False
        else:
            edited_project = _edited_project_path(request)
            edited_edb = edited_project.with_suffix(".aedb")
            check_source_unchanged = True

        try:
            if request.project_copy_mode != "working_project":
                _prepare_project_bundle(project_path, edited_project)

            aedt_variables = _aedt_project_variables_for_actions(
                request.actions
            )
            if aedt_variables and self._should_sync_aedt_variables():
                _set_aedt_project_variables(
                    edited_project,
                    request.environment,
                    aedt_variables,
                    hfss3dlayout_class=self._hfss3dlayout_class(),
                )

            pre_edit_digest = _project_bundle_digest(edited_project, edited_edb)
            changes: list[dict[str, Any]] = []
            edb = self._edb_class()(
                edbpath=str(edited_edb),
                version=request.environment.version,
                grpc=_grpc_mode(request.environment.edb_backend),
            )
            try:
                for index, action in enumerate(request.actions):
                    changes.extend(_apply_action(edb, action, index=index))
                save = getattr(edb, "save", None)
                if callable(save):
                    save()
            finally:
                _close_edb(edb)
        except Exception:
            if check_source_unchanged:
                _remove_project_bundle(edited_project)
            raise

        if check_source_unchanged and _sha256_file(project_path) != source_digest:
            raise RuntimeError("source AEDT project changed during model edit")
        persistence_check = self._verify_persisted_edit(
            request,
            edited_project,
            edited_edb,
            changes,
            pre_edit_digest=pre_edit_digest,
        )

        summary = {
            "status": "succeeded",
            "adapter": "pyedb_geometry_model_edit",
            "source_project": str(project_path),
            "edited_project": str(edited_project),
            "edited_edb": str(edited_edb),
            "project_copy_mode": request.project_copy_mode,
            "action_count": len(request.actions),
            "change_count": len(changes),
            "raw_project": "artifact_only",
            "persistence_check": persistence_check,
            "changes": changes,
        }
        manifest = {
            "version": 1,
            "input": {
                "source_project": source_project_record,
                "source_edb": source_edb_record,
            },
            "outputs": {
                "edited_project": _artifact_record(edited_project),
                "edited_edb": _artifact_record(edited_edb),
            },
            "summary": summary,
        }
        _atomic_write_json(manifest_path, manifest)
        return BrdModelEditResult(
            edited_project_path=str(edited_project),
            edited_edb_path=str(edited_edb),
            manifest_path=str(manifest_path),
            summary=summary,
        )

    def _edb_class(self) -> Callable[..., Any]:
        if self._edb_factory is not None:
            return self._edb_factory
        from pyedb import Edb

        return Edb

    def _hfss3dlayout_class(self) -> Callable[..., Any]:
        if self._hfss3dlayout_factory is not None:
            return self._hfss3dlayout_factory
        from ansys.aedt.core import Hfss3dLayout

        return Hfss3dLayout

    def _should_sync_aedt_variables(self) -> bool:
        return (
            self._edb_factory is None
            or self._hfss3dlayout_factory is not None
        )

    def _verify_persisted_edit(
        self,
        request: BrdModelEditRequest,
        edited_project: Path,
        edited_edb: Path,
        changes: list[dict[str, Any]],
        *,
        pre_edit_digest: str,
    ) -> dict[str, Any]:
        post_edit_digest = _project_bundle_digest(edited_project, edited_edb)
        if post_edit_digest == pre_edit_digest:
            raise RuntimeError(
                "model edit did not persist any AEDT/AEDB bundle changes"
            )
        expected_variables = _expected_persisted_variables(changes)
        if expected_variables:
            verifier = self._edb_class()(
                edbpath=str(edited_edb),
                version=request.environment.version,
                grpc=_grpc_mode(request.environment.edb_backend),
            )
            try:
                for variable_name in expected_variables:
                    if not _edb_variable_exists(verifier, variable_name):
                        raise RuntimeError(
                            "model edit parameterization did not persist: "
                            f"{variable_name}"
                        )
            finally:
                _close_edb(verifier)
        return {
            "status": "passed",
            "bundle_digest_before": pre_edit_digest,
            "bundle_digest_after": post_edit_digest,
            "verified_variables": sorted(expected_variables),
        }


def _validate_request(request: BrdModelEditRequest) -> None:
    project_path = Path(request.project_path)
    if project_path.suffix.casefold() != ".aedt":
        raise ValueError("project_path must end with .aedt")
    if not project_path.is_file():
        raise FileNotFoundError(
            f"project_path not found: {project_path}"
        )
    edb_path = _sidecar_edb(project_path)
    if not edb_path.is_dir():
        raise FileNotFoundError(f"sidecar AEDB not found: {edb_path}")
    if not request.actions:
        raise ValueError("at least one model edit action is required")
    if request.edited_project_name and not EDITED_PROJECT_NAME.fullmatch(
        request.edited_project_name
    ):
        raise ValueError("edited_project_name must be a safe .aedt filename")
    if not request.environment.version.strip():
        raise ValueError("AEDT version is required")
    if request.project_copy_mode not in {"checkpoint_copy", "working_project"}:
        raise ValueError(
            "project_copy_mode must be checkpoint_copy or working_project"
        )
    if request.project_copy_mode == "working_project" and request.edited_project_name:
        raise ValueError(
            "edited_project_name is not used with working_project mode"
        )


def _preflight_actions(actions: list[dict[str, Any]]) -> None:
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise ValueError(f"model edit action {index} must be an object")
        action_type = str(action.get("action_type") or "")
        if action_type not in SUPPORTED_ACTIONS:
            raise ValueError(f"unsupported model edit action_type: {action_type}")
        _layers(action)
        _parameter_name(action)
        if action_type == "anti_pad.enlarge":
            _preflight_antipad_action(action)
        else:
            _preflight_non_functional_pad_action(action)


def _preflight_antipad_action(action: dict[str, Any]) -> None:
    _parasitic_target(action)
    _target_void_diameter_m(action)
    center_ids = _center_padstack_instance_ids(action)
    manual_centers: list[tuple[float, float]] = []
    if not center_ids:
        manual_centers = _via_centers(action)
    if not _truthy(action.get("allow_auto_shape_select", False)):
        shape_ids = _shape_ids(action)
        if not shape_ids:
            raise ValueError(
                "plane_shape_ids are required for anti_pad.enlarge unless "
                "allow_auto_shape_select is true"
            )
    if not _bridge_requested(action):
        return
    bridge_ids = _bridge_center_padstack_instance_ids(action)
    bridge_centers = _bridge_via_centers(action)
    if bridge_ids and bridge_centers:
        raise ValueError(
            "bridge_between_vias must use either "
            "bridge_center_padstack_instance_ids or bridge_via_centers, not both"
        )
    if bridge_ids and len(bridge_ids) != 2:
        raise ValueError(
            "bridge_between_vias requires exactly two "
            "bridge_center_padstack_instance_ids"
        )
    if bridge_centers and len(bridge_centers) != 2:
        raise ValueError(
            "bridge_between_vias requires exactly two bridge_via_centers"
        )
    if bridge_ids or bridge_centers:
        return
    center_count = len(center_ids) if center_ids else len(manual_centers)
    if center_count != 2:
        raise ValueError(
            "bridge_between_vias requires exactly two via centers or explicit "
            "bridge_center_padstack_instance_ids"
        )


def _preflight_non_functional_pad_action(action: dict[str, Any]) -> None:
    implementation = str(
        _first_present(action, "implementation", "edit_mode") or "shape"
    ).casefold()
    if implementation in {"padstack", "pad_by_layer", "legacy_padstack"}:
        _required_string(action, "padstack")
        return
    if implementation not in {"shape", "circle_shape", "primitive"}:
        raise ValueError(
            "non_functional_pad.add_or_enlarge implementation must be shape "
            "or legacy_padstack"
        )
    _target_void_diameter_m(action)
    center_ids = _center_padstack_instance_ids(action)
    if center_ids:
        return
    centers = _via_centers(action)
    _non_functional_pad_nets(action, len(centers))


def _edited_project_path(request: BrdModelEditRequest) -> Path:
    name = (
        request.edited_project_name
        if request.edited_project_name
        else f"{request.project_path.stem}.edited.aedt"
    )
    return request.artifact_dir.resolve() / name


def _prepare_project_bundle(source_project: Path, edited_project: Path) -> None:
    edited_project.parent.mkdir(parents=True, exist_ok=True)
    _remove_project_bundle(edited_project)
    edited_edb = edited_project.with_suffix(".aedb")
    shutil.copy2(source_project, edited_project)
    shutil.copytree(_sidecar_edb(source_project), edited_edb)


def _remove_project_bundle(project_path: Path) -> None:
    edited_edb = project_path.with_suffix(".aedb")
    edited_results = Path(f"{project_path}results")
    lock = Path(f"{project_path}.lock")
    for path in (project_path, edited_edb, edited_results, lock):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink(missing_ok=True)


def _apply_action(
    edb: Any,
    action: dict[str, Any],
    *,
    index: int,
) -> list[dict[str, Any]]:
    action_type = str(action.get("action_type") or "")
    if action_type not in SUPPORTED_ACTIONS:
        raise ValueError(f"unsupported model edit action_type: {action_type}")
    if action_type == "anti_pad.enlarge":
        return _apply_antipad_void_action(edb, action, index=index)
    return _apply_non_functional_pad_action(edb, action, index=index)


def _apply_non_functional_pad_action(
    edb: Any,
    action: dict[str, Any],
    *,
    index: int,
) -> list[dict[str, Any]]:
    implementation = str(
        _first_present(action, "implementation", "edit_mode") or "shape"
    ).casefold()
    if implementation in {"shape", "circle_shape", "primitive"}:
        return _apply_non_functional_pad_shape_action(
            edb,
            action,
            index=index,
        )
    if implementation in {"padstack", "pad_by_layer", "legacy_padstack"}:
        return _apply_non_functional_pad_padstack_action(
            edb,
            action,
            index=index,
        )
    raise ValueError(
        "non_functional_pad.add_or_enlarge implementation must be shape "
        "or legacy_padstack"
    )


def _apply_non_functional_pad_padstack_action(
    edb: Any,
    action: dict[str, Any],
    *,
    index: int,
) -> list[dict[str, Any]]:
    padstack_name = _required_string(action, "padstack")
    layers = _layers(action)
    definition = _padstack_definition(edb, padstack_name)
    collection = getattr(definition, "pad_by_layer")
    changes = []
    for requested_layer in layers:
        layer = _resolve_layer(collection, requested_layer)
        pad = collection[layer]
        before = _pad_snapshot(pad)
        target_m = _target_diameter_m(before, action)
        _validate_target(before, target_m, action)
        _set_circle_diameter(pad, _target_diameter_edb_string(action, target_m))
        after = _pad_snapshot(pad)
        changes.append(
            {
                "action_index": index,
                "action_type": "non_functional_pad.add_or_enlarge",
                "padstack": padstack_name,
                "requested_layer": requested_layer,
                "layer": layer,
                "property": "regular_pad",
                "implementation": "legacy_padstack",
                "before": before,
                "after": after,
            }
        )
    return changes


def _apply_non_functional_pad_shape_action(
    edb: Any,
    action: dict[str, Any],
    *,
    index: int,
) -> list[dict[str, Any]]:
    layers = _layers(action)
    targets = _non_functional_pad_targets(edb, action)
    radius = _target_non_functional_pad_radius(edb, action)
    _validate_void_target(radius["diameter_m"], action)
    changes = []
    for requested_layer in layers:
        layer = _resolve_modeler_layer(edb, requested_layer)
        created_shapes = []
        for target in targets["targets"]:
            circle = _create_signal_circle_shape(
                edb,
                layer,
                target["center"],
                radius["expression"],
                target["net"],
            )
            created_shapes.append(
                {
                    "type": "circle_shape",
                    "primitive": _primitive_summary(circle),
                    "net": target["net"],
                    "center": {
                        "x": target["center"][0],
                        "y": target["center"][1],
                        "unit": "m",
                    },
                    "radius_m": radius["radius_m"],
                    "diameter_m": radius["diameter_m"],
                    "radius_expression": radius["expression"],
                    "center_refs": target["refs"],
                }
            )
        changes.append(
            {
                "action_index": index,
                "action_type": "non_functional_pad.add_or_enlarge",
                "requested_layer": requested_layer,
                "layer": layer,
                "property": "signal_circle_shape",
                "implementation": "shape",
                "center_source": targets["source"],
                "center_refs": targets["refs"],
                "parameters": radius.get("parameter", {}),
                "created_shapes": created_shapes,
            }
        )
    return changes


def _apply_antipad_void_action(
    edb: Any,
    action: dict[str, Any],
    *,
    index: int,
) -> list[dict[str, Any]]:
    layers = _layers(action)
    center_plan = _antipad_center_plan(edb, action)
    centers = center_plan["centers"]
    bridge_center_plan = (
        _antipad_bridge_center_plan(edb, action, center_plan)
        if _bridge_requested(action)
        else None
    )
    radius = _target_void_radius(edb, action)
    target_diameter_m = radius["diameter_m"]
    radius_m = radius["radius_m"]
    radius_expression = radius["expression"]
    _validate_void_target(target_diameter_m, action)
    changes = []
    for requested_layer in layers:
        layer = _resolve_modeler_layer(edb, requested_layer)
        _validate_antipad_layer(layer, action)
        shapes = _plane_shapes_for_action(edb, action, layer, centers)
        created_voids = []
        for center in centers:
            circle = _create_circle_void(
                edb,
                layer,
                center,
                radius_expression,
            )
            circle_shapes = _shapes_containing_any_point(shapes, [center])
            for shape in circle_shapes:
                _add_void(edb, shape, circle)
            created_voids.append(
                {
                    "type": "circle",
                    "center": {"x": center[0], "y": center[1], "unit": "m"},
                    "radius_m": radius_m,
                    "diameter_m": target_diameter_m,
                    "radius_expression": radius_expression,
                    "added_to_shapes": [
                        _primitive_id(shape) for shape in circle_shapes
                    ],
                }
            )
        if _bridge_requested(action):
            assert bridge_center_plan is not None
            bridge_centers = bridge_center_plan["centers"]
            bridge = _bridge_rectangle(
                edb,
                bridge_centers,
                radius_m,
                action,
                width_expression=radius_expression,
            )
            bridge_primitive = _create_bridge_void(edb, layer, bridge)
            bridge_shapes = _shapes_containing_all_points(
                shapes,
                bridge_centers + [bridge["center"]],
            )
            if not bridge_shapes:
                raise ValueError(
                    "bridge_between_vias requires one selected plane shape "
                    "covering both via centers and the bridge center"
                )
            for shape in bridge_shapes:
                _add_void(edb, shape, bridge_primitive)
            bridge["added_to_shapes"] = [
                _primitive_id(shape) for shape in bridge_shapes
            ]
            bridge["center_source"] = bridge_center_plan["source"]
            bridge["center_refs"] = bridge_center_plan["refs"]
            bridge["via_centers"] = [
                {"x": center[0], "y": center[1], "unit": "m"}
                for center in bridge_centers
            ]
            created_voids.append(bridge)
        changes.append(
            {
                "action_index": index,
                "action_type": "anti_pad.enlarge",
                "requested_layer": requested_layer,
                "layer": layer,
                "property": "plane_shape_void",
                "parasitic_target": center_plan["parasitic_target"],
                "center_source": center_plan["source"],
                "center_refs": center_plan["refs"],
                "selected_shapes": [_primitive_summary(shape) for shape in shapes],
                "via_centers": [
                    {"x": center[0], "y": center[1], "unit": "m"}
                    for center in centers
                ],
                "parameters": radius.get("parameter", {}),
                "shape_presence_check": "passed",
                "created_voids": created_voids,
            }
        )
    return changes


def _antipad_center_plan(edb: Any, action: dict[str, Any]) -> dict[str, Any]:
    parasitic_target = _parasitic_target(action)
    instance_ids = _center_padstack_instance_ids(action)
    if instance_ids:
        return _centers_from_padstack_instances(
            edb,
            instance_ids,
            parasitic_target=parasitic_target,
        )
    centers = _via_centers(action)
    source = _center_source(action) or "manual_reviewed_coordinates"
    return {
        "centers": centers,
        "source": source,
        "refs": [],
        "parasitic_target": parasitic_target,
    }


def _antipad_bridge_center_plan(
    edb: Any,
    action: dict[str, Any],
    fallback_plan: dict[str, Any],
) -> dict[str, Any]:
    parasitic_target = _parasitic_target(action)
    instance_ids = _bridge_center_padstack_instance_ids(action)
    if instance_ids:
        return _centers_from_padstack_instances(
            edb,
            instance_ids,
            parasitic_target=parasitic_target,
        )
    centers = _bridge_via_centers(action)
    if centers:
        return {
            "centers": centers,
            "source": "manual_reviewed_bridge_coordinates",
            "refs": [],
            "parasitic_target": parasitic_target,
        }
    return fallback_plan


def _non_functional_pad_targets(
    edb: Any,
    action: dict[str, Any],
) -> dict[str, Any]:
    instance_ids = _center_padstack_instance_ids(action)
    if instance_ids:
        plan = _centers_from_padstack_instances(
            edb,
            instance_ids,
            parasitic_target=str(
                _first_present(action, "parasitic_target")
                or "via_barrel_non_functional_pad"
            ),
        )
        targets_by_key: dict[tuple[float, float, str], dict[str, Any]] = {}
        for ref in plan["refs"]:
            net_name = str(ref.get("net") or "").strip()
            if not net_name:
                raise ValueError(
                    "padstack instance net_name is required for "
                    "non-functional pad circle shape"
                )
            center = plan["centers"][int(ref["center_index"])]
            key = (round(center[0], 12), round(center[1], 12), net_name)
            if key not in targets_by_key:
                targets_by_key[key] = {
                    "center": center,
                    "net": net_name,
                    "refs": [],
                }
            targets_by_key[key]["refs"].append(ref)
        return {
            "targets": list(targets_by_key.values()),
            "source": "padstack_instances",
            "refs": plan["refs"],
        }

    centers = _via_centers(action)
    nets = _non_functional_pad_nets(action, len(centers))
    return {
        "targets": [
            {
                "center": center,
                "net": net_name,
                "refs": [],
            }
            for center, net_name in zip(centers, nets, strict=True)
        ],
        "source": _center_source(action) or "manual_reviewed_coordinates",
        "refs": [],
    }


def _non_functional_pad_nets(
    action: dict[str, Any],
    center_count: int,
) -> list[str]:
    value = _first_present(
        action,
        "net_names",
        "nets",
        "signal_nets",
        "net_name",
        "signal_net",
    )
    if value is None:
        raise ValueError(
            "net_names are required when non-functional pad centers are "
            "provided as manual coordinates"
        )
    if isinstance(value, str):
        values = [value] * center_count if center_count > 1 else [value]
    else:
        values = [str(item) for item in list(value or [])]
    nets = [str(item).strip() for item in values if str(item).strip()]
    if len(nets) != center_count:
        raise ValueError(
            "net_names must contain one signal net per non-functional pad "
            "center"
        )
    return nets


def _parasitic_target(action: dict[str, Any]) -> str:
    value = _first_present(action, "parasitic_target", "center_role")
    if value is None:
        raise ValueError(
            "parasitic_target is required for anti_pad.enlarge so the "
            "void center is tied to the physical parasitic being reduced"
        )
    result = str(value).strip()
    if not result:
        raise ValueError("parasitic_target is required for anti_pad.enlarge")
    return result


def _center_source(action: dict[str, Any]) -> str:
    value = _first_present(action, "center_source", "center_reference")
    return str(value or "").strip()


def _center_padstack_instance_ids(action: dict[str, Any]) -> list[str]:
    value = _first_present(
        action,
        "center_padstack_instance_ids",
        "padstack_instance_ids",
        "center_instance_ids",
    )
    return _instance_id_list(value, "center_padstack_instance_ids")


def _bridge_center_padstack_instance_ids(action: dict[str, Any]) -> list[str]:
    value = _first_present(
        action,
        "bridge_center_padstack_instance_ids",
        "bridge_padstack_instance_ids",
        "bridge_center_instance_ids",
    )
    return _instance_id_list(value, "bridge_center_padstack_instance_ids")


def _instance_id_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int)):
        values = [value]
    else:
        values = list(value or [])
    result = [str(item).strip() for item in values if str(item).strip()]
    if not result:
        raise ValueError(f"{field_name} must not be empty")
    return result


def _centers_from_padstack_instances(
    edb: Any,
    instance_ids: list[str],
    *,
    parasitic_target: str,
) -> dict[str, Any]:
    instances = getattr(getattr(edb, "padstacks", None), "instances", None)
    if not isinstance(instances, dict):
        raise ValueError("EDB padstack instances are required for center lookup")
    refs = []
    centers: list[tuple[float, float]] = []
    center_index_by_key: dict[tuple[float, float], int] = {}
    for requested_id in instance_ids:
        instance = _padstack_instance_by_id(instances, requested_id)
        center = _padstack_instance_center(instance)
        key = (round(center[0], 12), round(center[1], 12))
        if key not in center_index_by_key:
            center_index_by_key[key] = len(centers)
            centers.append(center)
        refs.append(
            {
                "requested_id": requested_id,
                "id": _instance_attr(instance, "id"),
                "name": _instance_attr(instance, "name"),
                "net": _instance_attr(instance, "net_name"),
                "padstack": _instance_attr(instance, "padstack_definition"),
                "start_layer": _instance_attr(instance, "start_layer"),
                "stop_layer": _instance_attr(instance, "stop_layer"),
                "position": {
                    "x": center[0],
                    "y": center[1],
                    "unit": "m",
                },
                "center_index": center_index_by_key[key],
            }
        )
    if not centers:
        raise ValueError("no centers resolved from padstack instances")
    return {
        "centers": centers,
        "source": "padstack_instances",
        "refs": refs,
        "parasitic_target": parasitic_target,
    }


def _padstack_instance_by_id(instances: dict[Any, Any], requested_id: str) -> Any:
    if requested_id in instances:
        return instances[requested_id]
    for key, instance in instances.items():
        if str(key) == requested_id:
            return instance
        if str(getattr(instance, "id", "")) == requested_id:
            return instance
        if str(getattr(instance, "name", "")) == requested_id:
            return instance
    raise ValueError(f"padstack instance not found: {requested_id}")


def _padstack_instance_center(instance: Any) -> tuple[float, float]:
    position = getattr(instance, "position", None)
    if position is None:
        raise ValueError("padstack instance has no position")
    values = list(position)
    if len(values) < 2:
        raise ValueError("padstack instance position must include x and y")
    center = (float(values[0]), float(values[1]))
    if not math.isfinite(center[0]) or not math.isfinite(center[1]):
        raise ValueError("padstack instance center must be finite")
    return center


def _instance_attr(instance: Any, attr: str) -> Any:
    value = getattr(instance, attr, None)
    if value is None:
        return None
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _via_centers(action: dict[str, Any]) -> list[tuple[float, float]]:
    value = action.get("via_centers")
    if value is None:
        value = action.get("via_center")
    if value is None and isinstance(action.get("target"), dict):
        value = action["target"].get("via_centers") or action["target"].get(
            "via_center"
        )
    if value is None:
        raise ValueError("via_centers are required for anti_pad.enlarge")
    if isinstance(value, dict):
        items = [value]
    elif _looks_like_xy_pair(value):
        items = [value]
    else:
        items = list(value or [])
    if not items:
        raise ValueError("via_centers are required for anti_pad.enlarge")

    action_unit = str(action.get("unit") or "m")
    centers: list[tuple[float, float]] = []
    for item in items:
        centers.append(_via_center(item, action_unit))
    return centers


def _bridge_via_centers(action: dict[str, Any]) -> list[tuple[float, float]]:
    value = _first_present(action, "bridge_via_centers", "bridge_centers")
    if value is None:
        return []
    if isinstance(value, dict):
        items = [value]
    elif _looks_like_xy_pair(value):
        items = [value]
    else:
        items = list(value or [])
    action_unit = str(action.get("unit") or "m")
    return [_via_center(item, action_unit) for item in items]


def _looks_like_xy_pair(value: Any) -> bool:
    if isinstance(value, (str, bytes, dict)):
        return False
    try:
        items = list(value)
    except TypeError:
        return False
    if items and isinstance(items[0], dict):
        if any(key in items[0] for key in ("x", "y", "center_x", "center_y")):
            return False
    return len(items) >= 2 and all(
        isinstance(item, (int, float, str, dict)) for item in items[:2]
    )


def _via_center(value: Any, default_unit: str) -> tuple[float, float]:
    if isinstance(value, dict):
        unit = str(value.get("unit") or default_unit)
        x_value = value.get("x", value.get("center_x"))
        y_value = value.get("y", value.get("center_y"))
    else:
        unit = default_unit
        items = list(value)
        if len(items) < 2:
            raise ValueError("via center must include x and y")
        x_value = items[0]
        y_value = items[1]
    if x_value is None or y_value is None:
        raise ValueError("via center must include x and y")
    x = _coordinate_to_meters(x_value, unit)
    y = _coordinate_to_meters(y_value, unit)
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("via center coordinates must be finite")
    return (x, y)


def _coordinate_to_meters(value: Any, default_unit: str) -> float:
    if isinstance(value, dict):
        return _dimension_to_meters(value)
    if isinstance(value, (int, float)):
        return float(value) * _unit_factor(default_unit.casefold())
    return _dimension_to_meters(value)


def _target_void_diameter_m(action: dict[str, Any]) -> float:
    target_spec = _first_present(
        action,
        "target_diameter",
        "void_diameter",
        "diameter",
        "new_diameter",
    )
    if target_spec is not None:
        return _dimension_to_meters(target_spec)
    radius_spec = _first_present(
        action,
        "target_radius",
        "void_radius",
        "radius",
    )
    if radius_spec is not None:
        return 2.0 * _dimension_to_meters(radius_spec)
    delta_spec = action.get("delta")
    current_spec = _first_present(
        action,
        "current_diameter",
        "previous_diameter",
        "base_diameter",
    )
    if delta_spec is not None and current_spec is not None:
        return _dimension_to_meters(current_spec) + _dimension_to_meters(
            delta_spec
        )
    if delta_spec is not None:
        raise ValueError(
            "anti_pad.enlarge requires target_diameter or radius; delta-only "
            "cannot be applied to plane-shape void geometry"
        )
    raise ValueError("target_diameter or radius is required for anti_pad.enlarge")


def _target_void_radius(edb: Any, action: dict[str, Any]) -> dict[str, Any]:
    diameter_m = _target_void_diameter_m(action)
    radius_m = diameter_m / 2.0
    parameter_name = _parameter_name(action)
    if not parameter_name:
        return {
            "radius_m": radius_m,
            "diameter_m": diameter_m,
            "expression": radius_m,
        }
    radius_value = _target_radius_edb_string(action)
    variable_name = _project_variable_name(parameter_name)
    _set_project_variable(edb, variable_name, radius_value)
    return {
        "radius_m": radius_m,
        "diameter_m": diameter_m,
        "expression": variable_name,
        "parameter": {
            "name": parameter_name,
            "value": radius_value,
            "scope": "project",
            "expression": variable_name,
        },
    }


def _target_non_functional_pad_radius(
    edb: Any,
    action: dict[str, Any],
) -> dict[str, Any]:
    try:
        return _target_void_radius(edb, action)
    except ValueError as exc:
        message = str(exc)
        if "anti_pad.enlarge" in message:
            message = message.replace(
                "anti_pad.enlarge",
                "non_functional_pad.add_or_enlarge",
            )
        raise ValueError(message) from exc


def _target_radius_edb_string(action: dict[str, Any]) -> str:
    radius_spec = _first_present(
        action,
        "target_radius",
        "void_radius",
        "radius",
    )
    if radius_spec is not None:
        return _dimension_to_edb_string(radius_spec)
    target_spec = _first_present(
        action,
        "target_diameter",
        "void_diameter",
        "diameter",
        "new_diameter",
    )
    if target_spec is not None:
        return f"{_dimension_to_meters(target_spec) / 2.0}m"
    return f"{_target_void_diameter_m(action) / 2.0}m"


def _parameter_name(action: dict[str, Any]) -> str:
    value = _first_present(
        action,
        "parameter_name",
        "radius_parameter",
        "void_radius_parameter",
    )
    if value is None:
        return ""
    name = str(value).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"invalid parameter_name: {name}")
    return name


def _project_variable_name(name: str) -> str:
    return name if name.startswith("$") else f"${name}"


def _aedt_project_variables_for_actions(
    actions: list[dict[str, Any]],
) -> dict[str, str]:
    variables: dict[str, str] = {}
    for action in actions:
        parameter_name = _parameter_name(action)
        if not parameter_name:
            continue
        value = _target_radius_edb_string(action)
        variable_name = _project_variable_name(parameter_name)
        existing = variables.get(variable_name)
        if existing is not None and existing != value:
            raise ValueError(
                "conflicting target values for parameter_name: "
                f"{parameter_name}"
            )
        variables[variable_name] = value
    return variables


def _set_aedt_project_variables(
    project_path: Path,
    environment: RealAedtEnvironment,
    variables: dict[str, str],
    *,
    hfss3dlayout_class: Callable[..., Any],
) -> None:
    app = hfss3dlayout_class(
        project=str(project_path),
        version=environment.version,
        non_graphical=environment.non_graphical,
        new_desktop=True,
        close_on_exit=True,
        remove_lock=True,
    )
    try:
        for name, value in variables.items():
            app[name] = value
            if not _aedt_variable_resolves(app, name, value):
                raise RuntimeError(
                    "failed to define AEDT project variable before EDB edit: "
                    f"{name}"
                )
        save_project = getattr(app, "save_project", None)
        if not callable(save_project):
            raise ValueError("Hfss3dLayout.save_project is required")
        save_project()
    finally:
        release = getattr(app, "release_desktop", None)
        if callable(release):
            release(close_projects=True, close_desktop=True)


def _aedt_variable_resolves(app: Any, name: str, value: str) -> bool:
    try:
        expression = app[name]
    except Exception:
        variables = getattr(
            getattr(app, "variable_manager", None),
            "variables",
            None,
        )
        if isinstance(variables, dict) and name in variables:
            return True
        return False
    return str(expression) == value or bool(str(expression).strip())


def _set_design_variable(edb: Any, name: str, value: str) -> None:
    exists = getattr(edb, "variable_exists", None)
    change = getattr(edb, "change_design_variable_value", None)
    add = getattr(edb, "add_design_variable", None)
    if callable(exists) and _variable_exists_result(exists(name)):
        if not callable(change):
            raise ValueError("EDB change_design_variable_value is required")
        result = change(name, value)
    else:
        if not callable(add):
            raise ValueError("EDB add_design_variable is required")
        result = add(name, value)
    if _operation_failed(result):
        raise RuntimeError(f"failed to set design variable: {name}")


def _validate_void_target(target_m: float, action: dict[str, Any]) -> None:
    if target_m <= 0:
        raise ValueError("target diameter must be positive")
    limits = action.get("constraints") or action.get("limits") or {}
    minimum = _optional_dimension(limits, "min_diameter")
    maximum = _optional_dimension(limits, "max_diameter")
    max_delta = _optional_dimension(limits, "max_delta")
    current_spec = _first_present(
        action,
        "current_diameter",
        "previous_diameter",
        "base_diameter",
    )
    current = (
        _dimension_to_meters(current_spec)
        if current_spec is not None
        else None
    )
    if current is not None and target_m <= current:
        raise ValueError("target diameter must enlarge the current diameter")
    if minimum is not None and target_m < minimum:
        raise ValueError("target diameter is below min_diameter")
    if maximum is not None and target_m > maximum:
        raise ValueError("target diameter is above max_diameter")
    if max_delta is not None and current is not None:
        if abs(target_m - current) > max_delta:
            raise ValueError("target diameter delta exceeds max_delta")


def _resolve_modeler_layer(edb: Any, requested: str) -> str:
    modeler = _required_modeler(edb)
    primitives = _modeler_primitives(modeler)
    layers = []
    for primitive in primitives:
        layer = _primitive_layer(primitive)
        if layer and layer not in layers:
            layers.append(layer)
    if requested in layers:
        return requested
    normalized = _normalize_layer_name(requested)
    matches = [
        layer for layer in layers if _normalize_layer_name(layer) == normalized
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"ambiguous layer name: {requested}")
    stackup_layers = _edb_layer_names(edb)
    if requested in stackup_layers:
        return requested
    stackup_matches = [
        layer
        for layer in stackup_layers
        if _normalize_layer_name(layer) == normalized
    ]
    if len(stackup_matches) == 1:
        return stackup_matches[0]
    if len(stackup_matches) > 1:
        raise ValueError(f"ambiguous layer name: {requested}")
    return requested


def _edb_layer_names(edb: Any) -> list[str]:
    stackup = getattr(edb, "stackup", None)
    if stackup is None:
        return []
    layers: list[str] = []
    for attr in ("signal_layers", "layers", "all_layers"):
        value = getattr(stackup, attr, None)
        names = list(value.keys()) if isinstance(value, dict) else []
        for name in names:
            layer = str(name)
            if layer not in layers:
                layers.append(layer)
    return layers


def _plane_shapes_for_action(
    edb: Any,
    action: dict[str, Any],
    layer: str,
    centers: list[tuple[float, float]],
) -> list[Any]:
    modeler = _required_modeler(edb)
    candidates = _candidate_plane_shapes(modeler, layer, action)
    if not candidates:
        raise ValueError(
            "anti_pad.enlarge found no plane shape candidates on layer: "
            f"{layer}"
        )
    shape_ids = _shape_ids(action)
    if shape_ids:
        shapes = [
            primitive
            for primitive in candidates
            if str(_primitive_id(primitive)) in shape_ids
        ]
        missing = sorted(shape_ids - {str(_primitive_id(shape)) for shape in shapes})
        if missing:
            raise ValueError(
                "selected plane_shape_ids were not found on layer "
                f"{layer}: {', '.join(missing)}"
            )
    elif _truthy(action.get("allow_auto_shape_select", False)):
        shapes = _shapes_containing_any_point(candidates, centers)
    else:
        raise ValueError(
            "plane_shape_ids are required for anti_pad.enlarge unless "
            "allow_auto_shape_select is true"
        )
    if not shapes:
        raise ValueError(
            "anti_pad.enlarge selected no plane shapes around the via centers"
        )
    _check_centers_inside_shapes(shapes, centers, layer)
    _check_shapes_contain_some_center(shapes, centers, layer)
    return shapes


def _candidate_plane_shapes(
    modeler: Any,
    layer: str,
    action: dict[str, Any],
) -> list[Any]:
    primitives = _modeler_primitives(modeler, layer_name=layer, is_void=False)
    if not primitives:
        primitives = _modeler_primitives(modeler)
    net_filter = _shape_net_filter(action)
    candidates = []
    for primitive in primitives:
        if _primitive_is_void(primitive):
            continue
        if _primitive_is_path_like(primitive):
            continue
        if _normalize_layer_name(_primitive_layer(primitive)) != _normalize_layer_name(
            layer
        ):
            continue
        if net_filter and _primitive_net(primitive) not in net_filter:
            continue
        candidates.append(primitive)
    return candidates


def _modeler_primitives(modeler: Any, **filters: Any) -> list[Any]:
    get_primitives = getattr(modeler, "get_primitives", None)
    if not callable(get_primitives):
        return []
    try:
        value = get_primitives(**filters)
    except TypeError:
        value = get_primitives()
    return list(value or [])


def _required_modeler(edb: Any) -> Any:
    modeler = getattr(edb, "modeler", None)
    if modeler is None:
        raise ValueError("EDB modeler is required for anti_pad.enlarge")
    return modeler


def _shape_ids(action: dict[str, Any]) -> set[str]:
    value = action.get("plane_shape_ids")
    if value is None:
        value = action.get("shape_ids")
    if value is None and isinstance(action.get("target"), dict):
        value = action["target"].get("plane_shape_ids") or action["target"].get(
            "shape_ids"
        )
    if value is None:
        return set()
    if isinstance(value, (str, int)):
        values = [value]
    else:
        values = list(value)
    result = {str(item).strip() for item in values if str(item).strip()}
    return result


def _shape_net_filter(action: dict[str, Any]) -> set[str]:
    value = action.get("plane_net") or action.get("shape_net")
    if value is None and isinstance(action.get("target"), dict):
        value = action["target"].get("plane_net") or action["target"].get(
            "shape_net"
        )
    if value is None:
        return set()
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    return {str(item) for item in values}


def _check_centers_inside_shapes(
    shapes: list[Any],
    centers: list[tuple[float, float]],
    layer: str,
) -> None:
    for center in centers:
        if any(_point_inside_shape(shape, center) for shape in shapes):
            continue
        raise ValueError(
            "via center is not inside any selected plane shape on layer "
            f"{layer}: x={center[0]}, y={center[1]}"
        )


def _check_shapes_contain_some_center(
    shapes: list[Any],
    centers: list[tuple[float, float]],
    layer: str,
) -> None:
    for shape in shapes:
        if any(_point_inside_shape(shape, center) for center in centers):
            continue
        raise ValueError(
            "selected plane shape does not contain any via center on layer "
            f"{layer}: shape_id={_primitive_id(shape)}"
        )


def _shapes_containing_any_point(
    shapes: list[Any],
    points: list[tuple[float, float]],
) -> list[Any]:
    result = []
    for shape in shapes:
        if any(_point_inside_shape(shape, point) for point in points):
            result.append(shape)
    return result


def _shapes_containing_all_points(
    shapes: list[Any],
    points: list[tuple[float, float]],
) -> list[Any]:
    result = []
    for shape in shapes:
        if all(_point_inside_shape(shape, point) for point in points):
            result.append(shape)
    return result


def _point_inside_shape(shape: Any, point: tuple[float, float]) -> bool:
    polygon_data = getattr(shape, "polygon_data", None)
    is_inside = getattr(polygon_data, "is_inside", None)
    if not callable(is_inside):
        raise ValueError(
            "selected plane shape does not expose polygon_data.is_inside; "
            "cannot verify anti-pad void placement"
        )
    try:
        return bool(is_inside(point))
    except TypeError:
        return bool(is_inside(list(point)))


def _create_circle_void(
    edb: Any,
    layer: str,
    center: tuple[float, float],
    radius: float | str,
) -> Any:
    create_circle = getattr(_required_modeler(edb), "create_circle", None)
    if not callable(create_circle):
        raise ValueError("EDB modeler.create_circle is required")
    try:
        primitive = create_circle(layer, center[0], center[1], radius, net_name="")
    except TypeError:
        primitive = create_circle(
            layer_name=layer,
            x=center[0],
            y=center[1],
            radius=radius,
            net_name="",
        )
    if primitive is None:
        raise RuntimeError("failed to create circular anti-pad void")
    return primitive


def _create_signal_circle_shape(
    edb: Any,
    layer: str,
    center: tuple[float, float],
    radius: float | str,
    net_name: str,
) -> Any:
    if not net_name.strip():
        raise ValueError("signal net is required for non-functional pad shape")
    create_circle = getattr(_required_modeler(edb), "create_circle", None)
    if not callable(create_circle):
        raise ValueError("EDB modeler.create_circle is required")
    try:
        primitive = create_circle(
            layer,
            center[0],
            center[1],
            radius,
            net_name=net_name,
        )
    except TypeError:
        primitive = create_circle(
            layer_name=layer,
            x=center[0],
            y=center[1],
            radius=radius,
            net_name=net_name,
        )
    if primitive is None:
        raise RuntimeError("failed to create non-functional pad circle shape")
    return primitive


def _create_polygon_void(
    edb: Any,
    layer: str,
    points: list[tuple[float, float]] | list[list[str]],
) -> Any:
    create_polygon = getattr(_required_modeler(edb), "create_polygon", None)
    if not callable(create_polygon):
        raise ValueError("EDB modeler.create_polygon is required")
    try:
        primitive = create_polygon(points, layer, net_name="")
    except TypeError:
        primitive = create_polygon(points, layer)
    if primitive is None:
        raise RuntimeError("failed to create bridge anti-pad void")
    return primitive


def _create_bridge_void(edb: Any, layer: str, bridge: dict[str, Any]) -> Any:
    rectangle = bridge.get("rectangle")
    if rectangle:
        return _create_rectangle_void(edb, layer, rectangle)
    return _create_polygon_void(edb, layer, bridge["points"])


def _create_rectangle_void(
    edb: Any,
    layer: str,
    rectangle: dict[str, Any],
) -> Any:
    create_rectangle = getattr(_required_modeler(edb), "create_rectangle", None)
    if not callable(create_rectangle):
        raise ValueError("EDB modeler.create_rectangle is required")
    primitive = create_rectangle(
        layer,
        net_name="",
        lower_left_point=rectangle["lower_left_point"],
        upper_right_point=rectangle["upper_right_point"],
        representation_type=_rectangle_representation_type(
            rectangle["representation_type"]
        ),
    )
    if primitive is None:
        raise RuntimeError("failed to create rectangular anti-pad void")
    return primitive


def _add_void(edb: Any, shape: Any, void_shape: Any) -> None:
    modeler = _required_modeler(edb)
    failures: list[str] = []
    add_void = getattr(modeler, "add_void", None)
    if callable(add_void):
        if _try_add_void(add_void, shape, void_shape, failures, "modeler"):
            return
    primitive_add_void = getattr(shape, "add_void", None)
    if callable(primitive_add_void):
        if _try_add_void(
            primitive_add_void,
            shape,
            void_shape,
            failures,
            "primitive",
        ):
            return
    if not callable(add_void) and not callable(primitive_add_void):
        raise ValueError("EDB modeler.add_void or primitive.add_void is required")
    details = "; ".join(failures) if failures else "no add_void API succeeded"
    raise RuntimeError(f"failed to add anti-pad void to plane shape: {details}")


def _try_add_void(
    add_void: Any,
    shape: Any,
    void_shape: Any,
    failures: list[str],
    label: str,
) -> bool:
    call_shapes = (
        (shape, void_shape),
        (void_shape,),
    )
    for args in call_shapes:
        try:
            result = add_void(*args)
        except TypeError:
            continue
        except Exception as exc:
            failures.append(f"{label}.add_void raised {type(exc).__name__}: {exc}")
            return False
        if result is False:
            failures.append(f"{label}.add_void returned False")
            return False
        return True
    failures.append(f"{label}.add_void signature did not accept known arguments")
    return False


def _bridge_requested(action: dict[str, Any]) -> bool:
    return _truthy(action.get("bridge_between_vias", False)) or _truthy(
        action.get("add_bridge_rectangle", False)
    )


def _bridge_rectangle(
    edb: Any,
    centers: list[tuple[float, float]],
    radius_m: float,
    action: dict[str, Any],
    *,
    width_expression: float | str | None = None,
) -> dict[str, Any]:
    if len(centers) != 2:
        raise ValueError(
            "bridge_between_vias requires exactly two via centers"
        )
    first, second = centers
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    pitch = math.hypot(dx, dy)
    if pitch <= 0:
        raise ValueError("via centers must not be identical")

    radius_expr = width_expression if width_expression is not None else radius_m
    rectangle = _axis_aligned_tangent_rectangle_args(
        first=first,
        second=second,
        radius_expr=radius_expr,
    )
    if rectangle:
        return {
            "type": "rectangle_bridge",
            "points": [],
            "rectangle": rectangle,
            "center": (
                (first[0] + second[0]) / 2.0,
                (first[1] + second[1]) / 2.0,
            ),
            "length_m": pitch,
            "length_expression": pitch,
            "width_m": 2.0 * radius_m,
            "width_expression": _scaled_expression(radius_expr, 2.0),
            "length_factor": 1.0,
            "width_factor": 2.0,
            "parameters": {},
            "via_pitch_m": pitch,
            "bridge_convention": "center_to_center_tangent_rectangle",
        }

    bridge_length_spec = _first_present(action, "bridge_length")
    length_factor = _bridge_factor(action, "bridge_length_factor", default=1.0)
    length_m = (
        _dimension_to_meters(bridge_length_spec)
        if bridge_length_spec is not None
        else pitch * length_factor
    )
    length_expr: float | str = (
        _dimension_to_edb_string(bridge_length_spec)
        if bridge_length_spec is not None
        else length_m
    )
    length_parameter = _optional_parameter_name(
        action,
        "bridge_length_parameter_name",
        "bridge_length_parameter",
    )
    length_parameter_scope = str(
        _first_present(action, "bridge_length_parameter_scope") or "project"
    ).casefold()
    if length_parameter:
        length_value = _bridge_length_value(action, length_m)
        if length_parameter_scope == "project":
            _set_project_variable(edb, length_parameter, length_value)
            length_expr = f"${length_parameter}"
        elif length_parameter_scope == "design":
            _set_design_variable(edb, length_parameter, length_value)
            length_expr = length_parameter
        else:
            raise ValueError(
                "bridge_length_parameter_scope must be project or design"
            )
    bridge_width_spec = _first_present(action, "bridge_width")
    width_factor = _bridge_factor(action, "bridge_width_factor", default=1.0)
    width_m = (
        _dimension_to_meters(bridge_width_spec)
        if bridge_width_spec is not None
        else radius_m * width_factor
    )
    width_expr: float | str = (
        _dimension_to_edb_string(bridge_width_spec)
        if bridge_width_spec is not None
        else _scaled_expression(
            width_expression if width_expression is not None else radius_m,
            width_factor,
        )
    )
    if length_m <= 0 or width_m <= 0:
        raise ValueError("bridge rectangle dimensions must be positive")
    ux = dx / pitch
    uy = dy / pitch
    px = -uy
    py = ux
    middle = ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)
    half_length = length_m / 2.0
    half_width = width_m / 2.0
    rectangle = _axis_aligned_rectangle_args(
        middle=middle,
        ux=ux,
        uy=uy,
        length_expr=length_expr,
        width_expr=width_expr,
    )
    points = (
        []
        if rectangle
        else _bridge_polygon_points(
            middle=middle,
            ux=ux,
            uy=uy,
            px=px,
            py=py,
            half_length=half_length,
            half_width=half_width,
        )
    )
    return {
        "type": "rectangle_bridge",
        "points": points,
        "rectangle": rectangle,
        "center": middle,
        "length_m": length_m,
        "length_expression": length_expr,
        "width_m": width_m,
        "width_expression": width_expr,
        "length_factor": length_factor,
        "width_factor": width_factor,
        "parameters": {
            "length": {
                "name": length_parameter,
                "value": _bridge_length_value(action, length_m)
                if length_parameter
                else "",
                "scope": length_parameter_scope,
            }
        }
        if length_parameter
        else {},
        "via_pitch_m": pitch,
    }


def _bridge_factor(
    action: dict[str, Any],
    key: str,
    *,
    default: float,
) -> float:
    value = _first_present(action, key)
    if value is None:
        return default
    result = float(value)
    if result <= 0:
        raise ValueError(f"{key} must be positive")
    return result


def _bridge_length_value(action: dict[str, Any], length_m: float) -> str:
    value = _first_present(action, "bridge_length_parameter_value")
    if value is not None:
        return _dimension_to_edb_string(value)
    return f"{length_m * 1000.0}mm"


def _scaled_expression(value: float | str, factor: float) -> float | str:
    if isinstance(value, str):
        if abs(factor - 1.0) < 1e-12:
            return value
        return f"{factor}*{value}"
    return float(value) * factor


def _set_project_variable(edb: Any, name: str, value: str) -> None:
    variable_name = name if name.startswith("$") else f"${name}"
    clean_name = name[1:] if name.startswith("$") else name
    exists = getattr(edb, "variable_exists", None)
    change = getattr(edb, "change_design_variable_value", None)
    add = getattr(edb, "add_project_variable", None)
    if callable(exists) and _variable_exists_result(exists(variable_name)):
        if not callable(change):
            raise ValueError("EDB change_design_variable_value is required")
        result = change(variable_name, value)
    else:
        if not callable(add):
            raise ValueError("EDB add_project_variable is required")
        result = add(clean_name, value)
    if _operation_failed(result):
        raise RuntimeError(f"failed to set project variable: {variable_name}")


def _variable_exists_result(result: Any) -> bool:
    if isinstance(result, tuple):
        return bool(result[0]) if result else False
    return bool(result)


def _operation_failed(result: Any) -> bool:
    if isinstance(result, tuple):
        return bool(result) and result[0] is False
    return result is False


def _axis_aligned_rectangle_args(
    *,
    middle: tuple[float, float],
    ux: float,
    uy: float,
    length_expr: float | str,
    width_expr: float | str,
) -> dict[str, Any]:
    if abs(uy) < 1e-12:
        lower_left = [
            _coordinate_with_offset(middle[0], -0.5, length_expr),
            _coordinate_with_offset(middle[1], -0.5, width_expr),
        ]
        upper_right = [
            _coordinate_with_offset(middle[0], 0.5, length_expr),
            _coordinate_with_offset(middle[1], 0.5, width_expr),
        ]
    elif abs(ux) < 1e-12:
        lower_left = [
            _coordinate_with_offset(middle[0], -0.5, width_expr),
            _coordinate_with_offset(middle[1], -0.5, length_expr),
        ]
        upper_right = [
            _coordinate_with_offset(middle[0], 0.5, width_expr),
            _coordinate_with_offset(middle[1], 0.5, length_expr),
        ]
    else:
        return {}
    return {
        "representation_type": "lower_left_upper_right",
        "lower_left_point": lower_left,
        "upper_right_point": upper_right,
        "parameterized": any(
            isinstance(value, str)
            for value in lower_left + upper_right
        ),
    }


def _axis_aligned_tangent_rectangle_args(
    *,
    first: tuple[float, float],
    second: tuple[float, float],
    radius_expr: float | str,
) -> dict[str, Any]:
    if abs(first[1] - second[1]) < 1e-12:
        left, right = sorted((first, second), key=lambda center: center[0])
        lower_left = [
            _coordinate_with_offset(left[0], 0.0, radius_expr),
            _coordinate_with_offset(left[1], -1.0, radius_expr),
        ]
        upper_right = [
            _coordinate_with_offset(right[0], 0.0, radius_expr),
            _coordinate_with_offset(right[1], 1.0, radius_expr),
        ]
        engineering_start = [
            _coordinate_with_offset(left[0], 0.0, radius_expr),
            _coordinate_with_offset(left[1], 1.0, radius_expr),
        ]
        engineering_end = [
            _coordinate_with_offset(right[0], 0.0, radius_expr),
            _coordinate_with_offset(right[1], -1.0, radius_expr),
        ]
        orientation = "horizontal"
    elif abs(first[0] - second[0]) < 1e-12:
        bottom, top = sorted((first, second), key=lambda center: center[1])
        lower_left = [
            _coordinate_with_offset(bottom[0], -1.0, radius_expr),
            _coordinate_with_offset(bottom[1], 0.0, radius_expr),
        ]
        upper_right = [
            _coordinate_with_offset(top[0], 1.0, radius_expr),
            _coordinate_with_offset(top[1], 0.0, radius_expr),
        ]
        engineering_start = [
            _coordinate_with_offset(bottom[0], -1.0, radius_expr),
            _coordinate_with_offset(bottom[1], 0.0, radius_expr),
        ]
        engineering_end = [
            _coordinate_with_offset(top[0], 1.0, radius_expr),
            _coordinate_with_offset(top[1], 0.0, radius_expr),
        ]
        orientation = "vertical"
    else:
        return {}
    return {
        "representation_type": "lower_left_upper_right",
        "lower_left_point": lower_left,
        "upper_right_point": upper_right,
        "engineering_start_point": engineering_start,
        "engineering_end_point": engineering_end,
        "orientation": orientation,
        "parameterized": any(
            isinstance(value, str)
            for value in lower_left + upper_right
        ),
    }


def _rectangle_representation_type(value: str) -> str:
    normalized = str(value).strip()
    if normalized in {"lower_left_upper_right", "center_width_height"}:
        return normalized
    key = normalized.replace("_", "").casefold()
    if key == "lowerleftupperright":
        return "lower_left_upper_right"
    if key == "centerwidthheight":
        return "center_width_height"
    return normalized


def _bridge_polygon_points(
    *,
    middle: tuple[float, float],
    ux: float,
    uy: float,
    px: float,
    py: float,
    half_length: float,
    half_width: float,
) -> list[tuple[float, float]]:
    centerline = [
        (
            middle[0] - ux * half_length,
            middle[1] - uy * half_length,
        ),
        (
            middle[0] + ux * half_length,
            middle[1] + uy * half_length,
        ),
        (
            middle[0] + ux * half_length,
            middle[1] + uy * half_length,
        ),
        (
            middle[0] - ux * half_length,
            middle[1] - uy * half_length,
        ),
    ]
    signs = [-1.0, -1.0, 1.0, 1.0]
    return [
        (
            x + sign * px * half_width,
            y + sign * py * half_width,
        )
        for (x, y), sign in zip(centerline, signs, strict=True)
    ]


def _coordinate_with_offset(
    base_m: float,
    coeff: float,
    size_expr: float | str,
) -> str:
    if isinstance(size_expr, str):
        return _parametric_coordinate(base_m, coeff, size_expr)
    return f"{(base_m + coeff * float(size_expr)) * 1000.0}mm"


def _parametric_coordinate(base_m: float, coeff: float, variable: str) -> str:
    base = f"{base_m * 1000.0}mm"
    if abs(coeff) < 1e-15:
        return base
    sign = "+" if coeff >= 0 else "-"
    return f"{base}{sign}{abs(coeff)}*{variable}"


def _optional_parameter_name(action: dict[str, Any], *keys: str) -> str:
    value = _first_present(action, *keys)
    if value is None:
        return ""
    name = str(value).strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"invalid parameter name: {name}")
    return name


def _optional_action_dimension(
    action: dict[str, Any],
    key: str,
) -> float | None:
    value = _first_present(action, key)
    if value is None:
        return None
    return _dimension_to_meters(value)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _primitive_summary(primitive: Any) -> dict[str, Any]:
    return {
        "id": _primitive_id(primitive),
        "layer": _primitive_layer(primitive),
        "net": _primitive_net(primitive),
    }


def _primitive_id(primitive: Any) -> Any:
    for attr in ("id", "primitive_id", "uid"):
        value = getattr(primitive, attr, None)
        if value is not None:
            return value
    return None


def _primitive_layer(primitive: Any) -> str:
    for attr in ("layer_name", "layer"):
        value = getattr(primitive, attr, None)
        if value is not None:
            return str(value)
    return ""


def _primitive_net(primitive: Any) -> str:
    for attr in ("net_name", "net"):
        value = getattr(primitive, attr, None)
        if value is not None:
            return str(value)
    return ""


def _primitive_is_void(primitive: Any) -> bool:
    return bool(getattr(primitive, "is_void", False))


def _primitive_is_path_like(primitive: Any) -> bool:
    for attr in (
        "primitive_type",
        "type",
        "object_type",
        "edb_object_type",
        "primitive_object_type",
    ):
        value = getattr(primitive, attr, None)
        if value is None:
            continue
        normalized = str(value).strip().casefold()
        if normalized == "path" or normalized.rsplit(".", 1)[-1] == "path":
            return True
    return False


def _required_string(action: dict[str, Any], key: str) -> str:
    value = action.get(key)
    if value is None and isinstance(action.get("target"), dict):
        value = action["target"].get(key)
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _layers(action: dict[str, Any]) -> list[str]:
    value = action.get("layers")
    if value is None and isinstance(action.get("target"), dict):
        value = action["target"].get("layers") or action["target"].get("layer")
    if isinstance(value, str):
        layers = [value]
    else:
        layers = list(value or [])
    result = [str(layer).strip() for layer in layers if str(layer).strip()]
    if not result:
        raise ValueError("layers are required")
    return result


def _padstack_definition(edb: Any, padstack_name: str) -> Any:
    definitions = getattr(getattr(edb, "padstacks", None), "definitions", {})
    if padstack_name not in definitions:
        raise ValueError(f"padstack definition not found: {padstack_name}")
    return definitions[padstack_name]


def _resolve_layer(collection: dict[str, Any], requested: str) -> str:
    if requested in collection:
        return requested
    normalized = _normalize_layer_name(requested)
    matches = [
        layer
        for layer in collection
        if _normalize_layer_name(str(layer)) == normalized
    ]
    if len(matches) == 1:
        return str(matches[0])
    if not matches:
        raise ValueError(f"layer not found in padstack definition: {requested}")
    raise ValueError(f"ambiguous layer name: {requested}")


def _normalize_layer_name(value: str) -> str:
    value = value.strip().upper()
    match = re.fullmatch(r"L0*([0-9]+)(.*)", value)
    if match:
        return f"L{int(match.group(1))}{match.group(2)}"
    return value


def _validate_antipad_layer(layer: str, action: dict[str, Any]) -> None:
    return


def _pad_snapshot(pad: Any) -> dict[str, Any]:
    shape = str(getattr(pad, "shape", "") or "")
    parameters = dict(getattr(pad, "parameters", {}) or {})
    diameter = _diameter_m(parameters)
    return {
        "shape": shape,
        "parameters": {str(k): str(v) for k, v in parameters.items()},
        "diameter_m": diameter,
    }


def _diameter_m(parameters: dict[str, Any]) -> float | None:
    for key, value in parameters.items():
        if str(key).casefold() == "diameter":
            return _dimension_to_meters(value)
    return None


def _target_diameter_m(
    before: dict[str, Any],
    action: dict[str, Any],
) -> float:
    target_spec = _first_present(
        action,
        "target_diameter",
        "diameter",
        "new_diameter",
    )
    if target_spec is not None:
        return _dimension_to_meters(target_spec)
    delta_spec = action.get("delta")
    if delta_spec is None:
        raise ValueError(
            "target_diameter or delta is required for model edit action"
        )
    current = before.get("diameter_m")
    if current is None:
        raise ValueError(
            "delta cannot be applied when the current pad has no diameter"
        )
    return float(current) + _dimension_to_meters(delta_spec)


def _target_diameter_edb_string(
    action: dict[str, Any],
    target_m: float,
) -> str:
    target_spec = _first_present(
        action,
        "target_diameter",
        "diameter",
        "new_diameter",
    )
    if target_spec is not None:
        return _dimension_to_edb_string(target_spec)
    return f"{target_m}m"


def _first_present(action: dict[str, Any], *keys: str) -> Any | None:
    for key in keys:
        if key in action:
            return action[key]
    params = action.get("parameters")
    if isinstance(params, dict):
        for key in keys:
            if key in params:
                return params[key]
    target = action.get("target")
    if isinstance(target, dict):
        for key in keys:
            if key in target:
                return target[key]
    return None


def _validate_target(
    before: dict[str, Any],
    target_m: float,
    action: dict[str, Any],
) -> None:
    if target_m <= 0:
        raise ValueError("target diameter must be positive")
    current = before.get("diameter_m")
    if current is not None and target_m <= float(current):
        raise ValueError("target diameter must enlarge the current diameter")
    limits = action.get("constraints") or action.get("limits") or {}
    minimum = _optional_dimension(limits, "min_diameter")
    maximum = _optional_dimension(limits, "max_diameter")
    max_delta = _optional_dimension(limits, "max_delta")
    if minimum is not None and target_m < minimum:
        raise ValueError("target diameter is below min_diameter")
    if maximum is not None and target_m > maximum:
        raise ValueError("target diameter is above max_diameter")
    if max_delta is not None and current is not None:
        if abs(target_m - float(current)) > max_delta:
            raise ValueError("target diameter delta exceeds max_delta")


def _optional_dimension(
    payload: dict[str, Any],
    key: str,
) -> float | None:
    if key not in payload:
        return None
    return _dimension_to_meters(payload[key])


def _set_circle_diameter(pad: Any, diameter: str) -> None:
    try:
        pad.shape = "circle"
    except Exception:
        pad.shape = "Circle"
    pad.parameters = {"Diameter": diameter}


def _dimension_to_meters(value: Any) -> float:
    if isinstance(value, dict):
        magnitude = float(value["value"])
        unit = str(value.get("unit") or "m").casefold()
        return magnitude * _unit_factor(unit)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    match = re.fullmatch(
        r"([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*([A-Za-z]*)",
        text,
    )
    if not match:
        raise ValueError(f"invalid dimension: {value}")
    unit = match.group(2) or "m"
    return float(match.group(1)) * _unit_factor(unit.casefold())


def _dimension_to_edb_string(value: Any) -> str:
    if isinstance(value, dict):
        unit = str(value.get("unit") or "m")
        return f"{value['value']}{unit}"
    if isinstance(value, (int, float)):
        return f"{value}m"
    return str(value)


def _unit_factor(unit: str) -> float:
    factors = {
        "m": 1.0,
        "meter": 1.0,
        "meters": 1.0,
        "mm": 1e-3,
        "mil": 25.4e-6,
        "mils": 25.4e-6,
        "um": 1e-6,
        "µm": 1e-6,
        "nm": 1e-9,
    }
    try:
        return factors[unit]
    except KeyError as exc:
        raise ValueError(f"unsupported dimension unit: {unit}") from exc


def _sidecar_edb(project_path: Path) -> Path:
    return project_path.with_suffix(".aedb")


def _grpc_mode(value: str) -> bool | None:
    normalized = value.strip().casefold()
    if normalized in {"", "auto"}:
        return None
    if normalized in {"grpc", "true", "1", "yes", "on"}:
        return True
    if normalized in {"legacy", "dotnet", "false", "0", "no", "off"}:
        return False
    raise ValueError(f"unsupported edb_backend: {value}")


def _close_edb(edb: Any) -> None:
    close = getattr(edb, "close", None)
    if callable(close):
        close()


def _project_bundle_digest(project_path: Path, edb_path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(str(project_path.name).encode("utf-8"))
    digest.update(b"\0")
    digest.update(_sha256_file(project_path).encode("ascii"))
    digest.update(b"\0")
    edb_digest, edb_size, edb_count = _hash_directory(edb_path)
    digest.update(str(edb_path.name).encode("utf-8"))
    digest.update(b"\0")
    digest.update(edb_digest.encode("ascii"))
    digest.update(b"\0")
    digest.update(str(edb_size).encode("ascii"))
    digest.update(b"\0")
    digest.update(str(edb_count).encode("ascii"))
    return digest.hexdigest()


def _expected_persisted_variables(changes: list[dict[str, Any]]) -> set[str]:
    variables: set[str] = set()
    for change in changes:
        _collect_persisted_variables(change, variables)
    return variables


def _collect_persisted_variables(value: Any, variables: set[str]) -> None:
    if isinstance(value, dict):
        name = str(value.get("name") or "").strip()
        scope = str(value.get("scope") or "").strip().casefold()
        if name and scope in {"design", "project"}:
            if scope == "project" and not name.startswith("$"):
                name = f"${name}"
            variables.add(name)
        for item in value.values():
            _collect_persisted_variables(item, variables)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _collect_persisted_variables(item, variables)


def _edb_variable_exists(edb: Any, name: str) -> bool:
    exists = getattr(edb, "variable_exists", None)
    if callable(exists) and _variable_exists_result(exists(name)):
        return True
    design_variables = getattr(edb, "design_variables", None)
    if isinstance(design_variables, dict) and name in design_variables:
        return True
    project_variables = getattr(edb, "project_variables", None)
    if isinstance(project_variables, dict) and name in project_variables:
        return True
    if name.startswith("$"):
        bare_name = name[1:]
        if callable(exists) and _variable_exists_result(exists(bare_name)):
            return True
        return isinstance(project_variables, dict) and bare_name in project_variables
    return False


def _artifact_record(path: Path) -> dict[str, Any]:
    if path.is_file():
        return {
            "path": str(path),
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    if path.is_dir():
        digest, size, count = _hash_directory(path)
        return {
            "path": str(path),
            "sha256": digest,
            "size_bytes": size,
            "file_count": count,
        }
    raise FileNotFoundError(f"artifact path not found: {path}")


def _hash_directory(path: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    total_size = 0
    file_count = 0
    for file_path in sorted(p for p in path.rglob("*") if p.is_file()):
        relative = file_path.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        data = file_path.read_bytes()
        digest.update(data)
        total_size += len(data)
        file_count += 1
    return digest.hexdigest(), total_size, file_count


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
