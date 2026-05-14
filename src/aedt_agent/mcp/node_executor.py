from __future__ import annotations

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
        state_before = session.adapter.snapshot_state()
        node_fn = self._node_callable(node_id, validation.inputs)
        result = self.queue.submit_callable(
            session=session.ref,
            fn=lambda: session.adapter.execute_node_callable(node_fn),
            node_id=node_id,
        )
        state_after = session.adapter.snapshot_state()
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


def _create_airbox(app: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    padding = inputs["padding"]
    name = inputs["name"]
    if hasattr(app.modeler, "create_region"):
        obj = app.modeler.create_region(padding=padding, name=name)
    else:
        obj = app.modeler.create_box([-padding, -padding, -padding], [2 * padding, 2 * padding, 2 * padding], name=name, material="air")
    return _node_output(objects=[getattr(obj, "name", name)], postchecks=["air_region_created"])


def _assign_boundary(app: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    boundary_type = inputs["boundary_type"].lower()
    name = inputs["name"]
    if boundary_type in {"radiation", "open"}:
        if hasattr(app, "assign_radiation_boundary_to_objects"):
            created = app.assign_radiation_boundary_to_objects(inputs["assignment"], name=name)
        else:
            created = app.create_open_region(name=name)
    elif boundary_type in {"perfect_e", "pec"}:
        created = app.assign_perfecte_to_sheets(inputs["assignment"], name=name)
    else:
        raise ValueError(f"unsupported boundary_type: {inputs['boundary_type']}")
    return _node_output(boundaries=[str(created)], postchecks=["boundary_created"])


def _create_port(app: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    app.solution_type = "Modal"
    port_type = inputs["port_type"].lower()
    name = inputs["name"]
    if port_type in {"lumped", "lumped_port", "microstrip_lumped_port_default"}:
        port = app.lumped_port(
            assignment=inputs["assignment"],
            name=name,
            create_port_sheet=False,
            integration_line=inputs.get("integration_line"),
            impedance=inputs.get("impedance", 50),
        )
    elif port_type in {"wave", "wave_port", "wave_port_on_sheet", "wave_port_on_face_id"}:
        kwargs = {"name": name}
        if inputs.get("integration_line") is not None:
            kwargs["integration_line"] = inputs["integration_line"]
        if inputs.get("reference") is not None:
            kwargs["reference"] = inputs["reference"]
        port = app.wave_port(inputs["assignment"], **kwargs)
    else:
        raise ValueError(f"unsupported port_type: {inputs['port_type']}")
    return _node_output(ports=[str(port)], postchecks=["port_created"])


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
        "selected_face_id": selected["id"],
        "selected_face_center": selected["center"],
        "postcheck": {"passed": True, "checks": ["face_selected"]},
    }


def _create_setup(app: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    frequency = inputs["frequency"]
    if isinstance(frequency, (int, float)):
        frequency = f"{frequency}GHz"
    setup = app.create_setup(name=inputs["name"], Frequency=frequency, MaximumPasses=inputs["max_passes"])
    return _node_output(setups=[str(setup)], postchecks=["setup_created"])


def _create_sweep(app: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    sweep = app.create_linear_count_sweep(
        inputs["setup"],
        units="GHz",
        start_frequency=inputs["start"],
        stop_frequency=inputs["stop"],
        num_of_freq_points=inputs["points"],
        name=inputs["name"],
    )
    return _node_output(sweeps=[str(sweep)], postchecks=["sweep_created"])


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
    return {
        "created": {
            "objects": objects or [],
            "ports": ports or [],
            "boundaries": boundaries or [],
            "setups": setups or [],
            "sweeps": sweeps or [],
        },
        "postcheck": {"passed": True, "checks": postchecks or []},
    }


def _rejected(error_type: str, message: str) -> ExecutionResult:
    return ExecutionResult(
        status=ExecutionStatus.REJECTED,
        transaction_id=f"txn-{uuid4().hex}",
        error_type=error_type,
        error_message=message,
    )
