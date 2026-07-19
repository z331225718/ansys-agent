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


def test_real_live_hfss_surface_boundary_harness(tmp_path: Path):
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
    authority = HmacApprovalAuthority("real-hfss-surface-boundary-secret-32")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    hfss_app = None
    launched_pid = None
    session_id = ""
    project_path = tmp_path / "RealSurfaceBoundaryAcceptance.aedt"
    fixture_names = [
        "PerfectEObject",
        "PerfectHObject",
        "FiniteObject",
        "ImpedanceSheet",
        "RlcSheet",
        "ExternalSheet",
    ]
    boundary_names = {
        "HarnessPerfectE",
        "HarnessPerfectH",
        "HarnessFinite",
        "HarnessImpedance",
        "HarnessRLC",
        "ExternalPerfectE",
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
        perfect_e_object = hfss_app.modeler.create_rectangle(
            "XY",
            ["0mm", "0mm", "0mm"],
            ["4mm", "4mm"],
            name="PerfectEObject",
        )
        perfect_h_object = hfss_app.modeler.create_box(
            ["6mm", "0mm", "0mm"],
            ["4mm", "4mm", "1mm"],
            name="PerfectHObject",
            material="vacuum",
        )
        finite_object = hfss_app.modeler.create_box(
            ["12mm", "0mm", "0mm"],
            ["4mm", "4mm", "1mm"],
            name="FiniteObject",
            material="vacuum",
        )
        impedance_sheet = hfss_app.modeler.create_rectangle(
            "XY",
            ["0mm", "6mm", "0mm"],
            ["4mm", "4mm"],
            name="ImpedanceSheet",
        )
        rlc_sheet = hfss_app.modeler.create_rectangle(
            "XY",
            ["12mm", "6mm", "0mm"],
            ["4mm", "1mm"],
            name="RlcSheet",
        )
        external_sheet = hfss_app.modeler.create_rectangle(
            "XY",
            ["6mm", "6mm", "0mm"],
            ["4mm", "4mm"],
            name="ExternalSheet",
        )
        assert all(
            (
                perfect_e_object,
                perfect_h_object,
                finite_object,
                impedance_sheet,
                rlc_sheet,
                external_sheet,
            )
        )
        assert hfss_app.materials.exists_material("copper")
        perfect_h_face = int(perfect_h_object.faces[0].id)
        finite_face = int(finite_object.faces[0].id)
        assert hfss_app.save_project(str(project_path)) is True
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        inventory_before = manager.hfss_surface_boundary_inventory(
            session_id,
            project_name="RealSurfaceBoundaryAcceptance",
            design_name="HFSS1",
        )
        requests = [
            {
                "boundary_kind": "perfect_e",
                "boundary_name": "HarnessPerfectE",
                "object_names": ["PerfectEObject"],
                "options": {"is_infinite_ground": True},
            },
            {
                "boundary_kind": "perfect_h",
                "boundary_name": "HarnessPerfectH",
                "face_ids": [perfect_h_face],
            },
            {
                "boundary_kind": "finite_conductivity",
                "boundary_name": "HarnessFinite",
                "face_ids": [finite_face],
                "options": {
                    "material_name": "copper",
                    "use_thickness": True,
                    "thickness": "35um",
                    "roughness": "0.5um",
                    "is_infinite_ground": False,
                    "is_two_sided": False,
                    "is_internal": True,
                },
            },
            {
                "boundary_kind": "lumped_rlc",
                "boundary_name": "HarnessRLC",
                "object_names": ["RlcSheet"],
                "options": {
                    "rlc_type": "Serial",
                    "integration_line_direction": "XPos",
                    "resistance": 50,
                    "inductance": 1e-9,
                    "capacitance": 2e-12,
                },
            },
            {
                "boundary_kind": "impedance",
                "boundary_name": "HarnessImpedance",
                "object_names": ["ImpedanceSheet"],
                "options": {
                    "resistance": 75,
                    "reactance": -10,
                    "is_infinite_ground": False,
                },
            },
        ]
        results = []
        for request in requests:
            preview = manager.preview_hfss_surface_boundary_create(
                session_id,
                project_name="RealSurfaceBoundaryAcceptance",
                design_name="HFSS1",
                max_assignments=4,
                **request,
            )
            assert preview["project_dirty"] is False
            assert preview["project_saved"] is False
            applied = manager.apply_hfss_surface_boundary_create(
                session_id,
                preview_id=preview["preview_id"],
                approval_token=authority.issue(**preview["approval_request"]),
            )
            results.append(applied)

        stale_preview = manager.preview_hfss_surface_boundary_create(
            session_id,
            project_name="RealSurfaceBoundaryAcceptance",
            design_name="HFSS1",
            boundary_kind="perfect_h",
            boundary_name="MustNotCreate",
            object_names=["ExternalSheet"],
        )
        external = hfss_app.assign_perfect_e(
            "ExternalSheet",
            name="ExternalPerfectE",
        )
        assert external
        with pytest.raises(Exception, match="stale HFSS surface boundary create preview"):
            manager.apply_hfss_surface_boundary_create(
                session_id,
                preview_id=stale_preview["preview_id"],
                approval_token=authority.issue(**stale_preview["approval_request"]),
            )
        with pytest.raises(Exception, match="boundary already exists"):
            manager.preview_hfss_surface_boundary_create(
                session_id,
                project_name="RealSurfaceBoundaryAcceptance",
                design_name="HFSS1",
                boundary_kind="perfect_e",
                boundary_name="ExternalPerfectE",
                object_names=["ExternalSheet"],
            )
        with pytest.raises(Exception, match="requires sheet objects"):
            manager.preview_hfss_surface_boundary_create(
                session_id,
                project_name="RealSurfaceBoundaryAcceptance",
                design_name="HFSS1",
                boundary_kind="impedance",
                boundary_name="InvalidSolidImpedance",
                object_names=["PerfectHObject"],
            )
        with pytest.raises(Exception, match="requires planar sheet objects"):
            manager.preview_hfss_surface_boundary_create(
                session_id,
                project_name="RealSurfaceBoundaryAcceptance",
                design_name="HFSS1",
                boundary_kind="perfect_e",
                boundary_name="InvalidSolidInfiniteGround",
                object_names=["PerfectHObject"],
                options={"is_infinite_ground": True},
            )
        with pytest.raises(Exception, match="requires sheet objects"):
            manager.preview_hfss_surface_boundary_create(
                session_id,
                project_name="RealSurfaceBoundaryAcceptance",
                design_name="HFSS1",
                boundary_kind="lumped_rlc",
                boundary_name="InvalidSolidRLC",
                object_names=["PerfectHObject"],
                options={"resistance": 50},
            )
        with pytest.raises(Exception, match="requires at least one positive"):
            manager.preview_hfss_surface_boundary_create(
                session_id,
                project_name="RealSurfaceBoundaryAcceptance",
                design_name="HFSS1",
                boundary_kind="lumped_rlc",
                boundary_name="InvalidEmptyRLC",
                object_names=["RlcSheet"],
                options={},
            )

        inventory_after = manager.hfss_surface_boundary_inventory(
            session_id,
            project_name="RealSurfaceBoundaryAcceptance",
            design_name="HFSS1",
        )
        assert launched is True
        assert inventory_before["boundary_count"] == 0
        assert inventory_before["supported_surface_boundary_count"] == 0
        assert [item["status"] for item in results] == ["verified"] * 5
        assert [item["boundary"]["kind"] for item in results] == [
            "perfect_e",
            "perfect_h",
            "finite_conductivity",
            "lumped_rlc",
            "impedance",
        ]
        assert results[0]["boundary"]["object_names"] == ["PerfectEObject"]
        assert results[0]["boundary"]["options"]["is_infinite_ground"] is True
        assert results[1]["boundary"]["face_ids"] == [perfect_h_face]
        assert results[2]["boundary"]["face_ids"] == [finite_face]
        assert results[2]["boundary"]["options"]["material_name"] == "copper"
        assert results[2]["boundary"]["options"]["thickness"] == "35um"
        assert results[2]["boundary"]["options"]["roughness"] == "0.5um"
        assert results[3]["boundary"]["object_names"] == ["RlcSheet"]
        assert results[3]["boundary"]["options"]["rlc_type"] == "Serial"
        assert results[3]["boundary"]["options"]["integration_line"] == {
            "start": ["16.0mm", "6.5mm", "0.0mm"],
            "end": ["12.0mm", "6.5mm", "0.0mm"],
        }
        assert results[3]["boundary"]["options"]["resistance"] == "50ohm"
        assert results[3]["boundary"]["options"]["inductance"] == "1e-09H"
        assert results[3]["boundary"]["options"]["capacitance"].casefold() == "2e-12f"
        assert float(results[4]["boundary"]["options"]["resistance"]) == 75.0
        assert float(results[4]["boundary"]["options"]["reactance"]) == -10.0
        assert all(item["automatic_rollback_on_failure"] is True for item in results)
        assert all(item["project_saved"] is False for item in results)
        assert inventory_after["boundary_count"] == 6
        assert inventory_after["supported_surface_boundary_count"] == 6
        assert "MustNotCreate" not in {
            item["name"] for item in inventory_after["boundaries"]
        }
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
