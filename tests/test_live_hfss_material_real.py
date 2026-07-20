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


def test_real_live_hfss_material_assignment_harness(tmp_path: Path):
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
    authority = HmacApprovalAuthority("real-hfss-material-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    hfss_app = None
    launched_pid = None
    session_id = ""
    project_path = tmp_path / "RealMaterialAssignmentAcceptance.aedt"
    fixture_names = [
        "MaterialTarget1",
        "MaterialTarget2",
        "MaterialStaleTarget",
        "MaterialCatalogSeed",
        "MaterialSheet",
    ]
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
            name="MaterialTarget1",
            material="vacuum",
        )
        target2 = hfss_app.modeler.create_box(
            ["6mm", "0mm", "0mm"],
            ["4mm", "4mm", "1mm"],
            name="MaterialTarget2",
            material="vacuum",
        )
        stale_target = hfss_app.modeler.create_box(
            ["12mm", "0mm", "0mm"],
            ["4mm", "4mm", "1mm"],
            name="MaterialStaleTarget",
            material="vacuum",
        )
        seed = hfss_app.modeler.create_box(
            ["18mm", "0mm", "0mm"],
            ["1mm", "1mm", "1mm"],
            name="MaterialCatalogSeed",
            material="copper",
        )
        sheet = hfss_app.modeler.create_rectangle(
            "XY",
            ["0mm", "8mm", "0mm"],
            ["4mm", "4mm"],
            name="MaterialSheet",
        )
        assert all((target1, target2, stale_target, seed, sheet))
        target1.color = (10, 20, 30)
        target2.color = (40, 50, 60)
        assert hfss_app.save_project(str(project_path)) is True
        assert project_path.is_file()
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        material_inventory = manager.hfss_material_inventory(
            session_id,
            project_name="RealMaterialAssignmentAcceptance",
            design_name="HFSS1",
            max_items=100,
        )
        preview = manager.preview_hfss_material_assign(
            session_id,
            project_name="RealMaterialAssignmentAcceptance",
            design_name="HFSS1",
            object_names=["MaterialTarget1", "MaterialTarget2"],
            material_name="copper",
            max_objects=4,
        )
        applied = manager.apply_hfss_material_assign(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        inventory = manager.hfss_geometry_inventory(
            session_id,
            project_name="RealMaterialAssignmentAcceptance",
            design_name="HFSS1",
            object_names=["MaterialTarget1", "MaterialTarget2"],
        )

        stale_preview = manager.preview_hfss_material_assign(
            session_id,
            project_name="RealMaterialAssignmentAcceptance",
            design_name="HFSS1",
            object_names=["MaterialStaleTarget"],
            material_name="copper",
        )
        assert hfss_app.assign_material("MaterialStaleTarget", "copper") is True
        with pytest.raises(Exception, match="stale HFSS material assignment preview"):
            manager.apply_hfss_material_assign(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )

        with pytest.raises(Exception, match="only supports solid objects"):
            manager.preview_hfss_material_assign(
                session_id,
                project_name="RealMaterialAssignmentAcceptance",
                design_name="HFSS1",
                object_names=["MaterialSheet"],
                material_name="copper",
            )
        with pytest.raises(Exception, match="must already exist"):
            manager.preview_hfss_material_assign(
                session_id,
                project_name="RealMaterialAssignmentAcceptance",
                design_name="HFSS1",
                object_names=["MaterialStaleTarget"],
                material_name="DefinitelyMissingMaterial",
            )

        assert launched is True
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        material_names = {
            item["canonical_name"].casefold()
            for item in material_inventory["materials"]
        }
        assert {"copper", "vacuum"}.issubset(material_names)
        assert material_inventory["design_unchanged"] is True
        assert material_inventory["snapshot_digest"]
        assert preview["target_count"] == 2
        assert preview["target_solve_inside"] is False
        assert preview["target_material"]["definition_digest"]
        assert all(
            item["material_name"].casefold() == "vacuum"
            for item in preview["targets_before"]
        )
        assert applied["status"] == "verified"
        assert applied["target_count"] == applied["verified_count"] == 2
        assert applied["object_names"] == ["MaterialTarget1", "MaterialTarget2"]
        assert applied["material_name"].casefold() == "copper"
        assert applied["target_solve_inside"] is False
        assert all(
            item["material_name"].casefold() == "copper"
            and item["solve_inside"] is False
            for item in applied["targets_after"]
        )
        assert applied["automatic_rollback_on_failure"] is True
        assert applied["project_saved"] is False
        assert inventory["object_count"] == 2
        assert all(
            item["material_name"].casefold() == "copper"
            and item["solve_inside"] is False
            for item in inventory["objects"]
        )
        assert stale_target.material_name.casefold() == "copper"
        assert _file_digest(project_path) == project_digest_before
    finally:
        if hfss_app is not None:
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
