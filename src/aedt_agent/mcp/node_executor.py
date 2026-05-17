from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from aedt_agent.mcp.audit_log import AuditLogger
from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.node_schemas import validate_node_inputs
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.mcp.types import ExecutionResult, ExecutionStatus
from aedt_agent.nodes.registry import NodeRegistry


class NodeExecutor:
    def __init__(
        self,
        registry: NodeRegistry,
        session_manager: SessionManager,
        queue: ExecutionQueue,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.registry = registry
        self.session_manager = session_manager
        self.queue = queue
        self.audit_logger = audit_logger

    def execute_node(self, session_id: str, node_id: str, inputs: dict[str, Any]) -> ExecutionResult:
        if node_id not in self.registry.nodes:
            return _rejected("UnknownNode", f"Unknown node_id: {node_id}")
        validation = validate_node_inputs(node_id, inputs)
        if not validation.passed:
            return _rejected("schema_error", "; ".join(validation.errors))

        session = self.session_manager.get_session(session_id)
        state_before = _safe_snapshot(session.adapter)
        node_fn = self._node_callable(node_id, validation.inputs)
        result = self.queue.submit_callable(
            session=session.ref,
            fn=lambda: session.adapter.execute_node_callable(node_fn),
            node_id=node_id,
        )
        state_after = _safe_snapshot(session.adapter)
        if self.audit_logger is not None:
            self.audit_logger.record(
                event_type="execute_node",
                session_id=session_id,
                node_id=node_id,
                inputs=validation.inputs,
                result=result,
                state_before=state_before,
                state_after=state_after,
            )
        return result

    def _node_callable(self, node_id: str, inputs: dict[str, Any]) -> Callable[[Any], dict[str, Any]]:
        if node_id == "create_substrate":
            return lambda app: _create_substrate(app, inputs)
        if node_id == "create_conductor_or_geometry_group":
            return lambda app: _create_geometry_group(app, inputs)
        if node_id == "create_airbox":
            return lambda app: _create_airbox(app, inputs)
        if node_id == "assign_boundary":
            return lambda app: _assign_boundary(app, inputs)
        if node_id == "create_port":
            return lambda app: _create_port(app, inputs)
        if node_id == "select_face":
            return lambda app: _select_face(app, inputs)
        if node_id == "create_setup":
            return lambda app: _create_setup(app, inputs)
        if node_id == "create_sweep_or_export":
            return lambda app: _create_sweep(app, inputs)
        raise KeyError(node_id)


def create_node_executor(
    node_catalog_dir: Path,
    session_manager: SessionManager,
    audit_path: Path | None = None,
    timeout_seconds: float = 120.0,
) -> NodeExecutor:
    return NodeExecutor(
        registry=NodeRegistry.from_directory(node_catalog_dir),
        session_manager=session_manager,
        queue=ExecutionQueue(timeout_seconds=timeout_seconds),
        audit_logger=AuditLogger(audit_path) if audit_path else None,
    )


def _create_substrate(app: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    obj = app.modeler.create_box(inputs["origin"], inputs["size"], name=inputs["name"], material=inputs["material"])
    _assign_material_if_available(app, obj.name, inputs["material"])
    return _node_output(objects=[obj.name], postchecks=["object_exists", "material_matches"])


def _create_geometry_group(app: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    created = []
    for index, item in enumerate(inputs["geometry"]):
        if not isinstance(item, dict):
            raise TypeError("geometry entries must be mappings")
        item = _normalize_geometry_item(item)
        kind = item.get("kind", "box")
        name = str(item.get("name", f"Geometry{index + 1}"))
        material = item.get("material", "copper")
        if kind == "rectangle":
            obj = app.modeler.create_rectangle(
                item.get("orientation", "XY"),
                item["origin"],
                item["size"],
                name=name,
                material=material,
            )
        elif kind == "cylinder":
            obj = app.modeler.create_box(
                item["origin"],
                _cylinder_box_size(item),
                name=name,
                material=material,
            )
        else:
            obj = app.modeler.create_box(item["origin"], item["size"], name=name, material=material)
        created.append(obj.name)
        _assign_material_if_available(app, obj.name, material)
    return _node_output(objects=created, postchecks=["objects_exist"])


def _normalize_geometry_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    if "kind" not in normalized and "type" in normalized:
        normalized["kind"] = normalized["type"]
    if "origin" not in normalized and "position" in normalized:
        normalized["origin"] = normalized["position"]
    if "size" not in normalized and "sizes" in normalized:
        normalized["size"] = normalized["sizes"]
    if "size" not in normalized and "dimensions" in normalized:
        normalized["size"] = normalized["dimensions"]
    if "material" not in normalized and "matname" in normalized:
        normalized["material"] = normalized["matname"]
    return normalized


def _cylinder_box_size(item: dict[str, Any]) -> list[Any]:
    radius = item.get("radius", 0.5)
    height = item.get("height", item.get("size", [1, 1, 1])[-1] if isinstance(item.get("size"), list) else 1)
    axis = str(item.get("axis", "z")).lower()
    diameter = 2 * radius if isinstance(radius, (int, float)) else radius
    if axis == "x":
        return [height, diameter, diameter]
    if axis == "y":
        return [diameter, height, diameter]
    return [diameter, diameter, height]


def _create_airbox(app: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    padding = _normalize_padding(inputs["padding"])
    name = inputs["name"]
    if hasattr(app.modeler, "create_region"):
        obj = app.modeler.create_region(padding=padding, name=name)
    else:
        obj = app.modeler.create_box([-padding, -padding, -padding], [2 * padding, 2 * padding, 2 * padding], name=name, material="air")
    return _node_output(objects=[getattr(obj, "name", name)], postchecks=["air_region_created"])


def _assign_boundary(app: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    boundary_type = inputs["boundary_type"].lower()
    name = inputs["name"]
    assignment = _normalize_assignment(inputs["assignment"], prefer_list=True)
    if boundary_type in {"radiation", "open"}:
        if hasattr(app, "assign_radiation_boundary_to_objects"):
            created = app.assign_radiation_boundary_to_objects(assignment, name=name)
        else:
            created = app.create_open_region(name=name)
    elif boundary_type in {"perfect_e", "pec"}:
        created = app.assign_perfecte_to_sheets(assignment, name=name)
    else:
        raise ValueError(f"unsupported boundary_type: {inputs['boundary_type']}")
    return _node_output(boundaries=[str(created)], postchecks=["boundary_created"])


def _create_port(app: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    app.solution_type = "Modal"
    port_type = inputs["port_type"].lower()
    name = inputs["name"]
    assignment = _normalize_assignment(inputs["assignment"])
    if port_type in {"lumped", "lumped_port", "microstrip_lumped_port_default"}:
        if isinstance(assignment, int):
            assignment = _object_name_for_face_id(app, assignment) or assignment
        port = app.lumped_port(
            assignment=assignment,
            name=name,
            create_port_sheet=False,
            integration_line=_normalize_integration_line(inputs.get("integration_line", 0)),
            impedance=inputs.get("impedance", 50),
        )
    elif port_type in {"wave", "wave_port", "wave_port_on_sheet", "wave_port_on_face_id"}:
        kwargs = {"name": name}
        if inputs.get("integration_line") is not None:
            kwargs["integration_line"] = _normalize_integration_line(inputs["integration_line"])
        if inputs.get("reference") is not None:
            kwargs["reference"] = _normalize_assignment(inputs["reference"])
        port = app.wave_port(assignment, **kwargs)
    else:
        raise ValueError(f"unsupported port_type: {inputs['port_type']}")
    return _node_output(ports=[str(port)], postchecks=["port_created"])


def _normalize_padding(value: Any) -> int | float:
    if isinstance(value, list):
        numeric = [item for item in value if isinstance(item, (int, float))]
        if not numeric:
            raise TypeError("padding list must contain numeric values")
        return max(numeric)
    return value


def _normalize_assignment(value: Any, prefer_list: bool = False) -> Any:
    if not isinstance(value, dict):
        return value
    if "output" in value:
        return _normalize_assignment(value["output"], prefer_list=prefer_list)
    if value.get("selected_face_id") is not None:
        return value["selected_face_id"]
    if value.get("object_name") is not None:
        return value["object_name"]
    created = value.get("created")
    if isinstance(created, dict):
        for key in ("objects", "ports", "boundaries", "setups", "sweeps"):
            names = created.get(key)
            if isinstance(names, list) and names:
                return names if prefer_list else names[0]
    raise ValueError("Could not normalize node output as assignment")


def _normalize_integration_line(value: Any) -> Any:
    if isinstance(value, dict) and "start" in value and "end" in value:
        return [value["start"], value["end"]]
    return value


def _select_face(app: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    object_name = inputs["object_name"]
    axis = inputs.get("axis", "x").lower()
    side = inputs.get("side", "max").lower()
    axis_index = {"x": 0, "y": 1, "z": 2}.get(axis, 0)
    faces = _get_object_faces(app, object_name)
    if not faces:
        raise ValueError(f"object has no faces: {object_name}")
    selected = max(faces, key=lambda face: face["center"][axis_index]) if side == "max" else min(faces, key=lambda face: face["center"][axis_index])
    return {
        "created": {"objects": [], "ports": [], "boundaries": [], "setups": [], "sweeps": []},
        "object_name": object_name,
        "selected_face_id": selected["id"],
        "selected_face_center": selected["center"],
        "postcheck": {"passed": True, "checks": ["face_selected"]},
    }


def _create_setup(app: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    frequency = inputs["frequency"]
    if isinstance(frequency, (int, float)):
        frequency = f"{frequency}GHz"
    setup = app.create_setup(name=inputs["name"], Frequency=frequency, MaximumPasses=inputs["max_passes"])
    return _node_output(setups=[_aedt_object_name(setup, inputs["name"])], postchecks=["setup_created"])


def _create_sweep(app: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    kwargs = {
        "start_frequency": _frequency_value(inputs["start"]),
        "stop_frequency": _frequency_value(inputs["stop"]),
        "num_of_freq_points": inputs["points"],
        "name": inputs["name"],
    }
    signature = inspect.signature(app.create_linear_count_sweep)
    unit_arg = "unit" if "unit" in signature.parameters else "units"
    kwargs[unit_arg] = _frequency_unit(inputs["start"], inputs["stop"])
    sweep = app.create_linear_count_sweep(inputs["setup"], **kwargs)
    return _node_output(sweeps=[_aedt_object_name(sweep, inputs["name"])], postchecks=["sweep_created"])


def _aedt_object_name(value: Any, fallback: str) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    if isinstance(value, str) and value:
        return value
    return fallback


def _get_object_faces(app: Any, object_name: str) -> list[dict[str, Any]]:
    faces: list[dict[str, Any]] = []
    try:
        obj = app.modeler[object_name]
        for face in getattr(obj, "faces", []):
            faces.append({"id": int(face.id), "center": list(face.center)})
    except Exception:
        try:
            face_ids = app.modeler.get_object_faces(object_name)
            for face_id in face_ids:
                faces.append({"id": int(face_id), "center": list(app.modeler.get_face_center(face_id))})
        except Exception:
            return []
    return faces


def _object_name_for_face_id(app: Any, face_id: int) -> str | None:
    for object_name in getattr(app.modeler, "object_names", []) or []:
        if any(face["id"] == face_id for face in _get_object_faces(app, str(object_name))):
            return str(object_name)
    objects = getattr(app, "objects", None)
    if isinstance(objects, dict):
        for object_name, obj in objects.items():
            for face in getattr(obj, "faces", []):
                if int(getattr(face, "id", -1)) == face_id:
                    return str(object_name)
    return None


def _frequency_value(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.strip().lower()
        for suffix in ("ghz", "mhz", "khz", "hz"):
            if lowered.endswith(suffix):
                number = value.strip()[: -len(suffix)]
                try:
                    return float(number)
                except ValueError:
                    return value
    return value


def _frequency_unit(*values: Any) -> str:
    units = {"ghz": "GHz", "mhz": "MHz", "khz": "KHz", "hz": "Hz"}
    for value in values:
        if isinstance(value, str):
            lowered = value.strip().lower()
            for suffix, unit in units.items():
                if lowered.endswith(suffix):
                    return unit
    return "GHz"


def _assign_material_if_available(app: Any, name: str, material: str) -> None:
    if hasattr(app, "assign_material"):
        app.assign_material(name, material)


def _node_output(
    objects: list[str] | None = None,
    ports: list[str] | None = None,
    boundaries: list[str] | None = None,
    setups: list[str] | None = None,
    sweeps: list[str] | None = None,
    postchecks: list[str] | None = None,
) -> dict[str, Any]:
    created = {
        "objects": objects or [],
        "ports": ports or [],
        "boundaries": boundaries or [],
        "setups": setups or [],
        "sweeps": sweeps or [],
    }
    output = {
        "created": {
            "objects": list(created["objects"]),
            "ports": list(created["ports"]),
            "boundaries": list(created["boundaries"]),
            "setups": list(created["setups"]),
            "sweeps": list(created["sweeps"]),
        },
        "postcheck": {"passed": True, "checks": postchecks or []},
    }
    if created["objects"]:
        output["object_name"] = created["objects"][0]
        output["object_names"] = list(created["objects"])
    if created["ports"]:
        output["port_name"] = created["ports"][0]
    if created["boundaries"]:
        output["boundary_name"] = created["boundaries"][0]
    if created["setups"]:
        output["setup_name"] = created["setups"][0]
    if created["sweeps"]:
        output["sweep_name"] = created["sweeps"][0]
    return output


def _safe_snapshot(adapter: Any) -> dict[str, Any]:
    try:
        return adapter.snapshot_state()
    except Exception as exc:
        return {"snapshot_error": f"{type(exc).__name__}: {exc}"}


def _rejected(error_type: str, message: str) -> ExecutionResult:
    return ExecutionResult(
        status=ExecutionStatus.REJECTED,
        transaction_id=f"txn-{uuid4().hex}",
        error_type=error_type,
        error_message=message,
    )
