from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aedt_agent.agent import loop_runner


class FakeRuntime:
    def __init__(self) -> None:
        self.created_goals: list[str] = []

    def create_mission(self, goal, criteria, constraints):
        self.created_goals.append(goal)
        return SimpleNamespace(mission_id="mission-1")


def test_load_loop_config_defaults_and_rejects_tight_polling(tmp_path):
    config = tmp_path / "loop.json"
    config.write_text(json.dumps({"goal": "run"}), encoding="utf-8")

    loaded = loop_runner.load_loop_config(config)

    assert loaded["template_id"] == "brd_reviewed_model_optimize_loop"
    assert loaded["poll_interval_seconds"] == 30

    config.write_text(
        json.dumps({"goal": "run", "poll_interval_seconds": 1}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="poll_interval_seconds"):
        loop_runner.load_loop_config(config)


def test_run_loop_from_config_creates_graph_and_advances_until_terminal(monkeypatch):
    runtime = FakeRuntime()
    calls: list[tuple[str, str]] = []
    reports = [
        {"status": "running", "graph_run": {"step_count": 1}},
        {"status": "succeeded", "graph_run": {"step_count": 2}},
    ]

    monkeypatch.setattr(loop_runner, "load_graph_template", lambda value: "template")
    monkeypatch.setattr(
        loop_runner,
        "create_graph_run",
        lambda runtime, mission_id, template, **kwargs: SimpleNamespace(
            graph_run_id="graph-1",
            mission_id=mission_id,
        ),
    )

    def fake_advance(runtime, graph_run_id, *, worker_id, max_workers):
        calls.append((graph_run_id, worker_id))
        return reports.pop(0)

    monkeypatch.setattr(loop_runner, "advance_graph", fake_advance)

    report = loop_runner.run_loop_from_config(
        runtime,
        {
            "goal": "reviewed loop",
            "template_id": "brd_reviewed_model_optimize_loop",
            "poll_interval_seconds": 30,
        },
        worker_id="claude-code",
        max_workers=1,
    )

    assert runtime.created_goals == ["reviewed loop"]
    assert calls == [("graph-1", "claude-code"), ("graph-1", "claude-code")]
    assert report["status"] == "succeeded"
    assert report["mission_id"] == "mission-1"
    assert report["graph_run_id"] == "graph-1"
    assert report["poll_interval_seconds"] == 30


def test_run_loop_from_config_can_resume_existing_graph(monkeypatch):
    runtime = FakeRuntime()
    created = []
    monkeypatch.setattr(
        loop_runner,
        "graph_status",
        lambda runtime, graph_run_id: {
            "status": "running",
            "graph_run": {
                "graph_run_id": graph_run_id,
                "mission_id": "mission-existing",
            },
        },
    )
    monkeypatch.setattr(
        loop_runner,
        "create_graph_run",
        lambda *args, **kwargs: created.append(args),
    )
    monkeypatch.setattr(
        loop_runner,
        "advance_graph",
        lambda runtime, graph_run_id, **kwargs: {
            "status": "waiting_approval",
            "graph_run": {"graph_run_id": graph_run_id},
        },
    )

    report = loop_runner.run_loop_from_config(
        runtime,
        {"graph_run_id": "graph-existing", "poll_interval_seconds": 30},
    )

    assert runtime.created_goals == []
    assert created == []
    assert report["status"] == "waiting_approval"
    assert report["mission_id"] == "mission-existing"
    assert report["graph_run_id"] == "graph-existing"
