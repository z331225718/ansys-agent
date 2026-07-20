from __future__ import annotations

import os
from pathlib import Path
import socket

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LIVE_AEDT") != "1",
    reason="real live AEDT acceptance is opt-in",
)


def test_real_live_aedt_hfss_and_layout_chain(tmp_path):
    from ansys.aedt.core.desktop import launch_aedt

    from aedt_agent.capability_learning import CapabilityTraceStore
    from aedt_agent.knowledge.api_memory import AnsysApiMemory
    from aedt_agent.live.approval import HmacApprovalAuthority
    from aedt_agent.live.manager import LiveAedtSessionManager

    memory = AnsysApiMemory()
    width_symbol = memory.search("Line3dLayout width", package="pyaedt", limit=1)["results"][0][
        "qualified_name"
    ]
    width_evidence = memory.inspect(width_symbol, package="pyaedt")["operation_evidence"]

    executable = Path(os.environ["ANSYSEM_ROOT261"]) / "ansysedt.exe"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        requested_port = probe.getsockname()[1]
    launched, port = launch_aedt(
        executable,
        non_graphical=True,
        port=requested_port,
        student_version=False,
    )
    authority = HmacApprovalAuthority("real-live-acceptance-secret-32-bytes")
    manager = LiveAedtSessionManager(
        approval_verifier=authority,
        trace_store=CapabilityTraceStore(tmp_path / "capability-traces"),
    )
    hfss_app = None
    layout_app = None
    launched_pid = None
    try:
        opened = manager.attach(port=port)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        hfss = manager.create_hfss_design(
            session_id,
            project_name="LiveAcceptance",
            design_name="HFSS1",
        )
        status = manager.hfss_analysis_status(
            session_id,
            project_name="LiveAcceptance",
            design_name="HFSS1",
        )

        from ansys.aedt.core import Hfss

        hfss_app = Hfss(
            project="LiveAcceptance",
            design="HFSS1",
            version="2026.1",
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        hfss_app.modeler.create_box([0, 0, 0], [10, 10, 10], name="airbox", material="vacuum")
        geometry = manager.hfss_geometry_inventory(
            session_id,
            project_name="LiveAcceptance",
            design_name="HFSS1",
            object_names=["airbox"],
        )
        faces = geometry["objects"][0]["faces"]
        x_faces = sorted(faces, key=lambda item: item["center"][0])
        port1_preview = manager.preview_hfss_boundary(
            session_id,
            project_name="LiveAcceptance",
            design_name="HFSS1",
            boundary_kind="wave_port",
            boundary_name="P1",
            assignment_face_ids=[x_faces[0]["face_id"]],
        )
        port1 = manager.apply_hfss_boundary(
            session_id,
            preview_id=port1_preview["preview_id"],
            approval_token=authority.issue(**port1_preview["approval_request"]),
        )
        port2_preview = manager.preview_hfss_boundary(
            session_id,
            project_name="LiveAcceptance",
            design_name="HFSS1",
            boundary_kind="wave_port",
            boundary_name="P2",
            assignment_face_ids=[x_faces[-1]["face_id"]],
        )
        port2 = manager.apply_hfss_boundary(
            session_id,
            preview_id=port2_preview["preview_id"],
            approval_token=authority.issue(**port2_preview["approval_request"]),
        )
        setup_preview = manager.preview_hfss_setup(
            session_id,
            project_name="LiveAcceptance",
            design_name="HFSS1",
            setup_name="SetupControl",
            properties={"Frequency": "10GHz", "MaximumPasses": 2, "MaxDeltaS": 0.05},
        )
        setup = manager.apply_hfss_setup(
            session_id,
            preview_id=setup_preview["preview_id"],
            approval_token=authority.issue(**setup_preview["approval_request"]),
        )
        report_preview = manager.preview_hfss_report(
            session_id,
            project_name="LiveAcceptance",
            design_name="HFSS1",
            report_name="S11 Control",
            setup_sweep_name="SetupControl : LastAdaptive",
            expressions=["dB(S(P1,P1))", "dB(S(P2,P1))"],
        )
        report = manager.apply_hfss_report(
            session_id,
            preview_id=report_preview["preview_id"],
            approval_token=authority.issue(**report_preview["approval_request"]),
        )
        solve_preview = manager.preview_hfss_analysis_start(
            session_id,
            project_name="LiveAcceptance",
            design_name="HFSS1",
            setup_name="SetupControl",
            cores=2,
            tasks=1,
            gpus=0,
        )
        export_preview = manager.preview_hfss_export(
            session_id,
            project_name="LiveAcceptance",
            design_name="HFSS1",
            export_kind="report_csv",
            report_name="S11 Control",
        )

        from ansys.aedt.core import Hfss3dLayout

        layout_app = Hfss3dLayout(
            project="LiveLayoutAcceptance",
            design="Layout1",
            version="2026.1",
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        layout_app.modeler.layers.add_layer("L1")
        layout_app.modeler.create_line(
            "L1",
            [[0, 0], [10, 0]],
            lw=0.1,
            name="trace1",
            net="N1",
        )
        read_plan = {
            "schema_version": "ansys-operation-plan/v1",
            "intent": "Read one disposable live layout line width",
            "target": {
                "product": "hfss3dlayout",
                "project_name": "LiveLayoutAcceptance",
                "design_name": "Layout1",
            },
            "risk": "read_only",
            "evidence": [width_evidence],
            "steps": [
                {
                    "id": "read-width",
                    "op": "read_attr",
                    "path": "modeler.lines.trace1.width",
                }
            ],
            "readback": [],
            "rollback": [],
        }
        read_candidate = manager.propose_exploratory_operation(read_plan)
        manager.validate_exploratory_operation(read_candidate["candidate_id"])
        read_preview = manager.preview_exploratory_operation(
            session_id,
            candidate_id=read_candidate["candidate_id"],
        )
        read_result = manager.apply_exploratory_operation(
            session_id,
            preview_id=read_preview["preview_id"],
        )
        read_trace = manager.capture_capability_trace(read_candidate["candidate_id"])

        write_plan = {
            **read_plan,
            "intent": "Change one disposable live layout line width with rollback coverage",
            "risk": "reversible_edit",
            "steps": [
                {
                    "id": "set-width",
                    "op": "set_attr",
                    "path": "modeler.lines.trace1.width",
                    "value": "0.12mm",
                }
            ],
            "readback": [
                {
                    "id": "verify-width",
                    "path": "modeler.lines.trace1.width",
                    "operator": "equals",
                    "expected": "0.12mm",
                }
            ],
            "rollback": ["set-width"],
        }
        write_candidate = manager.propose_exploratory_operation(write_plan)
        manager.validate_exploratory_operation(write_candidate["candidate_id"])
        write_preview = manager.preview_exploratory_operation(
            session_id,
            candidate_id=write_candidate["candidate_id"],
        )
        write_result = manager.apply_exploratory_operation(
            session_id,
            preview_id=write_preview["preview_id"],
            approval_token=authority.issue(**write_preview["approval_request"]),
        )
        write_trace = manager.capture_capability_trace(write_candidate["candidate_id"])
        listed = manager.list_layout_paths(
            session_id,
            project_name="LiveLayoutAcceptance",
            design_name="Layout1",
            selector={"nets": ["N1"], "layers": ["L1"]},
        )
        preview = manager.preview_layout_width(
            session_id,
            project_name="LiveLayoutAcceptance",
            design_name="Layout1",
            selector={"nets": ["N1"], "layers": ["L1"]},
            variable_name="trace_w",
            variable_value="0.1mm",
        )
        applied = manager.apply_layout_width(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        released = manager.release(session_id)
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            alive_after_release = True
    finally:
        manager.close()
        if layout_app is not None:
            layout_app.release_desktop(close_projects=False, close_desktop=False)
        if launched_pid is not None:
            _close_test_owned_aedt(port, launched_pid)

    assert launched is True
    assert opened["probe"]["pid"] > 0
    assert manager.registry.broker_count == 0
    assert hfss["created_or_activated"] is True
    assert status["running"] is False
    assert geometry["object_count"] == 1
    assert port1["status"] == "verified"
    assert port2["status"] == "verified"
    assert setup["properties"]["Frequency"] == "10GHz"
    assert report["report_name"] == "S11 Control"
    assert solve_preview["resources"]["cores"] == 2
    assert solve_preview["blocking"] is False
    assert export_preview["path_policy"] == "server_managed_directory_only"
    assert read_result["status"] == "verified"
    assert read_trace["promotion_eligible"] is True
    assert write_result["status"] == "verified"
    assert write_result["readback"][0]["actual"] == "0.12mm"
    assert write_trace["promotion_eligible"] is True
    assert listed["count"] == 1
    assert preview["approval_required"] is True
    assert applied["status"] == "verified"
    assert applied["after"][0]["width_expression"] == "trace_w"
    assert applied["project_saved"] is False
    assert released["aedt_closed"] is False
    assert alive_after_release is True


def _close_test_owned_aedt(port: int, pid: int) -> None:
    try:
        from ansys.aedt.core import Desktop

        desktop = Desktop(
            version="2026.1",
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        desktop.release_desktop(close_projects=True, close_on_exit=True)
        return
    except Exception:
        pass
    try:
        import psutil

        process = psutil.Process(pid)
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        pass
