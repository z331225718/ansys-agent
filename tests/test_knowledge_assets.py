from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml


HIGH_VALUE_APIS = {
    "Hfss.modeler.create_box",
    "Hfss.modeler.create_rectangle",
    "Hfss.lumped_port",
    "Hfss.wave_port",
    "Hfss.create_setup",
    "Hfss.create_linear_count_sweep",
    "Hfss.assign_radiation_boundary_to_objects",
    "Hfss3dLayout.oeditor.ToggleViaPin",
}


def test_high_value_api_semantics_are_not_empty():
    records = {}
    for line in Path("knowledge/api_semantics/api_semantics.seed.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            records[item["fqname"]] = item

    missing = HIGH_VALUE_APIS - set(records)
    assert not missing

    for fqname in HIGH_VALUE_APIS:
        item = records[fqname]
        assert json.loads(item["params_json"]), fqname
        assert json.loads(item["constraints_json"]), fqname
        assert json.loads(item["common_errors_json"]), fqname
        assert json.loads(item["source_refs_json"]), fqname


def test_required_common_traps_exist():
    required = {
        "waveport_no_background_contact",
        "airbox_too_small",
        "missing_ground_plane",
        "wrong_face_selected_for_port",
        "sweep_range_misses_target_frequency",
        "material_or_unit_mismatch",
        "boundary_assigned_to_wrong_object",
    }
    found = {path.stem for path in Path("knowledge/common_traps").glob("*.yaml")}

    assert required <= found


def test_common_traps_have_detection_rules():
    for path in Path("knowledge/common_traps").glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["trap_id"] == path.stem
        assert data.get("description") or data.get("symptom")
        assert data.get("detection_rule") or data.get("validation_rule")
        assert data.get("avoidance") or data.get("prevention")


def test_default_sqlite_database_matches_seed_assets():
    db_path = Path("knowledge/api_semantics/api_semantics.sqlite")
    assert db_path.exists()

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("select count(*) from api_semantics").fetchone()[0]
        create_box = conn.execute(
            "select constraints_json, common_errors_json from api_semantics where fqname = ?",
            ("Hfss.modeler.create_box",),
        ).fetchone()
        toggle = conn.execute(
            "select fqname from api_semantics where fqname = ?",
            ("Hfss3dLayout.oeditor.ToggleViaPin",),
        ).fetchone()

    assert count >= 75
    assert create_box is not None
    assert "sizes must be positive" in create_box[0]
    assert "negative or zero size creates invalid geometry" in create_box[1]
    assert toggle is not None
