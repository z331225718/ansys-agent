from __future__ import annotations

import json
from json import JSONDecoder

from aedt_agent.mcp.types import NodePlan


class NodePlanParseError(ValueError):
    pass


def extract_node_plan(text: str) -> NodePlan:
    candidates = _json_object_candidates(text)
    for candidate in reversed(candidates):
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict) and "plan" in payload:
                return NodePlan.from_dict(payload)
        except Exception:
            continue
    raise NodePlanParseError("No valid node plan JSON object found")


def _json_object_candidates(text: str) -> list[str]:
    decoder = JSONDecoder()
    candidates: list[str] = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            _obj, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        candidates.append(text[index : index + end])
    return candidates
