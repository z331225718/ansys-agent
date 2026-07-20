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


def test_real_live_hfss_geometry_harness(tmp_path: Path):
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
    launched, port = launch_aedt(
        executable,
        non_graphical=True,
        port=requested_port,
        student_version=False,
    )
    authority = HmacApprovalAuthority("real-geometry-acceptance-secret-32")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    hfss_app = None
    launched_pid = None
    session_id = ""
    project_path = tmp_path / "RealGeometryAcceptance.aedt"
    try:
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
        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        project_digest_before = _file_digest(project_path)
        primitives = [
            {
                "kind": "box",
                "name": "HarnessBox",
                "origin": ["0mm", "0mm", "0mm"],
                "size": ["10mm", "5mm", "1mm"],
                "material": "copper",
                "solve_inside": False,
            },
            {
                "kind": "rectangle",
                "name": "HarnessSheet",
                "orientation": "XY",
                "origin": ["0mm", "0mm", "1mm"],
                "size": ["10mm", "5mm"],
            },
            {
                "kind": "cylinder",
                "name": "HarnessCylinder",
                "axis": "Z",
                "origin": ["2mm", "2mm", "1mm"],
                "radius": "0.25mm",
                "height": "2mm",
                "num_sides": 12,
                "material": "copper",
                "solve_inside": False,
            },
            {
                "kind": "region",
                "name": "HarnessRegion",
                "padding": ["5mm"] * 6,
                "padding_type": "Absolute Offset",
            },
        ]
        preview = manager.preview_hfss_geometry_create(
            session_id,
            project_name="RealGeometryAcceptance",
            design_name="HFSS1",
            primitives=primitives,
            max_new_objects=4,
        )
        applied = manager.apply_hfss_geometry_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        inventory = manager.hfss_geometry_inventory(
            session_id,
            project_name="RealGeometryAcceptance",
            design_name="HFSS1",
            object_names=[item["name"] for item in primitives],
        )

        stale_preview = manager.preview_hfss_geometry_create(
            session_id,
            project_name="RealGeometryAcceptance",
            design_name="HFSS1",
            primitives=[
                {
                    "kind": "box",
                    "name": "MustNotBeCreated",
                    "origin": ["20mm", "0mm", "0mm"],
                    "size": ["1mm", "1mm", "1mm"],
                    "material": "vacuum",
                }
            ],
        )
        sentinel = hfss_app.modeler.create_box(
            ["30mm", "0mm", "0mm"],
            ["1mm", "1mm", "1mm"],
            name="ExternalSentinel",
            material="vacuum",
        )
        assert sentinel
        with pytest.raises(Exception, match="stale HFSS geometry create preview"):
            manager.apply_hfss_geometry_create(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        names_after_stale = set(hfss_app.modeler.object_names)
        hfss_app.modeler.delete(
            [
                "ExternalSentinel",
                "HarnessRegion",
                "HarnessCylinder",
                "HarnessSheet",
                "HarnessBox",
            ]
        )

        assert launched is True
        assert hfss_app.project_name == "RealGeometryAcceptance"
        assert hfss_app.design_name == "HFSS1"
        assert preview["model_units"]
        assert preview["project_dirty"] is False
        assert applied["status"] == "verified"
        assert applied["created_object_count"] == 4
        assert applied["created_object_names"] == [item["name"] for item in primitives]
        assert applied["automatic_rollback_on_failure"] is True
        assert applied["project_saved"] is False
        assert _file_digest(project_path) == project_digest_before
        assert inventory["object_count"] == 4
        assert {item["name"] for item in inventory["objects"]} == {
            item["name"] for item in primitives
        }
        assert "MustNotBeCreated" not in names_after_stale
    finally:
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
        if launched_pid is not None:
            _close_test_owned_aedt(port, launched_pid, version)


def _file_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _close_test_owned_aedt(port: int, pid: int, version: str) -> None:
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
    try:
        import psutil

        process = psutil.Process(pid)
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        pass
