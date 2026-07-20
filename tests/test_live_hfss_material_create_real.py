from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import socket
import time

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LIVE_AEDT") != "1",
    reason="real live AEDT acceptance is opt-in",
)


def test_real_live_hfss_material_create_harness(tmp_path: Path, monkeypatch):
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
    authority = HmacApprovalAuthority("real-material-create-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    project_path = tmp_path / "RealMaterialCreateAcceptance.aedt"
    material_names = {
        "HarnessLaminate",
        "HarnessConductor",
        "ExternalMaterial",
        "MustNotCreate",
        "RollbackMaterial",
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
        seed = hfss_app.modeler.create_box(
            ["0mm", "0mm", "0mm"],
            ["1mm", "1mm", "1mm"],
            name="MaterialSeed",
            material="copper",
        )
        assert seed
        assert hfss_app.save_project(str(project_path)) is True
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        inventory_before = manager.hfss_material_inventory(
            session_id,
            project_name="RealMaterialCreateAcceptance",
            design_name="HFSS1",
            max_items=500,
        )
        preview = manager.preview_hfss_material_create(
            session_id,
            project_name="RealMaterialCreateAcceptance",
            design_name="HFSS1",
            material_name="HarnessLaminate",
            permittivity=4.2,
            permeability=1.01,
            conductivity=0.005,
            dielectric_loss_tangent=0.018,
            magnetic_loss_tangent=0.002,
            appearance=[10, 20, 30, 0.4],
        )
        inventory_after_preview = manager.hfss_material_inventory(
            session_id,
            project_name="RealMaterialCreateAcceptance",
            design_name="HFSS1",
            max_items=500,
        )
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        assert inventory_after_preview["snapshot_digest"] == inventory_before["snapshot_digest"]
        assert _file_digest(project_path) == project_digest_before

        applied = manager.apply_hfss_material_create(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        material = applied["material"]
        assert applied["status"] == "verified"
        assert applied["created_material_name"] == "HarnessLaminate"
        assert material["canonical_name"] == "HarnessLaminate"
        assert material["is_dielectric"] is True
        assert material["appearance"] == [10, 20, 30, 0.4]
        expected = {
            "permittivity": 4.2,
            "permeability": 1.01,
            "conductivity": 0.005,
            "dielectric_loss_tangent": 0.018,
            "magnetic_loss_tangent": 0.002,
        }
        for name, value in expected.items():
            readback = material["electrical_properties"][name]
            assert readback["type"] == "simple"
            assert float(readback["value"]) == pytest.approx(value)
        assert material["definition_digest"]
        assert applied["automatic_rollback_on_failure"] is True
        assert applied["project_saved"] is False
        assert _file_digest(project_path) == project_digest_before

        conductor_preview = manager.preview_hfss_material_create(
            session_id,
            project_name="RealMaterialCreateAcceptance",
            design_name="HFSS1",
            material_name="HarnessConductor",
            conductivity=58_000_000.0,
        )
        conductor = manager.apply_hfss_material_create(
            session_id,
            preview_id=conductor_preview["preview_id"],
            approval_token=authority.issue(**conductor_preview["approval_request"]),
        )
        assert conductor["status"] == "verified"
        assert conductor["material"]["is_dielectric"] is False
        assert float(
            conductor["material"]["electrical_properties"]["conductivity"]["value"]
        ) == pytest.approx(58_000_000.0)

        project_names = {
            str(item).casefold()
            for item in hfss_app.materials.odefinition_manager.GetProjectMaterialNames()
        }
        library_only_name = next(
            name
            for name in hfss_app.materials.mat_names_aedt
            if name.casefold() not in project_names
            and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_. +()-]{0,127}", name)
        )
        with pytest.raises(Exception, match="material library entry"):
            manager.preview_hfss_material_create(
                session_id,
                project_name="RealMaterialCreateAcceptance",
                design_name="HFSS1",
                material_name=library_only_name,
            )

        stale_preview = manager.preview_hfss_material_create(
            session_id,
            project_name="RealMaterialCreateAcceptance",
            design_name="HFSS1",
            material_name="MustNotCreate",
            permittivity=2.2,
        )
        external = hfss_app.materials.add_material(
            "ExternalMaterial",
            properties={"permittivity": 2.8},
        )
        assert external and external.name == "ExternalMaterial"
        with pytest.raises(Exception, match="stale HFSS material create preview"):
            manager.apply_hfss_material_create(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        assert "mustnotcreate" not in hfss_app.materials.material_keys
        with pytest.raises(Exception, match="already exists"):
            manager.preview_hfss_material_create(
                session_id,
                project_name="RealMaterialCreateAcceptance",
                design_name="HFSS1",
                material_name="harnesslaminate",
            )
        with pytest.raises(Exception, match="permittivity must be between"):
            manager.preview_hfss_material_create(
                session_id,
                project_name="RealMaterialCreateAcceptance",
                design_name="HFSS1",
                material_name="InvalidMaterial",
                permittivity=0.0,
            )
        assert _file_digest(project_path) == project_digest_before

        if "harnesslaminate" not in hfss_app.materials.material_keys:
            assert hfss_app.materials._aedmattolibrary("HarnessLaminate")
        assert hfss_app.materials.remove_material("HarnessLaminate") is True
        if "harnessconductor" not in hfss_app.materials.material_keys:
            assert hfss_app.materials._aedmattolibrary("HarnessConductor")
        assert hfss_app.materials.remove_material("HarnessConductor") is True
        assert hfss_app.materials.remove_material("ExternalMaterial") is True
        hfss_app.modeler.delete("MaterialSeed")

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
                "project_name": "RealMaterialCreateAcceptance",
                "design_name": "HFSS1",
                "max_items": 500,
            },
        )
        rollback_preview = direct_backend.execute(
            target,
            "hfss_material_create_preview",
            {
                "project_name": "RealMaterialCreateAcceptance",
                "design_name": "HFSS1",
                "material_name": "RollbackMaterial",
                "permittivity": 3.3,
                "dielectric_loss_tangent": 0.01,
            },
        )
        with monkeypatch.context() as patch:
            patch.setattr(
                backend_module,
                "_verify_hfss_material_create_readback",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    LiveBackendError("injected real material readback failure")
                ),
            )
            with pytest.raises(
                LiveBackendError,
                match="injected real material readback failure",
            ):
                direct_backend.execute(
                    target,
                    "hfss_material_create_apply",
                    {"preview_id": rollback_preview["preview_id"]},
                )
        rollback_after = direct_backend.execute(
            target,
            "hfss_material_inventory",
            {
                "project_name": "RealMaterialCreateAcceptance",
                "design_name": "HFSS1",
                "max_items": 500,
            },
        )
        assert rollback_after["snapshot_digest"] == rollback_before["snapshot_digest"]
        assert "rollbackmaterial" not in {
            item["canonical_name"].casefold()
            for item in rollback_after["materials"]
        }
        assert _file_digest(project_path) == project_digest_before
    finally:
        if session_id:
            try:
                manager.release(session_id)
            except Exception:
                pass
        if hfss_app is not None:
            try:
                for name in material_names:
                    if name.casefold() in hfss_app.materials.material_keys:
                        hfss_app.materials.remove_material(name)
            except Exception:
                pass
        if direct_backend is not None:
            try:
                direct_backend.release()
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
    except Exception:
        pass
    if pid is None:
        return
    try:
        import psutil

        process = psutil.Process(pid)
        for _ in range(50):
            if not process.is_running():
                return
            time.sleep(0.2)
        process.terminate()
        process.wait(timeout=10)
    except psutil.NoSuchProcess:
        return
    except Exception:
        pass
