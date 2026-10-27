
"""P0 spike Q3: multiple AgentRuntime/DB instances in one process."""
from __future__ import annotations

import json
import sys
import threading
import time
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
    "edges": [{"id": "source-validator", "from": "source", "to": "validator", "on": "succeeded"}],
    "handoffs": {},
}

def build_runtime(db: Path) -> AgentRuntime:
    store = SQLiteMissionStore(db)
    return AgentRuntime(store)

def make_task(root: Path, name: str) -> dict:
    task_dir = root / name
    task_dir.mkdir(parents=True, exist_ok=True)
    db = task_dir / "missions.db"
    runtime = build_runtime(db)
    mission = runtime.create_mission(f"{name} goal", [], [])
    template = graph_template_from_mapping(TEMPLATE)
    run = create_graph_run(runtime, mission.mission_id, template, initial_payload={"value": 1})
    return {"name": name, "dir": task_dir, "db": db, "runtime": runtime,
            "mission_id": mission.mission_id, "graph_run_id": run.graph_run_id}

def dump(db: Path) -> dict:
    import sqlite3
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        missions = [r["mission_id"] for r in con.execute("SELECT mission_id FROM missions")]
        graphs = [r["graph_run_id"] for r in con.execute("SELECT graph_run_id FROM graph_runs")]
        nodes = con.execute("SELECT COUNT(*) AS n FROM node_runs").fetchone()["n"]
        return {"missions": missions, "graphs": graphs, "node_runs": nodes}

def main() -> int:
    root = REPO / "tmp" / "p0-tasks"
    if root.exists():
        import shutil
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)

    print("== 1) 两个任务，各自独立 DB ==")
    a = make_task(root, "T-A")
    b = make_task(root, "T-B")
    print("A db:", a["db"], "graph:", a["graph_run_id"][:12])
    print("B db:", b["db"], "graph:", b["graph_run_id"][:12])
    sa, sb = dump(a["db"]), dump(b["db"])
    assert sa["graphs"] == [a["graph_run_id"]], sa
    assert sb["graphs"] == [b["graph_run_id"]], sb
    print("isolation at creation: OK")

    print("\n== 2) 两线程交替推进（每任务单独 runtime） ==")
    errors: list[str] = []
    trace: dict[str, list[str]] = {"A": [], "B": []}

    def drive(tag: str, task: dict, barrier: threading.Barrier) -> None:
        try:
            barrier.wait(timeout=10)
            for _ in range(3):
                report = advance_graph(task["runtime"], task["graph_run_id"])
                trace[tag].append(report["status"])
                if report["status"] in {"succeeded", "failed", "canceled"}:
                    break
        except BaseException as exc:  # noqa: BLE001 - spike records everything
            errors.append(f"{tag}: {exc}\n{traceback.format_exc()}")

    barrier = threading.Barrier(2)
    t0 = time.perf_counter()
    threads = [
        threading.Thread(target=drive, args=("A", a, barrier), name="drive-A"),
        threading.Thread(target=drive, args=("B", b, barrier), name="drive-B"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    elapsed = time.perf_counter() - t0
    print("A statuses:", trace["A"])
    print("B statuses:", trace["B"])
    print("errors:", len(errors))
    for e in errors:
        print(e)
    assert not errors, "concurrent advance raised"
    assert trace["A"][-1] == "succeeded", trace["A"]
    assert trace["B"][-1] == "succeeded", trace["B"]
    print(f"both graphs completed concurrently in {elapsed:.2f}s")

    print("\n== 3) 结果未串库 ==")
    sa, sb = dump(a["db"]), dump(b["db"])
    print("A:", sa)
    print("B:", sb)
    assert sa["graphs"] == [a["graph_run_id"]] and sb["graphs"] == [b["graph_run_id"]]
    assert sa["node_runs"] == 2 and sb["node_runs"] == 2, (sa, sb)
    assert sa["missions"] != sb["missions"]
    print("no cross-contamination: OK")

    print("\n== 4) 一个库写、另一个进程只读打开（工作台读取场景） ==")
    ro = SQLiteMissionStore.open_read_only(a["db"])
    print("read-only missions:", len(ro.list_missions()), "| read-only graph runs:",
          len(ro.list_graph_runs(a["mission_id"])))
    assert len(ro.list_graph_runs(a["mission_id"])) == 1

    print("\n== 5) 同库并发推进（预期：需单线程，记录真实行为） ==")
    same_errors: list[str] = []
    same_status: list[str] = []
    c = make_task(root, "T-C")

    def drive_same() -> None:
        try:
            for _ in range(2):
                rep = advance_graph(c["runtime"], c["graph_run_id"])
                same_status.append(rep["status"])
        except BaseException as exc:  # noqa: BLE001
            same_errors.append(str(exc))

    barrier2 = threading.Barrier(2)
    def drive_same2() -> None:
        try:
            barrier2.wait(timeout=10)
            for _ in range(2):
                rep = advance_graph(c["runtime"], c["graph_run_id"])
                same_status.append(rep["status"])
        except BaseException as exc:  # noqa: BLE001
            same_errors.append(str(exc))

    ts = [threading.Thread(target=drive_same), threading.Thread(target=drive_same2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=120)
    print("same-db statuses:", same_status)
    print("same-db errors:", same_errors[:2])
    print("same-db node_runs:", dump(c["db"])["node_runs"])

    result = {
        "templates": TEMPLATE["id"],
        "concurrent_ok": True,
        "elapsed_seconds": round(elapsed, 3),
        "a_node_runs": sa["node_runs"],
        "b_node_runs": sb["node_runs"],
        "same_db_errors": len(same_errors),
        "same_db_statuses": same_status,
    }
    out = REPO / "tmp" / "p0-q3-result.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("\nRESULT:", json.dumps(result, indent=2))
    print("\nP0-Q3 PASSED")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())