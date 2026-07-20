from __future__ import annotations

import json

import pytest

from aedt_agent.capability_learning.trace_store import CapabilityTraceStore, TraceStateError
from aedt_agent.capability_learning.trace_store import _digest


def test_trace_store_appends_states_seals_and_redacts_secrets(tmp_path):
    store = CapabilityTraceStore(tmp_path / "traces")
    created = store.create(
        candidate_id="candidate-1",
        intent="change one width",
        plan={"approval_token": "must-not-persist", "steps": [{"path": "line.width"}]},
        environment={"api_key": "sk-secret-value", "aedt_version": "2026.1"},
    )
    trace_id = created["trace_id"]
    store.transition(trace_id, "validated", "plan_validated", {"secret": "hidden"})
    store.transition(trace_id, "previewed", "preview_created", {"preview_id": "p1"})
    store.transition(trace_id, "approved", "host_approved", {"approval_token": "one-use"})
    store.transition(trace_id, "applied", "operation_applied", {"after": "trace_w"})
    sealed = store.transition(trace_id, "verified", "readback_verified", {"verified": True})

    assert sealed["sealed"] is True
    assert sealed["state"] == "verified"
    assert len(sealed["seal_digest"]) == 64
    assert len(sealed["seal_hmac"]) == 64
    assert sealed["authentication"]["scheme"] == "hmac-sha256"
    serialized = json.dumps(sealed)
    assert "must-not-persist" not in serialized
    assert "one-use" not in serialized
    assert "sk-secret-value" not in serialized
    assert "[REDACTED]" in serialized
    assert store.list()["traces"][0]["trace_id"] == trace_id
    assert store.export(trace_id)["seal_digest"] == sealed["seal_digest"]


def test_trace_store_rejects_invalid_transition_and_sealed_mutation(tmp_path):
    store = CapabilityTraceStore(tmp_path / "traces")
    trace_id = store.create(candidate_id="c", intent="x", plan={}, environment={})["trace_id"]
    with pytest.raises(TraceStateError):
        store.transition(trace_id, "previewed", "skip_validation")
    store.transition(trace_id, "failed", "validation_failed")
    with pytest.raises(TraceStateError):
        store.transition(trace_id, "validated", "cannot_reopen")


def test_trace_store_detects_event_log_tampering(tmp_path):
    store = CapabilityTraceStore(tmp_path / "traces")
    trace_id = store.create(candidate_id="c", intent="x", plan={}, environment={})["trace_id"]
    store.transition(trace_id, "failed", "validation_failed")
    event_log = store.root / trace_id / "events.jsonl"
    event_log.write_text(event_log.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")

    with pytest.raises(TraceStateError, match="event log digest"):
        store.export(trace_id)


def test_trace_store_rejects_recomputed_plain_digest_without_server_key(tmp_path):
    store = CapabilityTraceStore(tmp_path / "traces")
    trace_id = store.create(candidate_id="c", intent="x", plan={}, environment={})["trace_id"]
    store.transition(trace_id, "failed", "validation_failed")
    trace_path = store.root / trace_id / "trace.json"
    manifest_path = store.root / trace_id / "manifest.json"
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    payload["intent"] = "forged"
    payload["seal_digest"] = _digest(
        {key: value for key, value in payload.items() if key not in {"seal_digest", "seal_hmac"}}
    )
    trace_path.write_text(json.dumps(payload), encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["seal_digest"] = payload["seal_digest"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(TraceStateError, match="server authentication"):
        store.export(trace_id)


def test_approved_trace_can_expire_without_apply(tmp_path):
    store = CapabilityTraceStore(tmp_path / "traces")
    trace_id = store.create(candidate_id="c", intent="x", plan={}, environment={})["trace_id"]
    store.transition(
        trace_id,
        "validated",
        "plan_validated",
        {"message": "request failed with Bearer top-secret-token-value"},
    )
    store.transition(trace_id, "previewed", "preview_created")
    store.transition(trace_id, "approved", "host_approved")
    expired = store.transition(trace_id, "expired", "approval_expired")

    assert expired["state"] == "expired"
    assert expired["sealed"] is True
    assert "top-secret-token-value" not in json.dumps(expired)


def test_trace_store_recovers_seal_after_interrupted_final_write(tmp_path):
    store = CapabilityTraceStore(tmp_path / "traces")
    trace_id = store.create(candidate_id="c", intent="x", plan={}, environment={})["trace_id"]
    store.transition(trace_id, "failed", "validation_failed")
    (store.root / trace_id / "trace.json").unlink()

    recovered = store.export(trace_id)
    assert recovered["state"] == "failed"
    assert recovered["sealed"] is True
    assert len(recovered["seal_digest"]) == 64
