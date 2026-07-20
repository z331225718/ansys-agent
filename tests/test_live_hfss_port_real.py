from __future__ import annotations

import hashlib
import os
from pathlib import Path
import socket

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LIVE_AEDT") != "1",
    reason="real live AEDT acceptance is opt-in",
)


def test_real_live_hfss_typed_port_harness(tmp_path: Path):
    from ansys.aedt.core import Hfss
    from ansys.aedt.core.desktop import launch_aedt

    from aedt_agent.live.approval import HmacApprovalAuthority
    from aedt_agent.live.manager import LiveAedtSessionManager

    version = os.getenv("REAL_AEDT_VERSION", "2026.1")
    executable_override = os.getenv("REAL_AEDT_EXECUTABLE")
    if executable_override:
        executable = Path(executable_override)
    else:
        root_variable = "ANSYSEM_ROOT" + version.replace("20", "", 1).replace(".", "")
        executable = Path(os.environ[root_variable]) / "ansysedt.exe"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        requested_port = probe.getsockname()[1]

    launched = False
    port = requested_port
    authority = HmacApprovalAuthority("real-hfss-port-acceptance-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    hfss_app = None
    launched_pid = None
    session_id = ""
    project_path = tmp_path / "RealTypedPortAcceptance.aedt"
    fixture_names = ["PortBox", "LumpedSheet"]
    boundary_names = {"HarnessWave", "HarnessLumped", "ExternalRadiation", "MustNotCreate"}
    try:
        launched, port = launch_aedt(
            executable,
            non_graphical=True,
            port=requested_port,
            student_version=False,
        )
        hfss_app = Hfss(
            project=str(project_path),
            design="HFSS1",
            solution_type="DrivenModal",
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        port_box = hfss_app.modeler.create_box(
            ["0mm", "0mm", "0mm"],
            ["10mm", "10mm", "10mm"],
            name="PortBox",
            material="vacuum",
        )
        lumped_sheet = hfss_app.modeler.create_rectangle(
            "XY",
            ["12mm", "0mm", "0mm"],
            ["4mm", "2mm"],
            name="LumpedSheet",
        )
        assert port_box and lumped_sheet
        y_faces = sorted(port_box.faces, key=lambda face: face.center[1])
        wave_face = int(y_faces[0].id)
        stale_face = int(y_faces[-1].id)
        external_face = next(
            int(face.id)
            for face in port_box.faces
            if int(face.id) not in {wave_face, stale_face}
        )
        assert hfss_app.save_project(str(project_path)) is True
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        inventory_before = manager.hfss_port_inventory(
            session_id,
            project_name="RealTypedPortAcceptance",
            design_name="HFSS1",
        )
        assert inventory_before["port_count"] == 0
        assert inventory_before["design_unchanged"] is True

        wave_preview = manager.preview_hfss_boundary(
            session_id,
            project_name="RealTypedPortAcceptance",
            design_name="HFSS1",
            boundary_kind="wave_port",
            boundary_name="HarnessWave",
            assignment_face_ids=[wave_face],
            options={
                "modes": 2,
                "renormalize": False,
                "deembed": 1.25,
                "integration_line_direction": "XNeg",
                "characteristic_impedance": "Zwave",
            },
        )
        assert wave_preview["resolved_integration_line"] == {
            "start": ["0.0mm", "0.0mm", "5.0mm"],
            "end": ["10.0mm", "0.0mm", "5.0mm"],
        }
        wave = manager.apply_hfss_boundary(
            session_id,
            preview_id=wave_preview["preview_id"],
            approval_token=authority.issue(**wave_preview["approval_request"]),
        )

        lumped_preview = manager.preview_hfss_boundary(
            session_id,
            project_name="RealTypedPortAcceptance",
            design_name="HFSS1",
            boundary_kind="lumped_port",
            boundary_name="HarnessLumped",
            assignment_object_name="LumpedSheet",
            options={
                "impedance": 60,
                "renormalize": False,
                "deembed": True,
                "integration_line_direction": "XPos",
            },
        )
        assert lumped_preview["resolved_integration_line"] == {
            "start": ["16.0mm", "1.0mm", "0.0mm"],
            "end": ["12.0mm", "1.0mm", "0.0mm"],
        }
        lumped = manager.apply_hfss_boundary(
            session_id,
            preview_id=lumped_preview["preview_id"],
            approval_token=authority.issue(**lumped_preview["approval_request"]),
        )

        stale_preview = manager.preview_hfss_boundary(
            session_id,
            project_name="RealTypedPortAcceptance",
            design_name="HFSS1",
            boundary_kind="wave_port",
            boundary_name="MustNotCreate",
            assignment_face_ids=[stale_face],
            options={"integration_line_direction": "XPos"},
        )
        external = hfss_app.assign_radiation_boundary_to_faces(
            [external_face],
            name="ExternalRadiation",
        )
        assert external
        with pytest.raises(Exception, match="stale HFSS boundary preview"):
            manager.apply_hfss_boundary(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        with pytest.raises(Exception, match="boundary or port already exists"):
            manager.preview_hfss_boundary(
                session_id,
                project_name="RealTypedPortAcceptance",
                design_name="HFSS1",
                boundary_kind="wave_port",
                boundary_name="HarnessWave",
                assignment_face_ids=[stale_face],
            )
        with pytest.raises(Exception, match="planar sheet object"):
            manager.preview_hfss_boundary(
                session_id,
                project_name="RealTypedPortAcceptance",
                design_name="HFSS1",
                boundary_kind="lumped_port",
                boundary_name="InvalidSolidPort",
                assignment_object_name="PortBox",
            )

        inventory_after = manager.hfss_port_inventory(
            session_id,
            project_name="RealTypedPortAcceptance",
            design_name="HFSS1",
        )
        assert launched is True
        assert wave["status"] == "verified"
        assert wave["boundary"]["kind"] == "wave_port"
        assert wave["boundary"]["face_ids"] == [wave_face]
        assert wave["boundary"]["options"]["mode_count"] == 2
        assert wave["boundary"]["options"]["renormalize"] is False
        assert wave["boundary"]["options"]["deembed_enabled"] is True
        assert wave["boundary"]["options"]["deembed_distance"] == "1.25mm"
        assert {
            item["characteristic_impedance"]
            for item in wave["boundary"]["options"]["modes"]
        } == {"Zwave"}
        assert lumped["status"] == "verified"
        assert lumped["boundary"]["kind"] == "lumped_port"
        assert lumped["boundary"]["object_names"] == ["LumpedSheet"]
        assert lumped["boundary"]["options"]["impedance"] == "60.0ohm"
        assert lumped["boundary"]["options"]["renormalize"] is False
        assert lumped["boundary"]["options"]["deembed_enabled"] is True
        assert inventory_after["port_count"] == 2
        assert [item["name"] for item in inventory_after["ports"]] == [
            "HarnessLumped",
            "HarnessWave",
        ]
        assert "MustNotCreate" not in {item["name"] for item in inventory_after["ports"]}
        assert wave["automatic_rollback_on_failure"] is True
        assert lumped["automatic_rollback_on_failure"] is True
        assert wave["project_saved"] is False
        assert lumped["project_saved"] is False
        assert _file_digest(project_path) == project_digest_before
    finally:
        if hfss_app is not None:
            try:
                for boundary in list(hfss_app.boundaries or []):
                    if boundary.name in boundary_names:
                        boundary.delete()
            except Exception:
                pass
            try:
                hfss_app.modeler.delete(fixture_names)
            except Exception:
                pass
        if session_id:
            try:
                manager.release(session_id)
            except Exception:
                pass
        manager.close()
        if hfss_app is not None:
            try:
                hfss_app.release_desktop(close_projects=False, close_desktop=False)
            except Exception:
                pass
        if launched:
            _close_test_owned_aedt(port, launched_pid, version)


def _file_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _close_test_owned_aedt(port: int, pid: int | None, version: str) -> None:
    try:
        from ansys.aedt.core import Desktop

        desktop = Desktop(
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        desktop.release_desktop(close_projects=True, close_on_exit=True)
        return
    except Exception:
        pass
    if pid is None:
        return
    try:
        import psutil

        process = psutil.Process(pid)
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        pass
