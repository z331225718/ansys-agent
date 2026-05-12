from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml

from aedt_agent.knowledge.models import ApiSemantic, CommonTrap, WorkflowCase


class SQLiteKnowledgeProvider:
    def __init__(
        self,
        db_path: Path,
        workflow_cases_dir: Path | None = None,
        common_traps_dir: Path | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.workflow_cases_dir = Path(workflow_cases_dir or "knowledge/workflow_cases")
        self.common_traps_dir = Path(common_traps_dir or "knowledge/common_traps")

    def search_api(self, query: str, limit: int = 10) -> list[ApiSemantic]:
        if not self.db_path.exists():
            return []
        sql = """
        SELECT
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
        FROM api_semantics
        WHERE fqname LIKE ? OR category LIKE ? OR signature LIKE ? OR docstring LIKE ?
        ORDER BY fqname
        LIMIT ?
        """
        needle = f"%{query}%"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(sql, (needle, needle, needle, needle, limit)).fetchall()
        return [self._api_semantic_from_row(row) for row in rows]

    def list_workflow_cases(self) -> list[WorkflowCase]:
        return [
            WorkflowCase.from_dict(self._load_yaml(path))
            for path in sorted(self.workflow_cases_dir.glob("*.yaml"))
        ]

    def list_common_traps(self, filter_ids: list[str] | None = None) -> list[CommonTrap]:
        traps = [
            CommonTrap.from_dict(self._load_yaml(path))
            for path in sorted(self.common_traps_dir.glob("*.yaml"))
        ]
        if not filter_ids:
            return traps
        allowed = set(filter_ids)
        return [trap for trap in traps if trap.trap_id in allowed]

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise TypeError(f"{path} must contain a mapping")
        return data

    @staticmethod
    def _api_semantic_from_row(row: tuple) -> ApiSemantic:
        return ApiSemantic.from_dict(
            {
                "fqname": row[0],
                "domain": row[1],
                "category": row[2],
                "signature": row[3],
                "params": json.loads(row[4]),
                "returns": json.loads(row[5]),
                "docstring": row[6],
                "constraints": json.loads(row[7]),
                "common_errors": json.loads(row[8]),
                "common_traps": json.loads(row[9]),
                "examples_ref": json.loads(row[10]),
                "source_refs": json.loads(row[11]),
                "confidence": row[12],
                "pyaedt_version": row[13],
                "aedt_version": row[14],
                "last_verified_at": row[15],
            }
        )
