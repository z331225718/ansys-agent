from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aedt_agent.agent.mission import JobAttemptRecord
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore
from aedt_agent.infrastructure.harness import HarnessRequest, HarnessWorkspacePolicy


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "aedt_agent.agent.cli",
            "--db",
            str(tmp_path / "mission.db"),
            *args,
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_recover_harness_reports_interrupted_attempt(tmp_path):
    store = SQLiteMissionStore(tmp_path / "mission.db")
    runtime = AgentRuntime(store)
    mission = runtime.create_mission("goal", [], [])
    job = runtime.create_job(
        mission.mission_id,
        "fake.echo",
        "echo:1",
        {},
        retry_limit=1,
    )
    store.acquire_job_lease(job.job_id, "worker-1", lease_seconds=60)
    attempt = store.create_job_attempt(
        JobAttemptRecord.create(
            "attempt-1",
            mission.mission_id,
            job.job_id,
            1,
            "worker-1",
        )
    )
    workspace = HarnessWorkspacePolicy(tmp_path / "harness").create_attempt(
        mission.mission_id,
        job.job_id,
        attempt.attempt_id,
    )
    request = HarnessRequest.create(
        harness_run_id="run-1",
        mission_id=mission.mission_id,
        job_id=job.job_id,
        attempt_id=attempt.attempt_id,
        worker_id="worker-1",
        capability="fake.echo",
        entrypoint="tests.fixtures.process_workers:echo_worker",
        timeout_seconds=30,
        heartbeat_interval_seconds=1,
        input_payload={},
        workspace=str(workspace.root),
    )
    workspace.request_path.write_text(
        json.dumps(request.to_json_dict()),
        encoding="utf-8",
    )
    workspace.heartbeat_path.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "harness_run_id": "run-1",
                "job_id": job.job_id,
                "pid": 999999,
                "updated_at": (datetime.now(UTC) - timedelta(minutes=2)).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        tmp_path,
        "mission",
        "recover-harness",
        "--mission-id",
        mission.mission_id,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["interrupted_attempt_ids"] == [attempt.attempt_id]
    assert payload["requeued_job_ids"] == [job.job_id]
