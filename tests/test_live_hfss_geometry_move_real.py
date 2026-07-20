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


def test_real_live_hfss_geometry_move_harness(tmp_path: Path, monkeypatch):
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

    authority = HmacApprovalAuthority("real-hfss-geometry-move-secret-32")
    manager = LiveAedtSessionManager(approval_verifier=authority)
    hfss_app = None
    direct_backend = None
    launched = False
    launched_pid = None
    session_id = ""
    project_path = tmp_path / "RealGeometryMoveAcceptance.aedt"
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
        box = hfss_app.modeler.create_box(
            ["0mm", "0mm", "0mm"],
            ["2mm", "3mm", "4mm"],
            name="HarnessMoveBox",
            material="copper",
        )
        sheet = hfss_app.modeler.create_rectangle(
            "XY",
            ["10mm", "0mm", "0mm"],
            ["2mm", "3mm"],
            name="HarnessMoveSheet",
        )
        fixed = hfss_app.modeler.create_box(
            ["20mm", "0mm", "0mm"],
            ["1mm", "1mm", "1mm"],
            name="HarnessFixedBox",
            material="vacuum",
        )
        rollback_box = hfss_app.modeler.create_box(
            ["30mm", "0mm", "0mm"],
            ["2mm", "2mm", "2mm"],
            name="HarnessRollbackBox",
            material="vacuum",
        )
        rollback_sheet = hfss_app.modeler.create_rectangle(
            "YZ",
            ["35mm", "0mm", "0mm"],
            ["2mm", "2mm"],
            name="HarnessRollbackSheet",
        )
        assert all((box, sheet, fixed, rollback_box, rollback_sheet))
        boundary = hfss_app.assign_perfect_e(["HarnessMoveSheet"], name="HarnessMovePEC")
        mesh = hfss_app.mesh.assign_length_mesh(
            ["HarnessMoveBox"],
            inside_selection=True,
            maximum_length="0.5mm",
            maximum_elements=500,
            name="HarnessMoveMesh",
        )
        assert boundary and mesh
        assert hfss_app.save_project(str(project_path)) is True
        project_digest_before = _file_digest(project_path)
        assert project_digest_before is not None

        opened = manager.attach(port=port, version=version)
        launched_pid = opened["probe"]["pid"]
        session_id = opened["live_session_id"]
        before = {
            name: _object_identity(hfss_app.modeler[name])
            for name in ("HarnessMoveBox", "HarnessMoveSheet", "HarnessFixedBox")
        }
        moves = [
            {"name": "HarnessMoveBox", "vector": [1.25, -2.5, 3.75]},
            {"name": "HarnessMoveSheet", "vector": [-4.0, 5.0, 0.25]},
        ]
        preview = manager.preview_hfss_geometry_move(
            session_id,
            project_name="RealGeometryMoveAcceptance",
            design_name="HFSS1",
            moves=moves,
            max_objects=2,
        )
        assert preview["target_count"] == 2
        assert preview["model_units"] == "mm"
        assert preview["coordinate_system"] == "Global"
        assert preview["boundary_count"] >= 1
        assert preview["mesh_operation_count"] >= 1
        assert preview["project_dirty"] is False
        assert _file_digest(project_path) == project_digest_before

        applied = manager.apply_hfss_geometry_move(
            session_id,
            preview_id=preview["preview_id"],
            approval_token=authority.issue(**preview["approval_request"]),
        )
        assert applied["status"] == "verified"
        assert applied["moved_object_names"] == ["HarnessMoveBox", "HarnessMoveSheet"]
        assert applied["moved_object_count"] == 2
        assert applied["boundaries_preserved"] is True
        assert applied["mesh_operations_preserved"] is True
        assert applied["active_coordinate_system_preserved"] is True
        assert applied["automatic_rollback_on_failure"] is True
        assert applied["project_saved"] is False
        for move in moves:
            name = move["name"]
            after = _object_identity(hfss_app.modeler[name])
            assert after["object_id"] == before[name]["object_id"]
            assert after["face_ids"] == before[name]["face_ids"]
            assert after["material_name"] == before[name]["material_name"]
            assert after["solve_inside"] == before[name]["solve_inside"]
            _assert_bbox_translated(before[name]["bounding_box"], after["bounding_box"], move["vector"])
        assert _object_identity(hfss_app.modeler["HarnessFixedBox"]) == before[
            "HarnessFixedBox"
        ]
        assert _file_digest(project_path) == project_digest_before

        stale = manager.preview_hfss_geometry_move(
            session_id,
            project_name="RealGeometryMoveAcceptance",
            design_name="HFSS1",
            moves=[{"name": "HarnessFixedBox", "vector": [0, 1, 0]}],
        )
        assert hfss_app.modeler.move(["HarnessFixedBox"], [0, 0.5, 0]) is True
        with pytest.raises(Exception, match="stale HFSS geometry move preview"):
            manager.apply_hfss_geometry_move(
                session_id,
                preview_id=stale["preview_id"],
                approval_token=authority.issue(**stale["approval_request"]),
            )
        assert hfss_app.modeler.move(["HarnessFixedBox"], [0, -0.5, 0]) is True
        assert _file_digest(project_path) == project_digest_before

        manager.release(session_id)
        session_id = ""
        from aedt_agent.live import backend as backend_module
        from aedt_agent.live.backend import LiveAedtBackend, LiveBackendError
        from aedt_agent.live.target import AedtTarget

        direct_backend = LiveAedtBackend(version=version)
        direct_target = AedtTarget("port", port)
        rollback_moves = [
            {"name": "HarnessRollbackBox", "vector": [2.5, 1.25, -0.5]},
            {"name": "HarnessRollbackSheet", "vector": [-1.5, 0.75, 3.0]},
        ]
        rollback_preview = direct_backend.execute(
            direct_target,
            "hfss_geometry_move_preview",
            {
                "project_name": "RealGeometryMoveAcceptance",
                "design_name": "HFSS1",
                "moves": rollback_moves,
            },
        )
        rollback_inventory_before = direct_backend.execute(
            direct_target,
            "hfss_geometry_inventory",
            {
                "project_name": "RealGeometryMoveAcceptance",
                "design_name": "HFSS1",
                "object_names": ["HarnessRollbackBox", "HarnessRollbackSheet"],
            },
        )
        with monkeypatch.context() as patch:
            patch.setattr(
                backend_module,
                "_verify_hfss_geometry_move_state",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    LiveBackendError("injected real geometry move readback failure")
                ),
            )
            with pytest.raises(
                LiveBackendError,
                match="injected real geometry move readback failure",
            ):
                direct_backend.execute(
                    direct_target,
                    "hfss_geometry_move_apply",
                    {"preview_id": rollback_preview["preview_id"]},
                )
        rollback_after = direct_backend.execute(
            direct_target,
            "hfss_geometry_move_preview",
            {
                "project_name": "RealGeometryMoveAcceptance",
                "design_name": "HFSS1",
                "moves": rollback_moves,
            },
        )
        assert rollback_after["snapshot_digest"] == rollback_preview["snapshot_digest"]
        rollback_inventory_after = direct_backend.execute(
            direct_target,
            "hfss_geometry_inventory",
            {
                "project_name": "RealGeometryMoveAcceptance",
                "design_name": "HFSS1",
                "object_names": ["HarnessRollbackBox", "HarnessRollbackSheet"],
            },
        )
        _assert_inventory_identity_equal(
            rollback_inventory_after["objects"],
            rollback_inventory_before["objects"],
        )
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
        if hfss_app is not None:
            try:
                hfss_app.release_desktop(close_projects=False, close_desktop=False)
            except Exception:
                pass
        if launched:
            _close_test_owned_aedt(port, launched_pid, version)


def _object_identity(obj) -> dict:
    return {
        "object_id": int(obj.id),
        "face_ids": sorted(int(face.id) for face in obj.faces),
        "material_name": str(obj.material_name or ""),
        "solve_inside": obj.solve_inside,
        "bounding_box": [float(item) for item in obj.bounding_box],
    }


def _assert_bbox_translated(before: list[float], after: list[float], vector: list[float]) -> None:
    expected = [
        float(value) + float(vector[index % 3])
        for index, value in enumerate(before)
    ]
    assert len(after) == len(expected) == 6
    assert all(
        math.isclose(actual, wanted, rel_tol=1e-10, abs_tol=1e-10)
        for actual, wanted in zip(after, expected)
    )


def _assert_inventory_identity_equal(actual_records: list[dict], expected_records: list[dict]) -> None:
    actual = {item["name"]: item for item in actual_records}
    expected = {item["name"]: item for item in expected_records}
    assert set(actual) == set(expected)
    for name, before in expected.items():
        after = actual[name]
        assert after["object_id"] == before["object_id"]
        assert after["material_name"] == before["material_name"]
        assert after["solve_inside"] == before["solve_inside"]
        assert [item["face_id"] for item in after["faces"]] == [
            item["face_id"] for item in before["faces"]
        ]
        assert all(
            math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-10)
            for a, b in zip(after["bounding_box"], before["bounding_box"])
        )


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
