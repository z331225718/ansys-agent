from __future__ import annotations

from pathlib import Path

from aedt_agent.agent.actions.contracts import ActionRecord
from aedt_agent.agent.actions.validation import validate_action


class RecordedActionAdapter:
    def apply(self, action: ActionRecord) -> dict:
        validate_action(action)
        paths = {
            key: Path(str(action.adapter_input[key]))
            for key in ("before_touchstone", "before_tdr", "after_touchstone", "after_tdr")
        }
        for key, path in paths.items():
            if not path.exists():
                raise ValueError(f"{key} does not exist: {path}")
        return {
            "adapter_mode": "recorded",
            "applied": True,
            "real_project_modified": False,
            "before_artifact_refs": [str(paths["before_touchstone"]), str(paths["before_tdr"])],
            "after_artifact_refs": [str(paths["after_touchstone"]), str(paths["after_tdr"])],
        }


class RealAedtActionAdapter:
    def apply(self, action: ActionRecord) -> dict:
        validate_action(action)
        raise RuntimeError("real_aedt action adapter is not enabled")
