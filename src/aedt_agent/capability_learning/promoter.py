from __future__ import annotations

from dataclasses import dataclass
import difflib
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping
from uuid import uuid4

import yaml

from aedt_agent.capability_learning.classifier import ClassificationDecision, classify_trace
from aedt_agent.capability_learning.trace_store import CapabilityTraceStore, TraceStateError


class PromotionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PromotionResult:
    candidate_id: str
    trace_id: str
    kind: str
    state: str
    candidate_dir: str
    files: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "trace_id": self.trace_id,
            "kind": self.kind,
            "state": self.state,
            "candidate_dir": self.candidate_dir,
            "files": list(self.files),
        }


class CapabilityPromoter:
    """Generate review-only artifacts from a server-owned verified trace."""

    def __init__(
        self,
        trace_store: CapabilityTraceStore | None = None,
        candidate_root: str | Path | None = None,
    ) -> None:
        self.trace_store = trace_store or CapabilityTraceStore()
        configured = (
            candidate_root
            or os.environ.get("AEDT_AGENT_CANDIDATE_ROOT")
            or Path.cwd() / ".aedt-agent" / "capability-candidates"
        )
        self.candidate_root = Path(configured).resolve()
        if tuple(part.lower() for part in self.candidate_root.parts[-2:]) != (
            ".aedt-agent",
            "capability-candidates",
        ):
            raise ValueError("candidate_root must end with .aedt-agent/capability-candidates")

    def promote(self, trace_id: str, *, target_kind: str = "auto") -> PromotionResult:
        trace = self._load_verified_trace(trace_id)
        decision = classify_trace(trace, target_kind)
        candidate_id = f"candidate-{trace['seal_digest'][:16]}-{decision.kind}"
        candidate_dir = (self.candidate_root / candidate_id).resolve()
        self._ensure_candidate_path(candidate_dir)

        if candidate_dir.is_dir():
            return self._existing_result(candidate_dir, trace_id, trace["seal_digest"], decision.kind)

        hardcoding = _hardcoding_assessment(trace)
        candidate = _candidate_record(candidate_id, trace, decision, hardcoding)
        generated = _render_generated(candidate_id, trace, decision)
        report = _render_report(candidate, hardcoding)
        patch = _render_patch(candidate_id, decision.kind, generated)

        content_to_audit = [
            json.dumps(candidate, ensure_ascii=True, sort_keys=True),
            report,
            patch,
            *generated.values(),
        ]
        _assert_no_trace_literal_leak(trace, content_to_audit)

        self.candidate_root.mkdir(parents=True, exist_ok=True)
        temporary = self.candidate_root / f".{candidate_id}-{uuid4().hex}.tmp"
        try:
            temporary.mkdir(parents=False, exist_ok=False)
            generated_dir = temporary / "generated"
            generated_dir.mkdir()
            _write_json(temporary / "candidate.json", candidate)
            (temporary / "promotion-report.md").write_text(report, encoding="utf-8", newline="\n")
            (temporary / "candidate.patch").write_text(patch, encoding="utf-8", newline="\n")
            for relative_path, content in generated.items():
                destination = generated_dir / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(content, encoding="utf-8", newline="\n")
            manifest = _build_manifest(temporary, candidate)
            _write_json(temporary / "manifest.json", manifest)
            temporary.replace(candidate_dir)
        except Exception:
            if temporary.is_dir():
                self._ensure_candidate_path(temporary)
                shutil.rmtree(temporary)
            raise

        return self._existing_result(candidate_dir, trace_id, trace["seal_digest"], decision.kind)

    def _load_verified_trace(self, trace_id: str) -> dict[str, Any]:
        try:
            trace = self.trace_store.export(trace_id)
        except TraceStateError as exc:
            raise PromotionError("trace_tampered", "capability trace seal verification failed") from exc
        except (KeyError, ValueError) as exc:
            raise PromotionError("trace_not_found", "capability trace is not owned by the configured trace store") from exc
        if trace.get("sealed") is not True or trace.get("state") != "verified":
            raise PromotionError("trace_not_verified", "promotion requires a sealed trace in verified state")
        events = trace.get("events")
        if not isinstance(events, list) or not events or events[-1].get("state") != "verified":
            raise PromotionError("trace_invalid", "verified trace has an invalid terminal event")
        seal_digest = trace.get("seal_digest")
        if not isinstance(seal_digest, str) or len(seal_digest) != 64:
            raise PromotionError("trace_invalid", "verified trace is missing its seal digest")
        authentication = trace.get("authentication")
        seal_hmac = trace.get("seal_hmac")
        if (
            not isinstance(authentication, Mapping)
            or authentication.get("scheme") != "hmac-sha256"
            or not isinstance(seal_hmac, str)
            or len(seal_hmac) != 64
        ):
            raise PromotionError("trace_invalid", "verified trace is missing server authentication")
        expected = _digest(
            {key: value for key, value in trace.items() if key not in {"seal_digest", "seal_hmac"}}
        )
        if not hmac.compare_digest(seal_digest, expected):
            raise PromotionError("trace_tampered", "verified trace seal digest does not match its contents")
        return trace

    def _existing_result(
        self,
        candidate_dir: Path,
        trace_id: str,
        seal_digest: str,
        kind: str,
    ) -> PromotionResult:
        try:
            manifest = json.loads((candidate_dir / "manifest.json").read_text(encoding="utf-8"))
            self._verify_candidate_manifest(candidate_dir, manifest)
            candidate = json.loads((candidate_dir / "candidate.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PromotionError("candidate_conflict", "candidate directory exists but is not a valid candidate") from exc
        source = candidate.get("source_trace", {})
        if (
            source.get("trace_id") != trace_id
            or source.get("seal_digest") != seal_digest
            or candidate.get("kind") != kind
        ):
            raise PromotionError("candidate_conflict", "candidate directory belongs to a different promotion")
        files = tuple(sorted(item["path"] for item in manifest.get("files", [])))
        return PromotionResult(
            candidate["candidate_id"],
            trace_id,
            kind,
            candidate["state"],
            str(candidate_dir),
            files,
        )

    def _verify_candidate_manifest(self, candidate_dir: Path, manifest: Mapping[str, Any]) -> None:
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("candidate manifest has no files")
        seen: set[str] = set()
        for item in files:
            if not isinstance(item, Mapping) or set(item) != {"path", "sha256", "size"}:
                raise ValueError("candidate manifest file entry is invalid")
            relative = item["path"]
            if not isinstance(relative, str) or relative in seen:
                raise ValueError("candidate manifest file path is invalid")
            seen.add(relative)
            path = (candidate_dir / relative).resolve()
            try:
                path.relative_to(candidate_dir)
            except ValueError as exc:
                raise ValueError("candidate manifest path escaped candidate directory") from exc
            if not path.is_file():
                raise ValueError("candidate manifest file is missing")
            content = path.read_bytes()
            if item["size"] != len(content) or item["sha256"] != hashlib.sha256(content).hexdigest():
                raise ValueError("candidate manifest file digest does not match")

    def _ensure_candidate_path(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.candidate_root)
        except ValueError as exc:
            raise PromotionError("candidate_path_invalid", "candidate path escaped the candidate root") from exc


def _candidate_record(
    candidate_id: str,
    trace: Mapping[str, Any],
    decision: ClassificationDecision,
    hardcoding: list[dict[str, str]],
) -> dict[str, Any]:
    plan = trace.get("plan") if isinstance(trace.get("plan"), Mapping) else {}
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    evidence = plan.get("evidence") if isinstance(plan.get("evidence"), list) else []
    operation_shapes = sorted(
        {
            str(step.get("op"))
            for step in steps
            if isinstance(step, Mapping) and step.get("op") in {"read_attr", "call", "set_attr"}
        }
    )
    packages = sorted(
        {
            str(item.get("package"))
            for item in evidence
            if isinstance(item, Mapping) and item.get("package") in {"pyaedt", "pyedb"}
        }
    )
    return {
        "schema_version": "ansys-capability-candidate/v1",
        "candidate_id": candidate_id,
        "state": "candidate",
        "kind": decision.kind,
        "classification": decision.to_dict(),
        "source_trace": {
            "trace_id": trace["trace_id"],
            "seal_digest": trace["seal_digest"],
            "state": "verified",
        },
        "contract_summary": {
            "risk": plan.get("risk", "unknown"),
            "operation_shapes": operation_shapes,
            "step_count": len(steps),
            "readback_count": len(plan.get("readback", [])) if isinstance(plan.get("readback"), list) else 0,
            "evidence_count": len(evidence),
            "evidence_packages": packages,
            "target_template": {
                "product": plan.get("target", {}).get("product", "$request.product")
                if isinstance(plan.get("target"), Mapping)
                else "$request.product",
                "project_name": "$request.project_name",
                "design_name": "$request.design_name",
                "object_path": "$request.object_path",
                "operation_parameters": "$request.operation_parameters",
            },
            **_sanitized_operation_contract(plan),
        },
        "hardcoding_audit": {
            "checked": True,
            "raw_target_values_copied": False,
            "findings": hardcoding,
        },
        "activation": {
            "auto_apply": False,
            "hot_registration": False,
            "requires_human_review": True,
            "requires_tests_before_approval": True,
        },
    }


def _sanitized_operation_contract(plan: Mapping[str, Any]) -> dict[str, Any]:
    operations = []
    raw_steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    for step in raw_steps:
        if not isinstance(step, Mapping):
            continue
        step_id = str(step.get("id") or "operation")
        operations.append(
            {
                "id": step_id,
                "op": str(step.get("op") or "unknown"),
                "member": str(step.get("path") or "").rsplit(".", 1)[-1],
                "object_path": "$request.object_path",
                "parameters": f"$request.operation_parameters.{step_id}",
            }
        )
    readback = []
    raw_readback = plan.get("readback") if isinstance(plan.get("readback"), list) else []
    for check in raw_readback:
        if not isinstance(check, Mapping):
            continue
        check_id = str(check.get("id") or "readback")
        readback.append(
            {
                "id": check_id,
                "member": str(check.get("path") or "").rsplit(".", 1)[-1],
                "operator": str(check.get("operator") or "unknown"),
                "expected": f"$request.operation_parameters.{check_id}.expected",
            }
        )
    evidence = []
    raw_evidence = plan.get("evidence") if isinstance(plan.get("evidence"), list) else []
    for item in raw_evidence:
        if not isinstance(item, Mapping):
            continue
        evidence.append(
            {
                key: str(item[key])
                for key in (
                    "package",
                    "package_version",
                    "symbol",
                    "snippet_digest",
                    "query_id",
                )
                if item.get(key) is not None
            }
        )
    return {
        "operations": operations,
        "readback_contracts": readback,
        "api_evidence": evidence,
    }


def _hardcoding_assessment(trace: Mapping[str, Any]) -> list[dict[str, str]]:
    plan = trace.get("plan") if isinstance(trace.get("plan"), Mapping) else {}
    findings: list[dict[str, str]] = []
    target = plan.get("target") if isinstance(plan.get("target"), Mapping) else {}
    for field in ("project_name", "design_name"):
        if target.get(field):
            findings.append(
                {
                    "location": f"plan.target.{field}",
                    "kind": "target_identity",
                    "resolution": "replaced_by_request_parameter",
                }
            )
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        if step.get("path"):
            findings.append(
                {
                    "location": f"plan.steps[{index}].path",
                    "kind": "object_reference",
                    "resolution": "replaced_by_request_parameter",
                }
            )
        if any(key in step for key in ("args", "kwargs", "value")):
            findings.append(
                {
                    "location": f"plan.steps[{index}].operation_parameters",
                    "kind": "operation_value",
                    "resolution": "replaced_by_request_parameter",
                }
            )
    evidence = plan.get("evidence") if isinstance(plan.get("evidence"), list) else []
    for index, item in enumerate(evidence):
        if isinstance(item, Mapping) and item.get("source_path"):
            findings.append(
                {
                    "location": f"plan.evidence[{index}].source_path",
                    "kind": "local_source_path",
                    "resolution": "omitted_from_candidate",
                }
            )
    return findings


def _render_generated(
    candidate_id: str,
    trace: Mapping[str, Any],
    decision: ClassificationDecision,
) -> dict[str, str]:
    plan = trace.get("plan") if isinstance(trace.get("plan"), Mapping) else {}
    risk = plan.get("risk") if plan.get("risk") in {"read_only", "reversible_edit"} else "unknown"
    if decision.kind == "harness":
        return _render_harness(candidate_id, risk, _sanitized_operation_contract(plan))
    if decision.kind == "skill":
        return _render_skill(candidate_id)
    return _render_workflow(candidate_id)


def _render_harness(candidate_id: str, risk: str, operation_contract: Mapping[str, Any]) -> dict[str, str]:
    module_name = candidate_id.replace("-", "_")
    review_contract = json.dumps(
        {**operation_contract, "implementation": "typed_runtime_api_required"},
        ensure_ascii=True,
        sort_keys=True,
    )
    capability = f'''from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CANDIDATE_ID = {candidate_id!r}
STATUS = "candidate"
RISK = {risk!r}
REVIEW_CONTRACT = {review_contract}


class CandidateNotRegistered(RuntimeError):
    pass


@dataclass(frozen=True)
class CapabilityRequest:
    product: str
    project_name: str
    design_name: str
    object_path: str
    operation_parameters: dict[str, Any]


def validate_request(request: CapabilityRequest) -> None:
    for field in ("product", "project_name", "design_name", "object_path"):
        if not getattr(request, field).strip():
            raise ValueError(f"{{field}} must be a non-empty string")


def execute_candidate(request: CapabilityRequest) -> None:
    validate_request(request)
    raise CandidateNotRegistered(
        "review, implement with typed runtime APIs, test, and register in a later release"
    )
'''
    test = f'''import pytest

from aedt_agent.interactive.promoted_candidates.{module_name} import (
    CandidateNotRegistered,
    CapabilityRequest,
    execute_candidate,
)


def test_candidate_cannot_execute_before_registration():
    request = CapabilityRequest(
        product="hfss3dlayout",
        project_name="fixture_project",
        design_name="fixture_design",
        object_path="fixture.object.property",
        operation_parameters={{}},
    )
    with pytest.raises(CandidateNotRegistered):
        execute_candidate(request)
'''
    return {"capability.py": capability, "test_capability.py": test}


def _render_skill(candidate_id: str) -> dict[str, str]:
    skill_name = f"ansys-{candidate_id}"
    skill = f'''---
name: {skill_name}
description: Candidate Ansys engineering method derived from a verified exploration trace. Use only after a reviewer maps every action to registered Harness tools and accepts the candidate.
---

# Ansys Capability Candidate

1. Resolve project, design, object path, and operation values from explicit user input.
2. Use registered Harness tools for every AEDT read or mutation.
3. Preserve preview, approval, readback, and rollback gates from the verified trace.
4. Stop when a required Harness capability is unavailable. Never substitute raw Python, shell, or COM execution.
5. Treat this file as a review candidate until tests pass and a human promotes it in a later release.
'''
    test = f'''from pathlib import Path


def test_promoted_skill_keeps_candidate_safety_rules():
    text = Path(".agents/skills/{skill_name}/SKILL.md").read_text(encoding="utf-8")
    assert "registered Harness tools" in text
    assert "Never substitute raw Python" in text
'''
    return {"SKILL.md": skill, "test_skill_contract.py": test}


def _render_workflow(candidate_id: str) -> dict[str, str]:
    workflow = {
        "schema_version": "ansys-workflow-candidate/v1",
        "template_id": candidate_id,
        "status": "candidate",
        "max_rounds": 1,
        "inputs": ["product", "project_name", "design_name", "object_path", "operation_parameters"],
        "nodes": [
            {"id": "validate", "kind": "harness", "capability": "TO_BE_REVIEWED"},
            {"id": "preview", "kind": "approval_gate", "capability": "TO_BE_REVIEWED"},
            {"id": "apply", "kind": "harness", "capability": "TO_BE_REVIEWED"},
            {"id": "verify", "kind": "harness", "capability": "TO_BE_REVIEWED"},
        ],
        "edges": [
            {"from": "validate", "to": "preview", "on": "validated"},
            {"from": "preview", "to": "apply", "on": "approved"},
            {"from": "apply", "to": "verify", "on": "applied"},
        ],
        "activation": {"enabled": False, "requires_human_review": True},
    }
    workflow_text = yaml.safe_dump(workflow, sort_keys=False, allow_unicode=False)
    test = f'''from pathlib import Path

import yaml


def test_workflow_candidate_is_disabled_until_reviewed():
    value = yaml.safe_load(Path("workflow_templates/{candidate_id}.yaml").read_text(encoding="utf-8"))
    assert value["status"] == "candidate"
    assert value["activation"]["enabled"] is False
'''
    return {"workflow.yaml": workflow_text, "test_workflow_contract.py": test}


def _render_patch(candidate_id: str, kind: str, generated: Mapping[str, str]) -> str:
    module_name = candidate_id.replace("-", "_")
    if kind == "harness":
        destinations = {
            "capability.py": f"src/aedt_agent/interactive/promoted_candidates/{module_name}.py",
            "test_capability.py": f"tests/test_{module_name}.py",
        }
    elif kind == "skill":
        skill_name = f"ansys-{candidate_id}"
        destinations = {
            "SKILL.md": f".agents/skills/{skill_name}/SKILL.md",
            "test_skill_contract.py": f"tests/test_{module_name}_skill.py",
        }
    else:
        destinations = {
            "workflow.yaml": f"workflow_templates/{candidate_id}.yaml",
            "test_workflow_contract.py": f"tests/test_{module_name}_workflow.py",
        }
    chunks = [
        _new_file_diff(destination, generated[source])
        for source, destination in destinations.items()
    ]
    return "".join(chunks)


def _new_file_diff(destination: str, content: str) -> str:
    return "".join(
        difflib.unified_diff(
            [],
            content.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=f"b/{destination}",
        )
    )


def _render_report(candidate: Mapping[str, Any], hardcoding: list[dict[str, str]]) -> str:
    classification = candidate["classification"]
    lines = [
        "# Capability Promotion Report",
        "",
        f"- Candidate: `{candidate['candidate_id']}`",
        f"- Source trace: `{candidate['source_trace']['trace_id']}`",
        f"- Trace seal: `{candidate['source_trace']['seal_digest']}`",
        f"- Proposed kind: `{candidate['kind']}`",
        f"- Classification confidence: `{classification['confidence']}`",
        "- Activation: disabled; human review is required",
        "",
        "## Classification",
        "",
    ]
    lines.extend(f"- `{reason}`" for reason in classification["reasons"])
    lines.extend(["", "## Parameterization Audit", ""])
    if hardcoding:
        lines.extend(
            f"- `{item['location']}`: {item['kind']} -> {item['resolution']}"
            for item in hardcoding
        )
    else:
        lines.append("- No target literals were copied into the candidate.")
    lines.extend(
        [
            "",
            "## Required Review Gates",
            "",
            "- Implement only through typed Harness APIs.",
            "- Add negative tests for ambiguity, stale preview, failure, and rollback.",
            "- Repeat validation on representative fixtures and supported AEDT versions.",
            "- Approve and register only in a later release; never hot-register this candidate.",
            "",
        ]
    )
    return "\n".join(lines)


def _build_manifest(directory: Path, candidate: Mapping[str, Any]) -> dict[str, Any]:
    files = []
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        relative = path.relative_to(directory).as_posix()
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
        )
    return {
        "schema_version": "ansys-capability-candidate-manifest/v1",
        "candidate_id": candidate["candidate_id"],
        "state": "candidate",
        "files": files,
    }


def _assert_no_trace_literal_leak(trace: Mapping[str, Any], contents: list[str]) -> None:
    forbidden: list[tuple[str, str]] = []
    plan = trace.get("plan") if isinstance(trace.get("plan"), Mapping) else {}
    target = plan.get("target") if isinstance(plan.get("target"), Mapping) else {}
    for field in ("project_name", "design_name"):
        value = target.get(field)
        if isinstance(value, str) and len(value) >= 6:
            forbidden.append((f"target.{field}", value))
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    for index, step in enumerate(steps):
        if isinstance(step, Mapping):
            value = step.get("path")
            if isinstance(value, str) and len(value) >= 6:
                forbidden.append((f"steps[{index}].path", value))
    evidence = plan.get("evidence") if isinstance(plan.get("evidence"), list) else []
    for index, item in enumerate(evidence):
        if isinstance(item, Mapping):
            value = item.get("source_path")
            if isinstance(value, str) and len(value) >= 6:
                forbidden.append((f"evidence[{index}].source_path", value))
    joined = "\n".join(contents)
    for location, value in forbidden:
        if value in joined:
            raise PromotionError(
                "hardcoded_target_detected",
                f"candidate rendering copied a trace-specific literal from {location}",
            )
    if re.search(r"(?:sk-|Bearer\s+)[A-Za-z0-9._-]{8,}", joined, re.I):
        raise PromotionError("secret_detected", "candidate rendering contains a credential-like value")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8", newline="\n")


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
