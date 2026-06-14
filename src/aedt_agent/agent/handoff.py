from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class HandoffValidationError(ValueError):
    """Raised when a graph node handoff payload does not match its schema."""


@dataclass(frozen=True)
class HandoffSchema:
    schema_id: str
    required_fields: list[str]

    def to_json_dict(self) -> dict[str, Any]:
        return {"id": self.schema_id, "required_fields": list(self.required_fields)}


def validate_handoff(schema: HandoffSchema, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    missing = [field for field in schema.required_fields if field not in payload]
    if missing:
        raise HandoffValidationError(f"handoff {schema.schema_id} missing required fields: {missing}")
    return payload
