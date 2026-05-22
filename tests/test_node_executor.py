import json
from pathlib import Path

from aedt_agent.mcp.audit_log import AuditLogger
from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.fake_aedt import FakeAedtAdapter
from aedt_agent.mcp.node_executor import NodeExecutor, _create_sweep
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.mcp.types import ExecutionStatus
from aedt_agent.nodes.registry import NodeRegistry


def _executor(tmp_path):
    manager = SessionManager(lambda project_id, design_id: FakeAedtAdapter(project_id, design_id))
    executor = NodeExecutor(
        registry=NodeRegistry.from_directory(Path("nodes/catalog")),
        session_manager=manager,
        queue=ExecutionQueue(timeout_seconds=1),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
    )
    return manager, executor


def test_node_executor_runs_create_substrate_and_audits(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(
        session.ref.session_id,
        "create_substrate",
        {"origin": [0, 0, 0], "size": [20, 15, 0.8], "material": "FR4_epoxy", "name": "Substrate"},
    )

    state = manager.snapshot(session.ref.session_id)
    audit_event = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert result.status == ExecutionStatus.SUCCEEDED
    assert state["objects"]["Substrate"]["material"] == "FR4_epoxy"
    assert audit_event["node_id"] == "create_substrate"


def test_node_executor_rejects_unknown_node(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(session.ref.session_id, "not_a_node", {})

    assert result.status == ExecutionStatus.REJECTED
    assert result.error_type == "UnknownNode"


def test_node_executor_selects_face_from_object(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")
    executor.execute_node(
        session.ref.session_id,
        "create_substrate",
        {"origin": [0, 0, 0], "size": [1, 1, 1], "material": "FR4_epoxy"},
    )

    result = executor.execute_node(
        session.ref.session_id,
        "select_face",
        {"object_name": "Substrate", "axis": "x", "side": "max"},
    )

    assert result.status == ExecutionStatus.SUCCEEDED
    assert result.output["selected_face_id"] > 0


def test_node_executor_accepts_common_geometry_aliases(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(
        session.ref.session_id,
        "create_conductor_or_geometry_group",
        {
            "geometry": [
                {
                    "type": "box",
                    "position": [0, 0, 0],
                    "dimensions": [10, 10, 1],
                    "name": "metal",
                    "matname": "copper",
                }
            ]
        },
    )

    state = manager.snapshot(session.ref.session_id)
    assert result.status == ExecutionStatus.SUCCEEDED
    assert state["objects"]["metal"]["material"] == "copper"
    assert result.output["object_name"] == "metal"


def test_node_executor_accepts_cylinder_geometry(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(
        session.ref.session_id,
        "create_conductor_or_geometry_group",
        {
            "geometry": [
                {
                    "kind": "cylinder",
                    "origin": [0, 0, 0],
                    "radius": 0.5,
                    "height": 10,
                    "axis": "z",
                    "name": "probe",
                    "material": "copper",
                }
            ]
        },
    )

    state = manager.snapshot(session.ref.session_id)
    assert result.status == ExecutionStatus.SUCCEEDED
    assert state["objects"]["probe"]["type"] == "cylinder"
    assert state["objects"]["probe"]["sizes"] == [0.5, 10, "Z", 0]


def test_node_executor_accepts_airbox_padding_list_and_output_assignment(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    airbox = executor.execute_node(
        session.ref.session_id,
        "create_airbox",
        {"padding": [5, 5, 5], "name": "AirBox"},
    )
    boundary = executor.execute_node(
        session.ref.session_id,
        "assign_boundary",
        {"boundary_type": "Radiation", "assignment": airbox.output, "name": "Radiation"},
    )

    state = manager.snapshot(session.ref.session_id)
    assert airbox.status == ExecutionStatus.SUCCEEDED
    assert boundary.status == ExecutionStatus.SUCCEEDED
    assert "Radiation" in state["boundaries"]


def test_node_executor_creates_airbox_with_absolute_padding_around_existing_geometry(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")
    executor.execute_node(
        session.ref.session_id,
        "create_conductor_or_geometry_group",
        {"geometry": [{"kind": "box", "origin": [10, 0, -1], "size": [2, 4, 6], "name": "Metal"}]},
    )

    result = executor.execute_node(session.ref.session_id, "create_airbox", {"padding": 5, "name": "AirBox"})

    state = manager.snapshot(session.ref.session_id)
    assert result.status == ExecutionStatus.SUCCEEDED
    assert state["objects"]["AirBox"]["origin"] == [5, -5, -6]
    assert state["objects"]["AirBox"]["sizes"] == [12, 14, 16]
    assert state["objects"]["AirBox"]["material"] == "air"


def test_node_executor_accepts_created_object_reference_for_port(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")
    geometry = executor.execute_node(
        session.ref.session_id,
        "create_conductor_or_geometry_group",
        {"geometry": [{"kind": "box", "origin": [0, 0, 0], "size": [1, 1, 1], "name": "metal"}]},
    )

    result = executor.execute_node(
        session.ref.session_id,
        "create_port",
        {"port_type": "wave", "assignment": geometry.output, "reference": geometry.output},
    )

    assert result.status == ExecutionStatus.SUCCEEDED


def test_node_executor_accepts_wrapped_output_reference_for_boundary(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")
    airbox = executor.execute_node(session.ref.session_id, "create_airbox", {"padding": 5, "name": "AirBox"})

    result = executor.execute_node(
        session.ref.session_id,
        "assign_boundary",
        {"boundary_type": "Radiation", "assignment": {"output": airbox.output}},
    )

    assert result.status == ExecutionStatus.SUCCEEDED


def test_node_executor_accepts_integration_line_start_end_mapping(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")
    geometry = executor.execute_node(
        session.ref.session_id,
        "create_conductor_or_geometry_group",
        {"geometry": [{"kind": "box", "origin": [0, 0, 0], "size": [1, 1, 1], "name": "metal"}]},
    )

    result = executor.execute_node(
        session.ref.session_id,
        "create_port",
        {
            "port_type": "wave",
            "assignment": geometry.output,
            "integration_line": {"start": [0, 0, 0], "end": [0, 1, 0]},
        },
    )

    assert result.status == ExecutionStatus.SUCCEEDED


def test_node_executor_rejects_bad_schema(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(session.ref.session_id, "create_setup", {})

    assert result.status == ExecutionStatus.REJECTED
    assert result.error_type == "schema_error"


def test_node_executor_creates_lumped_port_with_modal_solution(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(
        session.ref.session_id,
        "create_port",
        {"port_type": "lumped", "assignment": "PortSheet", "name": "P1", "integration_line": [[0, 0, 0], [0, 1, 0]]},
    )

    state = manager.snapshot(session.ref.session_id)
    assert result.status == ExecutionStatus.SUCCEEDED
    assert state["ports"]["P1"]["type"] == "lumped"


def test_node_executor_solves_and_creates_sparameter_report(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")
    executor.execute_node(session.ref.session_id, "create_setup", {"frequency": "2.4GHz", "name": "Setup1"})
    executor.execute_node(session.ref.session_id, "create_sweep_or_export", {"setup": "Setup1", "name": "Sweep1"})
    solve = executor.execute_node(session.ref.session_id, "solve_setup", {"setup": "Setup1"})
    report = executor.execute_node(
        session.ref.session_id,
        "create_sparameter_report",
        {
            "setup": "Setup1",
            "sweep": "Sweep1",
            "ports": ["P1", "P2"],
            "report_name": "Demo S-Parameters",
            "output_dir": str(tmp_path),
        },
    )

    state = manager.snapshot(session.ref.session_id)
    assert solve.status == ExecutionStatus.SUCCEEDED
    assert report.status == ExecutionStatus.SUCCEEDED
    assert state["reports"]["Demo S-Parameters"]["type"] == "sparameter"
    assert Path(report.output["touchstone_path"]).exists()


def test_node_executor_creates_cylinder_geometry_for_dipole_arm(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(
        session.ref.session_id,
        "create_conductor_or_geometry_group",
        {
            "geometry": [
                {
                    "kind": "cylinder",
                    "axis": "X",
                    "origin": [-30, 0, 0],
                    "radius": 0.5,
                    "height": 29.5,
                    "name": "DipoleArmLeft",
                    "material": "copper",
                }
            ]
        },
    )

    state = manager.snapshot(session.ref.session_id)
    assert result.status == ExecutionStatus.SUCCEEDED
    assert state["objects"]["DipoleArmLeft"]["type"] == "cylinder"
    assert state["objects"]["DipoleArmLeft"]["material"] == "copper"


def test_node_executor_creates_farfield_and_antenna_report(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")
    executor.execute_node(session.ref.session_id, "create_setup", {"frequency": "2.4GHz", "name": "Setup1"})
    executor.execute_node(session.ref.session_id, "create_sweep_or_export", {"setup": "Setup1", "name": "Sweep1"})
    farfield = executor.execute_node(session.ref.session_id, "create_farfield_setup", {"name": "InfiniteSphere1"})
    executor.execute_node(session.ref.session_id, "solve_setup", {"setup": "Setup1"})
    report = executor.execute_node(
        session.ref.session_id,
        "create_antenna_report",
        {
            "setup": "Setup1",
            "sweep": "Sweep1",
            "farfield": farfield.output["farfield_name"],
            "report_name": "Dipole Gain Pattern",
            "output_dir": str(tmp_path),
            "export_report": True,
        },
    )

    state = manager.snapshot(session.ref.session_id)
    assert farfield.status == ExecutionStatus.SUCCEEDED
    assert report.status == ExecutionStatus.SUCCEEDED
    assert state["farfields"]["InfiniteSphere1"]["definition"] == "Theta-Phi"
    assert state["reports"]["Dipole Gain Pattern"]["type"] == "antenna"
    assert Path(report.output["report_path"]).exists()


def test_node_executor_does_not_export_antenna_report_by_default(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")
    executor.execute_node(session.ref.session_id, "create_setup", {"frequency": "2.4GHz", "name": "Setup1"})
    executor.execute_node(session.ref.session_id, "create_sweep_or_export", {"setup": "Setup1", "name": "Sweep1"})
    farfield = executor.execute_node(session.ref.session_id, "create_farfield_setup", {"name": "InfiniteSphere1"})
    executor.execute_node(session.ref.session_id, "solve_setup", {"setup": "Setup1"})

    report = executor.execute_node(
        session.ref.session_id,
        "create_antenna_report",
        {
            "setup": "Setup1",
            "sweep": "Sweep1",
            "farfield": farfield.output["farfield_name"],
            "report_name": "Dipole Gain Pattern",
            "output_dir": str(tmp_path),
        },
    )

    assert report.status == ExecutionStatus.SUCCEEDED
    assert "report_path" not in report.output


def test_node_executor_remaps_lumped_port_face_id_to_object_name(tmp_path):
    manager, executor = _executor(tmp_path)
    session = manager.create_session("p1", "d1")
    geometry = executor.execute_node(
        session.ref.session_id,
        "create_conductor_or_geometry_group",
        {"geometry": [{"kind": "rectangle", "origin": [0, 0, 0], "size": [1, 1], "name": "port_sheet"}]},
    )
    face = executor.execute_node(
        session.ref.session_id,
        "select_face",
        {"object_name": geometry.output["object_name"], "axis": "z", "side": "max"},
    )

    result = executor.execute_node(
        session.ref.session_id,
        "create_port",
        {"port_type": "lumped", "assignment": face.output["selected_face_id"], "name": "P1"},
    )

    state = manager.snapshot(session.ref.session_id)
    assert result.status == ExecutionStatus.SUCCEEDED
    assert state["ports"]["P1"]["assignment"] == "port_sheet"


def test_create_sweep_uses_unit_signature_when_available():
    class UnitSweepApp:
        def create_linear_count_sweep(
            self,
            setup,
            unit,
            start_frequency,
            stop_frequency,
            num_of_freq_points=None,
            name=None,
            sweep_type="Discrete",
        ):
            self.call = {
                "setup": setup,
                "unit": unit,
                "start_frequency": start_frequency,
                "stop_frequency": stop_frequency,
                "num_of_freq_points": num_of_freq_points,
                "name": name,
                "sweep_type": sweep_type,
            }
            return name

    app = UnitSweepApp()

    result = _create_sweep(
        app,
        {"setup": "Setup1", "start": "1GHz", "stop": "5GHz", "points": 101, "name": "Sweep1", "type": "Interpolating"},
    )

    assert result["sweep_name"] == "Sweep1"
    assert app.call["unit"] == "GHz"
    assert app.call["start_frequency"] == 1.0
    assert app.call["stop_frequency"] == 5.0
    assert app.call["sweep_type"] == "Interpolating"


def test_node_executor_records_failure_when_snapshot_after_fails(tmp_path):
    class SnapshotAfterFailureAdapter(FakeAedtAdapter):
        def __init__(self, project_id, design_id):
            super().__init__(project_id, design_id)
            self.snapshots = 0

        def snapshot_state(self):
            self.snapshots += 1
            if self.snapshots > 1:
                raise RuntimeError("snapshot failed")
            return super().snapshot_state()

        def execute_node_callable(self, fn):
            raise ValueError("node failed")

    manager = SessionManager(lambda project_id, design_id: SnapshotAfterFailureAdapter(project_id, design_id))
    executor = NodeExecutor(
        registry=NodeRegistry.from_directory(Path("nodes/catalog")),
        session_manager=manager,
        queue=ExecutionQueue(timeout_seconds=1),
        audit_logger=AuditLogger(tmp_path / "audit.jsonl"),
    )
    session = manager.create_session("p1", "d1")

    result = executor.execute_node(
        session.ref.session_id,
        "create_setup",
        {"frequency": "1GHz"},
    )

    assert result.status == ExecutionStatus.FAILED
    audit_event = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert "snapshot_error" in audit_event["state_after"]
