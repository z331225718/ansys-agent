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


def test_real_live_layout_material_create_assign_harness(tmp_path: Path, monkeypatch):
    from ansys.aedt.core import Hfss3dLayout
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
    layout_app = None
    session_id = ""
    direct_backend = None
    authority = HmacApprovalAuthority("real-layout-material-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    project_path = tmp_path / "RealLayoutMaterialAcceptance.aedt"
    material_names = {
        "HarnessLayoutLaminate",
        "HarnessLayoutFill",
        "HarnessLayoutCopper",
        "ExternalLayoutMaterial",
        "MustNotCreate",
        "RollbackLayoutLaminate",
    }
    try:
        launched, port = launch_aedt(
            executable,
            non_graphical=True,
            port=requested_port,
            student_version=False,
        )
        layout_app = Hfss3dLayout(
            project=str(project_path),
            design="Layout1",
            version=version,
            machine="localhost",
            port=port,
            new_desktop=False,
            close_on_exit=False,
        )
        assert layout_app.modeler.layers.add_layer(
            "D1",
            layer_type="dielectric",
            thickness="0.2mm",
            elevation="0.1mm",
            material="FR4_epoxy",
        )
        assert layout_app.modeler.layers.add_layer(
            "TOP",
            layer_type="signal",
            thickness="0.035mm",
            elevation="0.3mm",
            material="copper",
        )
        assert layout_app.save_project(str(project_path)) is True
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None
        baseline_stackup = _stackup(layout_app)

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]

        preview = manager.preview_layout_material_create_assign(
            session_id,
            project_name="RealLayoutMaterialAcceptance",
            design_name="Layout1",
            material_name="HarnessLayoutLaminate",
            layer_name="D1",
            assignment_field="material",
            permittivity=3.7,
            permeability=1.0,
            conductivity=0.001,
            dielectric_loss_tangent=0.012,
            magnetic_loss_tangent=0.0,
            appearance=[20, 30, 40, 0.2],
        )
        assert preview["project_dirty"] is False
        assert preview["project_saved"] is False
        assert preview["expected_material_class"] == "dielectric"
        assert _stackup(layout_app) == baseline_stackup
        assert _file_digest(project_path) == project_digest_before

        applied = manager.apply_layout_material_create_assign(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        assert applied["status"] == "verified"
        assert applied["created_material_name"] == "HarnessLayoutLaminate"
        assert applied["layer"]["name"] == "D1"
        assert applied["layer"]["material"] == "HarnessLayoutLaminate"
        assert applied["material"]["is_dielectric"] is True
        assert applied["material"]["appearance"] == [20, 30, 40, 0.2]
        assert applied["automatic_rollback_on_failure"] is True
        assert applied["project_saved"] is False
        assert _file_digest(project_path) == project_digest_before

        fill_preview = manager.preview_layout_material_create_assign(
            session_id,
            project_name="RealLayoutMaterialAcceptance",
            design_name="Layout1",
            material_name="HarnessLayoutFill",
            layer_name="TOP",
            assignment_field="fill_material",
            permittivity=3.2,
            conductivity=0.0,
            dielectric_loss_tangent=0.008,
        )
        fill = manager.apply_layout_material_create_assign(
            session_id,
            preview_id=fill_preview["preview_id"],
            approval_token=authority.issue(**fill_preview["approval_request"]),
        )
        assert fill["status"] == "verified"
        assert fill["expected_material_class"] == "dielectric"
        assert fill["layer"]["fill_material"] == "HarnessLayoutFill"

        conductor_preview = manager.preview_layout_material_create_assign(
            session_id,
            project_name="RealLayoutMaterialAcceptance",
            design_name="Layout1",
            material_name="HarnessLayoutCopper",
            layer_name="TOP",
            assignment_field="material",
            conductivity=58_000_000.0,
        )
        conductor = manager.apply_layout_material_create_assign(
            session_id,
            preview_id=conductor_preview["preview_id"],
            approval_token=authority.issue(**conductor_preview["approval_request"]),
        )
        assert conductor["status"] == "verified"
        assert conductor["expected_material_class"] == "conductor"
        assert conductor["material"]["is_dielectric"] is False
        assert conductor["layer"]["material"] == "HarnessLayoutCopper"
        assert _file_digest(project_path) == project_digest_before

        project_names = {
            str(item).casefold()
            for item in layout_app.materials.odefinition_manager.GetProjectMaterialNames()
        }
        library_only_name = next(
            name
            for name in layout_app.materials.mat_names_aedt
            if name.casefold() not in project_names
            and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_. +()-]{0,127}", name)
        )
        with pytest.raises(Exception, match="material library entry"):
            manager.preview_layout_material_create_assign(
                session_id,
                project_name="RealLayoutMaterialAcceptance",
                design_name="Layout1",
                material_name=library_only_name,
                layer_name="D1",
                assignment_field="material",
            )
        with pytest.raises(Exception, match="requires a dielectric material"):
            manager.preview_layout_material_create_assign(
                session_id,
                project_name="RealLayoutMaterialAcceptance",
                design_name="Layout1",
                material_name="MustNotCreate",
                layer_name="D1",
                assignment_field="material",
                conductivity=58_000_000.0,
            )

        stale_preview = manager.preview_layout_material_create_assign(
            session_id,
            project_name="RealLayoutMaterialAcceptance",
            design_name="Layout1",
            material_name="MustNotCreate",
            layer_name="D1",
            assignment_field="material",
            permittivity=2.8,
        )
        external = layout_app.materials.add_material(
            "ExternalLayoutMaterial",
            properties={"permittivity": 2.9},
        )
        assert external and external.name == "ExternalLayoutMaterial"
        with pytest.raises(
            Exception,
            match="stale 3D Layout material create-and-assign preview",
        ):
            manager.apply_layout_material_create_assign(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        assert "mustnotcreate" not in layout_app.materials.material_keys
        assert _file_digest(project_path) == project_digest_before

        _restore_stackup(layout_app, baseline_stackup)
        for name in (
            "HarnessLayoutLaminate",
            "HarnessLayoutFill",
            "HarnessLayoutCopper",
            "ExternalLayoutMaterial",
        ):
            _remove_project_material(layout_app, name)
        assert _stackup(layout_app) == baseline_stackup

        manager.release(session_id)
        session_id = ""
        from aedt_agent.live import backend as backend_module
        from aedt_agent.live.backend import LiveAedtBackend, LiveBackendError
        from aedt_agent.live.target import AedtTarget

        direct_backend = LiveAedtBackend(version=version)
        target = AedtTarget("port", port)
        rollback_preview = direct_backend.execute(
            target,
            "layout_material_create_assign_preview",
            {
                "project_name": "RealLayoutMaterialAcceptance",
                "design_name": "Layout1",
                "material_name": "RollbackLayoutLaminate",
                "layer_name": "D1",
                "assignment_field": "material",
                "permittivity": 4.1,
                "dielectric_loss_tangent": 0.016,
            },
        )
        rollback_state_before = dict(
            direct_backend._previews[rollback_preview["preview_id"]]["state"]
        )
        with monkeypatch.context() as patch:
            patch.setattr(
                backend_module,
                "_verify_layout_material_create_assign_readback",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    LiveBackendError("injected real layout material readback failure")
                ),
            )
            with pytest.raises(
                LiveBackendError,
                match="injected real layout material readback failure",
            ):
                direct_backend.execute(
                    target,
                    "layout_material_create_assign_apply",
                    {"preview_id": rollback_preview["preview_id"]},
                )
        retry_preview = direct_backend.execute(
            target,
            "layout_material_create_assign_preview",
            {
                "project_name": "RealLayoutMaterialAcceptance",
                "design_name": "Layout1",
                "material_name": "RollbackLayoutLaminate",
                "layer_name": "D1",
                "assignment_field": "material",
                "permittivity": 4.1,
                "dielectric_loss_tangent": 0.016,
            },
        )
        rollback_state_after = dict(
            direct_backend._previews[retry_preview["preview_id"]]["state"]
        )
        assert rollback_state_after == rollback_state_before
        assert "rollbacklayoutlaminate" not in {
            item["canonical_name"].casefold()
            for item in rollback_state_after["material_catalog"]["materials"]
        }
        assert rollback_state_after["stackup"] == rollback_state_before["stackup"]
        assert _file_digest(project_path) == project_digest_before
    finally:
        if session_id:
            try:
                manager.release(session_id)
            except Exception:
                pass
        if layout_app is not None:
            try:
                if "baseline_stackup" in locals():
                    _restore_stackup(layout_app, baseline_stackup)
            except Exception:
                pass
            for name in material_names:
                try:
                    _remove_project_material(layout_app, name)
                except Exception:
                    pass
        if direct_backend is not None:
            try:
                direct_backend.release()
            except Exception:
                pass
        manager.close()
        if layout_app is not None:
            try:
                layout_app.release_desktop(
                    close_projects=False,
                    close_desktop=False,
                )
            except Exception:
                pass
        if launched:
            _close_test_owned_aedt(port, launched_pid, version)


def _stackup(app) -> list[dict]:
    return [
        {
            "name": layer.name,
            "type": layer.type,
            "id": layer.id,
            "material": layer.material,
            "fill_material": layer.fill_material,
            "thickness": layer.thickness,
            "lower_elevation": layer.lower_elevation,
        }
        for layer in app.modeler.layers.stackup_layers
    ]


def _restore_stackup(app, baseline: list[dict]) -> None:
    _refresh_project_material_cache(app)
    expected = {item["name"]: item for item in baseline}
    for name in ("TOP", "D1"):
        _refresh_project_material_cache(app)
        layers = {layer.name: layer for layer in app.modeler.layers.stackup_layers}
        layer = layers[name]
        if layer.material != expected[name]["material"]:
            layer.material = expected[name]["material"]
        layers = {item.name: item for item in app.modeler.layers.stackup_layers}
        layer = layers[name]
        if layer.fill_material != expected[name]["fill_material"]:
            layer.fill_material = expected[name]["fill_material"]


def _refresh_project_material_cache(app) -> None:
    for name in app.materials.odefinition_manager.GetProjectMaterialNames():
        if str(name).casefold() not in app.materials.material_keys:
            assert app.materials._aedmattolibrary(str(name))


def _remove_project_material(app, name: str) -> None:
    project_names = {
        str(item).casefold()
        for item in app.materials.odefinition_manager.GetProjectMaterialNames()
    }
    if name.casefold() not in project_names:
        return
    if name.casefold() not in app.materials.material_keys:
        assert app.materials._aedmattolibrary(name)
    assert app.materials.remove_material(name) is True


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
