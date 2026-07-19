from __future__ import annotations

import hashlib
import os
from pathlib import Path
import socket
import time

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LIVE_AEDT") != "1",
    reason="real live AEDT acceptance is opt-in",
)


def test_real_live_hfss_material_delete_harness(tmp_path: Path, monkeypatch):
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
    launched_pid = None
    hfss_app = None
    session_id = ""
    direct_backend = None
    authority = HmacApprovalAuthority("real-material-delete-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    project_path = tmp_path / "RealMaterialDeleteAcceptance.aedt"
    created_materials = {
        "HarnessDeleteA",
        "HarnessDeleteB",
        "HarnessRollbackA",
        "HarnessRollbackB",
        "HarnessStaleTarget",
        "HarnessSolidUsed",
        "HarnessBoundaryUsed",
        "ExternalMaterial",
    }
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
        material_specs = {
            "HarnessDeleteA": (3.1, 0.01, [11, 21, 31, 0.31]),
            "HarnessDeleteB": (3.2, 0.02, [12, 22, 32, 0.32]),
            "HarnessRollbackA": (4.1, 0.03, [41, 51, 61, 0.41]),
            "HarnessRollbackB": (4.2, 0.04, [42, 52, 62, 0.42]),
            "HarnessStaleTarget": (5.1, 0.05, [51, 61, 71, 0.51]),
            "HarnessSolidUsed": (2.8, 0.01, [71, 81, 91, 0.2]),
            "HarnessBoundaryUsed": (1.0, 5_800_000.0, [91, 81, 71, 0.1]),
        }
        for name, (permittivity, conductivity, appearance) in material_specs.items():
            material = hfss_app.materials.add_material(
                name,
                properties={
                    "permittivity": permittivity,
                    "permeability": 1.01,
                    "conductivity": conductivity,
                    "dielectric_loss_tangent": 0.012,
                    "magnetic_loss_tangent": 0.003,
                },
            )
            material.material_appearance = appearance
        solid_used = hfss_app.modeler.create_box(
            ["0mm", "0mm", "0mm"],
            ["1mm", "1mm", "1mm"],
            name="SolidUsedBox",
            material="HarnessSolidUsed",
        )
        boundary_host = hfss_app.modeler.create_box(
            ["2mm", "0mm", "0mm"],
            ["1mm", "1mm", "1mm"],
            name="BoundaryHostBox",
            material="vacuum",
        )
        assert solid_used and boundary_host
        boundary = hfss_app.assign_finite_conductivity(
            [int(boundary_host.faces[0].id)],
            material="HarnessBoundaryUsed",
            name="HarnessMaterialBoundary",
        )
        assert boundary
        assert hfss_app.save_project(str(project_path)) is True
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        inventory_before = manager.hfss_material_inventory(
            session_id,
            project_name="RealMaterialDeleteAcceptance",
            design_name="HFSS1",
            max_items=500,
        )
        preview = manager.preview_hfss_material_delete(
            session_id,
            project_name="RealMaterialDeleteAcceptance",
            design_name="HFSS1",
            names=["HarnessDeleteA", "HarnessDeleteB"],
            max_materials=2,
        )
        inventory_after_preview = manager.hfss_material_inventory(
            session_id,
            project_name="RealMaterialDeleteAcceptance",
            design_name="HFSS1",
            max_items=500,
        )
        assert preview["target_count"] == 2
        assert preview["solid_reference_count"] == 0
        assert preview["boundary_reference_count"] == 0
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        assert inventory_after_preview["snapshot_digest"] == inventory_before["snapshot_digest"]
        assert _file_digest(project_path) == project_digest_before

        applied = manager.apply_hfss_material_delete(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        assert applied["status"] == "verified"
        assert applied["deleted_material_names"] == ["HarnessDeleteA", "HarnessDeleteB"]
        assert applied["deleted_material_count"] == 2
        assert applied["solid_reference_count"] == 0
        assert applied["boundary_reference_count"] == 0
        assert applied["absence_digest"]
        assert applied["automatic_rollback_on_failure"] is True
        assert applied["project_saved"] is False
        remaining_names = {
            item["canonical_name"]
            for item in manager.hfss_material_inventory(
                session_id,
                project_name="RealMaterialDeleteAcceptance",
                design_name="HFSS1",
                max_items=500,
            )["materials"]
        }
        assert "HarnessDeleteA" not in remaining_names
        assert "HarnessDeleteB" not in remaining_names
        assert _file_digest(project_path) == project_digest_before

        with pytest.raises(Exception, match="zero solid-object references"):
            manager.preview_hfss_material_delete(
                session_id,
                project_name="RealMaterialDeleteAcceptance",
                design_name="HFSS1",
                names=["HarnessSolidUsed"],
            )
        with pytest.raises(Exception, match="zero boundary references"):
            manager.preview_hfss_material_delete(
                session_id,
                project_name="RealMaterialDeleteAcceptance",
                design_name="HFSS1",
                names=["HarnessBoundaryUsed"],
            )
        still_healthy = manager.hfss_material_inventory(
            session_id,
            project_name="RealMaterialDeleteAcceptance",
            design_name="HFSS1",
            max_items=500,
        )
        assert still_healthy["material_count"] == inventory_before["material_count"] - 2

        stale_preview = manager.preview_hfss_material_delete(
            session_id,
            project_name="RealMaterialDeleteAcceptance",
            design_name="HFSS1",
            names=["HarnessStaleTarget"],
        )
        external = hfss_app.materials.add_material(
            "ExternalMaterial",
            properties={"permittivity": 2.6, "conductivity": 0.01},
        )
        assert external and external.name == "ExternalMaterial"
        with pytest.raises(Exception, match="stale HFSS material delete preview"):
            manager.apply_hfss_material_delete(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        assert hfss_app.materials.remove_material("ExternalMaterial") is True
        assert _file_digest(project_path) == project_digest_before

        manager.release(session_id)
        session_id = ""
        from aedt_agent.live import backend as backend_module
        from aedt_agent.live.backend import LiveAedtBackend, LiveBackendError
        from aedt_agent.live.target import AedtTarget

        direct_backend = LiveAedtBackend(version=version)
        target = AedtTarget("port", port)
        rollback_before = direct_backend.execute(
            target,
            "hfss_material_inventory",
            {
                "project_name": "RealMaterialDeleteAcceptance",
                "design_name": "HFSS1",
                "max_items": 500,
            },
        )
        rollback_preview = direct_backend.execute(
            target,
            "hfss_material_delete_preview",
            {
                "project_name": "RealMaterialDeleteAcceptance",
                "design_name": "HFSS1",
                "names": ["HarnessRollbackA", "HarnessRollbackB"],
            },
        )
        with monkeypatch.context() as patch:
            patch.setattr(
                backend_module,
                "_verify_hfss_material_delete_catalog",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    LiveBackendError("injected real material delete readback failure")
                ),
            )
            with pytest.raises(
                LiveBackendError,
                match="injected real material delete readback failure",
            ):
                direct_backend.execute(
                    target,
                    "hfss_material_delete_apply",
                    {"preview_id": rollback_preview["preview_id"]},
                )
        rollback_after = direct_backend.execute(
            target,
            "hfss_material_inventory",
            {
                "project_name": "RealMaterialDeleteAcceptance",
                "design_name": "HFSS1",
                "max_items": 500,
            },
        )
        assert rollback_after["snapshot_digest"] == rollback_before["snapshot_digest"]
        restored_by_name = {
            item["canonical_name"]: item for item in rollback_after["materials"]
        }
        assert restored_by_name["HarnessRollbackA"]["definition_digest"]
        assert restored_by_name["HarnessRollbackB"]["definition_digest"]
        assert _file_digest(project_path) == project_digest_before
    finally:
        if session_id:
            try:
                manager.release(session_id)
            except Exception:
                pass
        if direct_backend is not None:
            try:
                direct_backend.release()
            except Exception:
                pass
        manager.close()
        if hfss_app is not None and direct_backend is None:
            try:
                if "HarnessMaterialBoundary" in [item.name for item in hfss_app.boundaries]:
                    next(
                        item
                        for item in hfss_app.boundaries
                        if item.name == "HarnessMaterialBoundary"
                    ).delete()
                for object_name in ("SolidUsedBox", "BoundaryHostBox"):
                    if object_name in hfss_app.modeler.object_names:
                        hfss_app.modeler.delete(object_name)
                for name in created_materials:
                    if name.casefold() in hfss_app.materials.material_keys:
                        hfss_app.materials.remove_material(name)
            except Exception:
                pass
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
    except Exception:
        pass
    if pid is None:
        return
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        try:
            import psutil

            if not psutil.pid_exists(pid):
                return
        except Exception:
            return
        time.sleep(0.25)
    try:
        import psutil

        process = psutil.Process(pid)
        if process.name().casefold() == "ansysedt.exe":
            process.terminate()
            process.wait(timeout=10)
    except Exception:
        pass
