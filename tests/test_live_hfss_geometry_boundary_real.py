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


def test_real_live_hfss_atomic_geometry_boundary_harness(tmp_path: Path):
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
    authority = HmacApprovalAuthority("real-atomic-geometry-boundary-secret")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    hfss_app = None
    launched_pid = None
    session_id = ""
    project_path = tmp_path / "RealAtomicGeometryBoundaryAcceptance.aedt"
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
                "name": "AtomicPortBody",
                "origin": ["0mm", "0mm", "0mm"],
                "size": ["10mm", "5mm", "1mm"],
                "material": "vacuum",
            },
            {
                "kind": "region",
                "name": "AtomicAirRegion",
                "padding": ["5mm"] * 6,
                "padding_type": "Absolute Offset",
            },
        ]
        boundaries = [
            {
                "boundary_kind": "wave_port",
                "boundary_name": "AtomicPort",
                "assignment_object": "AtomicPortBody",
                "face_selector": "x_min",
            },
            {
                "boundary_kind": "radiation",
                "boundary_name": "AtomicRadiation",
                "assignment_object": "AtomicAirRegion",
                "face_selector": "all_faces",
            },
        ]
        preview = manager.preview_hfss_geometry_boundary_create(
            session_id,
            project_name="RealAtomicGeometryBoundaryAcceptance",
            design_name="HFSS1",
            primitives=primitives,
            boundaries=boundaries,
            max_new_objects=2,
            max_new_boundaries=2,
        )
        applied = manager.apply_hfss_geometry_boundary_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        design_inventory = manager.hfss_design_inventory(
            session_id,
            project_name="RealAtomicGeometryBoundaryAcceptance",
            design_name="HFSS1",
        )

        stale_preview = manager.preview_hfss_geometry_boundary_create(
            session_id,
            project_name="RealAtomicGeometryBoundaryAcceptance",
            design_name="HFSS1",
            primitives=[
                {
                    "kind": "box",
                    "name": "MustNotBeCreated",
                    "origin": ["20mm", "0mm", "0mm"],
                    "size": ["1mm", "1mm", "1mm"],
                }
            ],
            boundaries=[
                {
                    "boundary_kind": "radiation",
                    "boundary_name": "MustNotBeAssigned",
                    "assignment_object": "MustNotBeCreated",
                    "face_selector": "all_faces",
                }
            ],
        )
        sentinel = hfss_app.modeler.create_box(
            ["30mm", "0mm", "0mm"],
            ["1mm", "1mm", "1mm"],
            name="ExternalAtomicSentinel",
            material="vacuum",
        )
        assert sentinel
        with pytest.raises(Exception, match="stale HFSS geometry and boundary create preview"):
            manager.apply_hfss_geometry_boundary_create(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        names_after_stale = set(hfss_app.modeler.object_names)
        boundaries_after_stale = {
            str(getattr(item, "name", item)) for item in list(hfss_app.boundaries or [])
        }

        assert launched is True
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        assert applied["status"] == "verified"
        assert applied["created_object_names"] == ["AtomicPortBody", "AtomicAirRegion"]
        assert applied["created_boundary_names"] == ["AtomicPort", "AtomicRadiation"]
        assert applied["created_object_count"] == 2
        assert applied["created_boundary_count"] == 2
        assert len(applied["resolved_boundaries"][0]["assignment_face_ids"]) == 1
        assert len(applied["resolved_boundaries"][1]["assignment_face_ids"]) == 6
        assert "wave" in applied["resolved_boundaries"][0]["readback_type"].casefold()
        assert "radiation" in applied["resolved_boundaries"][1]["readback_type"].casefold()
        assert applied["atomic_geometry_boundary_transaction"] is True
        assert applied["automatic_rollback_on_failure"] is True
        assert applied["project_saved"] is False
        assert _file_digest(project_path) == project_digest_before
        inventory_names = {
            str(item["name"]) for item in list(design_inventory.get("boundaries") or [])
        }
        assert {"AtomicPort", "AtomicRadiation"}.issubset(inventory_names)
        assert "MustNotBeCreated" not in names_after_stale
        assert "MustNotBeAssigned" not in boundaries_after_stale
    finally:
        if hfss_app is not None:
            for boundary in list(hfss_app.boundaries or []):
                if str(getattr(boundary, "name", boundary)) in {
                    "AtomicPort",
                    "AtomicRadiation",
                    "MustNotBeAssigned",
                }:
                    try:
                        boundary.delete()
                    except Exception:
                        pass
            try:
                hfss_app.modeler.delete(
                    [
                        "ExternalAtomicSentinel",
                        "MustNotBeCreated",
                        "AtomicAirRegion",
                        "AtomicPortBody",
                    ]
                )
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
