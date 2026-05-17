from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from aedt_agent.evolution.models import NodeEvolutionEvidence


def mine_stage_b_report(path: Path) -> list[NodeEvolutionEvidence]:
    data = _read_json(path)
    tasks = data.get("tasks", {})
    if not isinstance(tasks, dict):
        return []
    failures: dict[str, list[str]] = defaultdict(list)
    repeated_repairs: dict[str, list[str]] = defaultdict(list)
    subgraphs: Counter[tuple[str, ...]] = Counter()

    for task_id, task_data in tasks.items():
        if not isinstance(task_data, dict):
            continue
        for group_name, group_data in task_data.items():
            if group_name == "metadata" or not isinstance(group_data, dict):
                continue
            if group_data.get("final_pass") is False:
                failure_type = str(group_data.get("failure_type") or "unknown_failure")
                failures[failure_type].append(str(task_id))
            attempts = group_data.get("attempts", [])
            if isinstance(attempts, list) and len(attempts) > 1:
                repeated_repairs[f"{group_name}_multi_attempt"].append(str(task_id))
            steps = group_data.get("node_steps", [])
            node_ids = tuple(step.get("node_id", "") for step in steps if isinstance(step, dict) and step.get("node_id"))
            if len(node_ids) >= 2:
                subgraphs[node_ids] += 1

    evidence = []
    for failure_type, task_ids in sorted(failures.items()):
        evidence.append(
            NodeEvolutionEvidence(
                source=str(path),
                kind="failure_pattern",
                summary=failure_type,
                count=len(task_ids),
                tasks=sorted(set(task_ids)),
            )
        )
    for pattern, task_ids in sorted(repeated_repairs.items()):
        evidence.append(
            NodeEvolutionEvidence(
                source=str(path),
                kind="repeated_repair",
                summary=pattern,
                count=len(task_ids),
                tasks=sorted(set(task_ids)),
            )
        )
    for node_ids, count in subgraphs.most_common():
        evidence.append(
            NodeEvolutionEvidence(
                source=str(path),
                kind="node_subgraph",
                summary=" -> ".join(node_ids),
                count=count,
                node_ids=list(node_ids),
            )
        )
    return evidence


def mine_audit_jsonl(path: Path) -> list[NodeEvolutionEvidence]:
    failures: dict[str, list[str]] = defaultdict(list)
    node_counts: Counter[str] = Counter()
    session_sequences: dict[str, list[str]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            node_id = str(event.get("node_id") or "")
            session_id = str(event.get("session_id") or "")
            if node_id:
                node_counts[node_id] += 1
                session_sequences[session_id].append(node_id)
            result = event.get("result", {})
            if isinstance(result, dict) and result.get("status") not in {"succeeded", None}:
                failures[str(result.get("error_type") or "unknown_failure")].append(node_id)

    evidence = []
    for node_id, count in node_counts.most_common():
        evidence.append(NodeEvolutionEvidence(source=str(path), kind="node_usage", summary=node_id, count=count, node_ids=[node_id]))
    for error_type, node_ids in sorted(failures.items()):
        evidence.append(NodeEvolutionEvidence(source=str(path), kind="audit_failure", summary=error_type, count=len(node_ids), node_ids=sorted(set(node_ids))))
    sequence_counts = Counter(tuple(sequence) for sequence in session_sequences.values() if len(sequence) >= 2)
    for sequence, count in sequence_counts.most_common():
        evidence.append(NodeEvolutionEvidence(source=str(path), kind="node_subgraph", summary=" -> ".join(sequence), count=count, node_ids=list(sequence)))
    return evidence


def mine_evolution_evidence(paths: list[Path]) -> list[NodeEvolutionEvidence]:
    evidence: list[NodeEvolutionEvidence] = []
    for path in paths:
        if path.suffix == ".jsonl":
            evidence.extend(mine_audit_jsonl(path))
        elif path.suffix == ".json":
            evidence.extend(mine_stage_b_report(path))
    return evidence


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return data
