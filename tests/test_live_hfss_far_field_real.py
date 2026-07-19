from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import socket

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LIVE_AEDT") != "1",
    reason="real live AEDT acceptance is opt-in",
)


def test_real_live_hfss_infinite_sphere_harness(tmp_path: Path):
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
    authority = HmacApprovalAuthority("real-hfss-far-field-secret-32-bytes")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    hfss_app = None
    launched_pid = None
    session_id = ""
    project_path = tmp_path / "RealFarFieldAcceptance.aedt"
    sphere_names = {
        "HarnessThetaPhi",
        "HarnessElOverAz",
        "HarnessAzOverEl",
        "HarnessRadians",
        "ExternalSphere",
        "MustNotCreate",
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
        metal = hfss_app.modeler.create_box(
            ["0mm", "0mm", "0mm"],
            ["10mm", "2mm", "0.5mm"],
            name="FarFieldMetal",
            material="copper",
        )
        region = hfss_app.modeler.create_region(
            [10, 10, 10, 10, 10, 10],
            name="FarFieldRegion",
        )
        radiation = hfss_app.assign_radiation_boundary_to_objects(
            region.name,
            name="FarFieldRadiation",
        )
        assert all((metal, region, radiation))
        assert hfss_app.save_project(str(project_path)) is True
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        inventory_before = manager.hfss_far_field_inventory(
            session_id,
            project_name="RealFarFieldAcceptance",
            design_name="HFSS1",
        )
        specs = [
            {
                "sphere_name": "HarnessThetaPhi",
                "definition": "Theta-Phi",
                "angle1_start": -90,
                "angle1_stop": 90,
                "angle1_step": 5,
                "angle2_start": 0,
                "angle2_stop": 360,
                "angle2_step": 10,
                "polarization": "Slant",
                "polarization_angle": 45,
                "units": "deg",
            },
            {
                "sphere_name": "HarnessElOverAz",
                "definition": "El Over Az",
                "angle1_start": -180,
                "angle1_stop": 180,
                "angle1_step": 10,
                "angle2_start": -90,
                "angle2_stop": 90,
                "angle2_step": 5,
                "polarization": "Linear",
                "units": "deg",
            },
            {
                "sphere_name": "HarnessAzOverEl",
                "definition": "Az Over El",
                "angle1_start": -90,
                "angle1_stop": 90,
                "angle1_step": 5,
                "angle2_start": -180,
                "angle2_stop": 180,
                "angle2_step": 10,
                "polarization": "Linear",
                "units": "deg",
            },
            {
                "sphere_name": "HarnessRadians",
                "definition": "Theta-Phi",
                "angle1_start": -math.pi / 2,
                "angle1_stop": math.pi / 2,
                "angle1_step": math.pi / 18,
                "angle2_start": 0,
                "angle2_stop": 2 * math.pi,
                "angle2_step": math.pi / 18,
                "polarization": "Slant",
                "polarization_angle": math.pi / 4,
                "units": "rad",
            },
        ]
        results = []
        for spec in specs:
            preview = manager.preview_hfss_infinite_sphere_create(
                session_id,
                project_name="RealFarFieldAcceptance",
                design_name="HFSS1",
                max_samples=5000,
                **spec,
            )
            assert preview["project_dirty"] is False
            assert preview["project_saved"] is False
            applied = manager.apply_hfss_infinite_sphere_create(
                session_id,
                preview_id=preview["preview_id"],
                approval_token=authority.issue(**preview["approval_request"]),
            )
            results.append(applied)

        stale_preview = manager.preview_hfss_infinite_sphere_create(
            session_id,
            project_name="RealFarFieldAcceptance",
            design_name="HFSS1",
            sphere_name="MustNotCreate",
            definition="Theta-Phi",
            angle1_start=0,
            angle1_stop=180,
            angle1_step=30,
            angle2_start=0,
            angle2_stop=360,
            angle2_step=30,
            max_samples=1000,
        )
        external = hfss_app.insert_infinite_sphere(
            definition="Theta-Phi",
            theta_start=0,
            theta_stop=180,
            theta_step=30,
            phi_start=0,
            phi_stop=360,
            phi_step=30,
            units="deg",
            name="ExternalSphere",
        )
        assert external
        with pytest.raises(Exception, match="stale HFSS infinite sphere create preview"):
            manager.apply_hfss_infinite_sphere_create(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        with pytest.raises(Exception, match="field setup already exists"):
            manager.preview_hfss_infinite_sphere_create(
                session_id,
                project_name="RealFarFieldAcceptance",
                design_name="HFSS1",
                sphere_name="ExternalSphere",
            )

        inventory_after = manager.hfss_far_field_inventory(
            session_id,
            project_name="RealFarFieldAcceptance",
            design_name="HFSS1",
        )
        assert launched is True
        assert inventory_before["field_setup_count"] == 0
        assert inventory_before["creation_ready"] is True
        assert [
            (item["name"], item["type"])
            for item in inventory_before["radiated_field_sources"]
        ] == [("FarFieldRadiation", "Radiation")]
        assert [item["status"] for item in results] == ["verified"] * 4
        assert [item["field_setup"]["definition"] for item in results] == [
            "Theta-Phi",
            "El Over Az",
            "Az Over El",
            "Theta-Phi",
        ]
        assert [item["field_setup"]["angle1_axis"] for item in results] == [
            "Theta",
            "Azimuth",
            "Elevation",
            "Theta",
        ]
        assert [item["field_setup"]["angle2_axis"] for item in results] == [
            "Phi",
            "Elevation",
            "Azimuth",
            "Phi",
        ]
        assert all(item["automatic_rollback_on_failure"] is True for item in results)
        assert all(item["project_saved"] is False for item in results)
        assert inventory_after["field_setup_count"] == 5
        assert "MustNotCreate" not in {
            item["name"] for item in inventory_after["field_setups"]
        }
        assert _file_digest(project_path) == project_digest_before
    finally:
        if hfss_app is not None:
            try:
                for setup in list(hfss_app.field_setups or []):
                    if setup.name in sphere_names:
                        setup.delete()
            except Exception:
                pass
            try:
                hfss_app.modeler.delete(["FarFieldRegion", "FarFieldMetal"])
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
