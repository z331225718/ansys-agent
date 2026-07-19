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


def test_real_live_hfss_material_update_harness(tmp_path: Path, monkeypatch):
    from ansys.aedt.core import Hfss
    from ansys.aedt.core.desktop import launch_aedt

    from aedt_agent.live.approval import HmacApprovalAuthority
    from aedt_agent.live.backend import _hfss_material_target_snapshot
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
    authority = HmacApprovalAuthority("real-material-update-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    project_path = tmp_path / "RealMaterialUpdateAcceptance.aedt"
    material_names = {
        "HarnessUpdateA",
        "HarnessUpdateB",
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
        first = hfss_app.materials.add_material(
            "HarnessUpdateA",
            properties={
                "permittivity": 3.2,
                "permeability": 1.01,
                "conductivity": 0.02,
                "dielectric_loss_tangent": 0.011,
                "magnetic_loss_tangent": 0.003,
            },
        )
        first.material_appearance = [11, 22, 33, 0.35]
        second = hfss_app.materials.add_material(
            "HarnessUpdateB",
            properties={
                "permittivity": 4.1,
                "permeability": 1.02,
                "conductivity": 0.04,
                "dielectric_loss_tangent": 0.015,
                "magnetic_loss_tangent": 0.005,
            },
        )
        second.material_appearance = [41, 42, 43, 0.45]
        assert hfss_app.modeler.create_box(
            ["0mm", "0mm", "0mm"],
            ["1mm", "1mm", "1mm"],
            name="MaterialBoxA",
            material="HarnessUpdateA",
        )
        assert hfss_app.modeler.create_box(
            ["2mm", "0mm", "0mm"],
            ["1mm", "1mm", "1mm"],
            name="MaterialBoxB",
            material="HarnessUpdateB",
        )
        assert hfss_app.save_project(str(project_path)) is True
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        inventory_before = manager.hfss_material_inventory(
            session_id,
            project_name="RealMaterialUpdateAcceptance",
            design_name="HFSS1",
            max_items=500,
        )
        references_before = _hfss_material_target_snapshot(
            hfss_app,
            ["MaterialBoxA", "MaterialBoxB"],
        )
        updates = [
            {
                "material_name": "HarnessUpdateA",
                "permittivity": 4.4,
                "permeability": 1.2,
                "appearance": [44, 55, 66, 0.6],
            },
            {
                "material_name": "HarnessUpdateB",
                "conductivity": 0.5,
                "dielectric_loss_tangent": 0.021,
                "magnetic_loss_tangent": 0.004,
            },
        ]
        preview = manager.preview_hfss_material_update(
            session_id,
            project_name="RealMaterialUpdateAcceptance",
            design_name="HFSS1",
            updates=updates,
            max_materials=2,
        )
        inventory_after_preview = manager.hfss_material_inventory(
            session_id,
            project_name="RealMaterialUpdateAcceptance",
            design_name="HFSS1",
            max_items=500,
        )
        assert preview["target_count"] == 2
        assert preview["reference_count"] == 2
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        assert inventory_after_preview["snapshot_digest"] == inventory_before["snapshot_digest"]
        assert _file_digest(project_path) == project_digest_before

        applied = manager.apply_hfss_material_update(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        assert applied["status"] == "verified"
        assert applied["updated_material_names"] == ["HarnessUpdateA", "HarnessUpdateB"]
        assert applied["updated_material_count"] == 2
        assert applied["references_after"] == applied["references_before"]
        assert applied["references_after"] == references_before
        after = {item["canonical_name"]: item for item in applied["targets_after"]}
        assert float(after["HarnessUpdateA"]["electrical_properties"]["permittivity"]["value"]) == pytest.approx(4.4)
        assert float(after["HarnessUpdateA"]["electrical_properties"]["permeability"]["value"]) == pytest.approx(1.2)
        assert after["HarnessUpdateA"]["appearance"] == [44, 55, 66, 0.6]
        assert float(after["HarnessUpdateA"]["electrical_properties"]["conductivity"]["value"]) == pytest.approx(0.02)
        assert float(after["HarnessUpdateB"]["electrical_properties"]["conductivity"]["value"]) == pytest.approx(0.5)
        assert float(after["HarnessUpdateB"]["electrical_properties"]["dielectric_loss_tangent"]["value"]) == pytest.approx(0.021)
        assert float(after["HarnessUpdateB"]["electrical_properties"]["magnetic_loss_tangent"]["value"]) == pytest.approx(0.004)
        assert all(item["is_dielectric"] is True for item in after.values())
        assert applied["automatic_rollback_on_failure"] is True
        assert applied["project_saved"] is False
        assert _file_digest(project_path) == project_digest_before

        with pytest.raises(Exception, match="exact case"):
            manager.preview_hfss_material_update(
                session_id,
                project_name="RealMaterialUpdateAcceptance",
                design_name="HFSS1",
                updates=[{"material_name": "harnessupdatea", "permittivity": 4.8}],
            )
        with pytest.raises(Exception, match="no-op"):
            manager.preview_hfss_material_update(
                session_id,
                project_name="RealMaterialUpdateAcceptance",
                design_name="HFSS1",
                updates=[{"material_name": "HarnessUpdateA", "permittivity": 4.4}],
            )
        with pytest.raises(Exception, match="cannot cross the dielectric/conductor threshold"):
            manager.preview_hfss_material_update(
                session_id,
                project_name="RealMaterialUpdateAcceptance",
                design_name="HFSS1",
                updates=[{"material_name": "HarnessUpdateA", "conductivity": 100000.0}],
            )

        stale_preview = manager.preview_hfss_material_update(
            session_id,
            project_name="RealMaterialUpdateAcceptance",
            design_name="HFSS1",
            updates=[{"material_name": "HarnessUpdateA", "permittivity": 4.8}],
        )
        external = hfss_app.materials.add_material(
            "ExternalMaterial",
            properties={"permittivity": 2.8},
        )
        assert external and external.name == "ExternalMaterial"
        with pytest.raises(Exception, match="stale HFSS material update preview"):
            manager.apply_hfss_material_update(
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
                "project_name": "RealMaterialUpdateAcceptance",
                "design_name": "HFSS1",
                "max_items": 500,
            },
        )
        direct_app = direct_backend._app(
            target,
            "hfss",
            "RealMaterialUpdateAcceptance",
            "HFSS1",
        )
        rollback_references_before = _hfss_material_target_snapshot(
            direct_app,
            ["MaterialBoxA", "MaterialBoxB"],
        )
        rollback_preview = direct_backend.execute(
            target,
            "hfss_material_update_preview",
            {
                "project_name": "RealMaterialUpdateAcceptance",
                "design_name": "HFSS1",
                "updates": [
                    {"material_name": "HarnessUpdateA", "permittivity": 5.1},
                    {
                        "material_name": "HarnessUpdateB",
                        "conductivity": 0.8,
                        "appearance": [70, 80, 90, 0.25],
                    },
                ],
            },
        )
        with monkeypatch.context() as patch:
            patch.setattr(
                backend_module,
                "_verify_hfss_material_update_catalog",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    LiveBackendError("injected real material update readback failure")
                ),
            )
            with pytest.raises(
                LiveBackendError,
                match="injected real material update readback failure",
            ):
                direct_backend.execute(
                    target,
                    "hfss_material_update_apply",
                    {"preview_id": rollback_preview["preview_id"]},
                )
        rollback_after = direct_backend.execute(
            target,
            "hfss_material_inventory",
            {
                "project_name": "RealMaterialUpdateAcceptance",
                "design_name": "HFSS1",
                "max_items": 500,
            },
        )
        rollback_references_after = _hfss_material_target_snapshot(
            direct_app,
            ["MaterialBoxA", "MaterialBoxB"],
        )
        assert rollback_after["snapshot_digest"] == rollback_before["snapshot_digest"]
        assert rollback_references_after == rollback_references_before
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
                for name in ("MaterialBoxA", "MaterialBoxB"):
                    if name in hfss_app.modeler.object_names:
                        hfss_app.modeler.delete(name)
                for name in material_names:
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
