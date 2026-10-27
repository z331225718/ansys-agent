
r"""P0 N1: what happens when two drivers advance the SAME task database.

Expected (and observed): sqlite3.IntegrityError on the node_runs
(graph_run_id, sequence) unique constraint, because the graph runner has no
cross-thread lock for one database. This is why the task service must hold one
driver per task and write an advisory lock file.

Run: .venv\Scripts\python.exe scripts\spikes\p0_samedb.py
"""
from __future__ import annotations

import sys
import threading
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from aedt_agent.agent.graph_runner import advance_graph, create_graph_run
from aedt_agent.agent.graph_template import graph_template_from_mapping
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.infrastructure import SQLiteMissionStore

TEMPLATE = {
    "id": "p0_probe",
    "version": 1,
    "nodes": [
        {"id": "source", "role": "planner", "kind": "program"},
        {"id": "validator", "role": "validator", "kind": "program"},
    ],
    "edges": [{"id": "s-v", "from": "source", "to": "validator", "on": "succeeded"}],
    "handoffs": {},
}


def main() -> int:
    root = REPO / "tmp" / "p0-samedb"
    root.mkdir(parents=True, exist_ok=True)
    for leftover in root.glob("missions.db*"):
        leftover.unlink(missing_ok=True)
    db = root / "missions.db"

    store = SQLiteMissionStore(db)
    runtime = AgentRuntime(store)
    mission = runtime.create_mission("same-db", [], [])
    run = create_graph_run(runtime, mission.mission_id, graph_template_from_mapping(TEMPLATE))

    outcomes: list[tuple] = []
    barrier = threading.Barrier(2)

    def worker(tag: str) -> None:
        try:
            barrier.wait(timeout=10)
            report = advance_graph(runtime, run.graph_run_id)
            outcomes.append((tag, "ok", report["status"]))
        except BaseException as exc:  # noqa: BLE001 - the exception IS the finding
            outcomes.append((tag, type(exc).__name__, str(exc)[:200],
                             traceback.format_exc().splitlines()[-1]))

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    for outcome in outcomes:
        print(outcome)

    node_runs = len(store.list_node_runs(run.graph_run_id))
    print("\nnode_runs:", node_runs)
    conflicted = any(item[1] != "ok" for item in outcomes)
    print("\nSAME-DB CONFLICT OBSERVED:", conflicted)
    print("=> the task service must serialize one driver per task database")
    return 0 if conflicted or node_runs >= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
