from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def build_api_semantics_db(schema_path: Path, seed_path: Path, db_path: Path) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        with seed_path.open(encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                conn.execute(
                    """
                    INSERT INTO api_semantics (
                        fqname,
                        domain,
                        category,
                        signature,
                        params_json,
                        returns_json,
                        docstring,
                        constraints_json,
                        common_errors_json,
                        common_traps_json,
                        examples_ref_json,
                        source_refs_json,
                        confidence,
                        pyaedt_version,
                        aedt_version,
                        last_verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record["fqname"],
                        record.get("domain", "hfss"),
                        record["category"],
                        record.get("signature", ""),
                        record.get("params_json", "[]"),
                        record.get("returns_json", "{}"),
                        record.get("docstring", ""),
                        record.get("constraints_json", "[]"),
                        record.get("common_errors_json", "[]"),
                        record.get("common_traps_json", "[]"),
                        record.get("examples_ref_json", "[]"),
                        record.get("source_refs_json", "[]"),
                        record.get("confidence", "inferred"),
                        record.get("pyaedt_version", ""),
                        record.get("aedt_version", ""),
                        record.get("last_verified_at", ""),
                    ),
                )
        conn.commit()
    return db_path
