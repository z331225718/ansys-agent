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


def test_real_live_hfss_length_mesh_harness(tmp_path: Path):
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
    authority = HmacApprovalAuthority("real-hfss-length-mesh-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    hfss_app = None
    launched_pid = None
    session_id = ""
    project_path = tmp_path / "RealLengthMeshAcceptance.aedt"
    fixture_names = [
        "MeshTarget1",
        "MeshTarget2",
        "MeshStaleTarget",
        "MeshSheet",
    ]
    mesh_names = {"HarnessLength", "StaleLength", "ExternalLength"}
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
        target1 = hfss_app.modeler.create_box(
            ["0mm", "0mm", "0mm"],
            ["4mm", "4mm", "1mm"],
            name="MeshTarget1",
            material="vacuum",
        )
        target2 = hfss_app.modeler.create_box(
            ["6mm", "0mm", "0mm"],
            ["4mm", "4mm", "1mm"],
            name="MeshTarget2",
            material="vacuum",
        )
        stale_target = hfss_app.modeler.create_box(
            ["12mm", "0mm", "0mm"],
            ["4mm", "4mm", "1mm"],
            name="MeshStaleTarget",
            material="vacuum",
        )
        sheet = hfss_app.modeler.create_rectangle(
            "XY",
            ["0mm", "8mm", "0mm"],
            ["4mm", "4mm"],
            name="MeshSheet",
        )
        assert all((target1, target2, stale_target, sheet))
        assert hfss_app.save_project(str(project_path)) is True
        assert project_path.is_file()
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        inventory_before = manager.hfss_mesh_inventory(
            session_id,
            project_name="RealLengthMeshAcceptance",
            design_name="HFSS1",
        )
        preview = manager.preview_hfss_length_mesh_create(
            session_id,
            project_name="RealLengthMeshAcceptance",
            design_name="HFSS1",
            mesh_name="HarnessLength",
            object_names=["MeshTarget1", "MeshTarget2"],
            inside_selection=True,
            maximum_length="0.4mm",
            maximum_elements=500,
            max_objects=4,
        )
        applied = manager.apply_hfss_length_mesh_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        inventory_after = manager.hfss_mesh_inventory(
            session_id,
            project_name="RealLengthMeshAcceptance",
            design_name="HFSS1",
        )

        stale_preview = manager.preview_hfss_length_mesh_create(
            session_id,
            project_name="RealLengthMeshAcceptance",
            design_name="HFSS1",
            mesh_name="StaleLength",
            object_names=["MeshStaleTarget"],
            inside_selection=False,
            maximum_length="0.8mm",
            maximum_elements=800,
        )
        external = hfss_app.mesh.assign_length_mesh(
            ["MeshStaleTarget"],
            inside_selection=False,
            maximum_length="0.8mm",
            maximum_elements=800,
            name="ExternalLength",
        )
        assert external
        with pytest.raises(Exception, match="stale HFSS length mesh create preview"):
            manager.apply_hfss_length_mesh_create(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        with pytest.raises(Exception, match="mesh operation already exists"):
            manager.preview_hfss_length_mesh_create(
                session_id,
                project_name="RealLengthMeshAcceptance",
                design_name="HFSS1",
                mesh_name="ExternalLength",
                object_names=["MeshStaleTarget"],
                maximum_length="0.8mm",
                maximum_elements=800,
            )
        with pytest.raises(Exception, match="only supports solid objects"):
            manager.preview_hfss_length_mesh_create(
                session_id,
                project_name="RealLengthMeshAcceptance",
                design_name="HFSS1",
                mesh_name="SheetLength",
                object_names=["MeshSheet"],
                maximum_length="0.2mm",
                maximum_elements=200,
            )

        assert launched is True
        assert inventory_before["mesh_operation_count"] == 0
        assert inventory_before["design_unchanged"] is True
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        assert preview["target_count"] == 2
        assert preview["existing_mesh_operation_names"] == []
        assert applied["status"] == "verified"
        assert applied["created_mesh_operation_name"] == "HarnessLength"
        assert applied["target_count"] == 2
        readback = applied["mesh_operation"]
        assert readback["name"] == "HarnessLength"
        assert readback["type"].casefold().replace(" ", "") == "lengthbased"
        assert readback["object_names"] == ["MeshTarget1", "MeshTarget2"]
        assert readback["inside_selection"] is True
        assert readback["restrict_length"] is True
        assert readback["maximum_length"] == "0.4mm"
        assert readback["restrict_elements"] is True
        assert readback["maximum_elements"] == 500
        assert readback["property_digest"]
        assert applied["automatic_rollback_on_failure"] is True
        assert applied["project_saved"] is False
        inventory_by_name = {
            item["name"]: item for item in inventory_after["mesh_operations"]
        }
        assert inventory_by_name["HarnessLength"] == readback
        assert "StaleLength" not in {
            item["name"]
            for item in manager.hfss_mesh_inventory(
                session_id,
                project_name="RealLengthMeshAcceptance",
                design_name="HFSS1",
            )["mesh_operations"]
        }
        assert _file_digest(project_path) == project_digest_before
    finally:
        if hfss_app is not None:
            try:
                hfss_app.mesh._meshoperations = None
                for operation in list(hfss_app.mesh.meshoperations or []):
                    if operation.name in mesh_names:
                        operation.delete()
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
