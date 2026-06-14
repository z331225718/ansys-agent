from __future__ import annotations

from aedt_agent.agent.evaluation import build_sparameter_evidence, query_sparameter_window


def _dense_samples():
    samples = []
    for index in range(1341):
        frequency = round(index * 0.05, 2)
        s11 = -22.0
        if frequency == 18.0:
            s11 = -7.0
        samples.append({"frequency_ghz": frequency, "s11_db": s11, "s21_db": -1.0})
    return samples


def test_build_sparameter_evidence_keeps_raw_trace_as_artifact_only():
    evidence = build_sparameter_evidence(
        trace_id="run-1:S11",
        samples=_dense_samples(),
        artifact_ref="artifacts/channel.s2p",
        rl_target_db=-20.0,
        bucket_count=64,
    )

    assert evidence["raw_trace_policy"] == "artifact_only"
    assert evidence["artifact_refs"] == ["artifacts/channel.s2p"]
    assert evidence["summary"]["sample_count"] == 1341
    assert evidence["summary"]["frequency_start_ghz"] == 0.0
    assert evidence["summary"]["frequency_stop_ghz"] == 67.0
    assert evidence["summary"]["rl_worst_db"] == -7.0
    assert evidence["summary"]["rl_worst_frequency_ghz"] == 18.0
    assert len(evidence["summary"]["buckets"]) <= 64
    assert "1341" in str(evidence["summary"])
    assert "0.0,0.05,0.1" not in str(evidence["summary"])


def test_extrema_preserving_buckets_keep_narrowband_failure():
    evidence = build_sparameter_evidence(
        trace_id="run-1:S11",
        samples=_dense_samples(),
        artifact_ref="artifacts/channel.s2p",
        rl_target_db=-20.0,
        bucket_count=32,
    )

    bucket = next(item for item in evidence["summary"]["buckets"] if item["frequency_start_ghz"] <= 18.0 <= item["frequency_stop_ghz"])

    assert bucket["max_db"] == -7.0
    assert bucket["max_frequency_ghz"] == 18.0
    assert bucket["threshold_crossings"] >= 1
    assert evidence["summary"]["failure_windows"] == [{"start_ghz": 18.0, "stop_ghz": 18.0, "worst_db": -7.0}]


def test_query_sparameter_window_limits_points_and_preserves_extrema():
    result = query_sparameter_window(
        trace_id="run-1:S11",
        samples=_dense_samples(),
        frequency_start_ghz=17.0,
        frequency_stop_ghz=19.0,
        max_points=8,
        rl_target_db=-20.0,
    )

    assert result["trace_id"] == "run-1:S11"
    assert result["point_count"] <= 8
    assert result["window_summary"]["sample_count"] == 41
    assert result["window_summary"]["rl_worst_db"] == -7.0
    assert any(point["frequency_ghz"] == 18.0 and point["s11_db"] == -7.0 for point in result["points"])
