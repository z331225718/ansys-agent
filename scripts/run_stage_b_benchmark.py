from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.benchmark.aedt_executor import AEDTSubprocessExecutor
from aedt_agent.benchmark.config import load_benchmark_config
from aedt_agent.benchmark.runner_stage_b import run_stage_b_node_benchmark
from aedt_agent.mcp.pyaedt_adapter import PyaedtAdapter
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.mcp.tools import create_fake_kernel, McpToolKernel
from aedt_agent.mcp.ast_guard import AstGuard
from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.node_executor import NodeExecutor
from aedt_agent.mcp.audit_log import AuditLogger
from aedt_agent.nodes.registry import NodeRegistry


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="run_stage_b_benchmark.py")
    parser.add_argument("--task", action="append", dest="tasks")
    parser.add_argument("--groups", nargs="+", choices=["B", "C"], default=["B", "C"])
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--run-dir", default="benchmarks/runs/stage_b_latest")
    parser.add_argument("--fake-node-kernel", action="store_true", help="Use fake node kernel for unit-level dry runs only.")
    parser.add_argument("--dry-run-node-plan", action="store_true", help="Use a static C-group node plan generator for local plumbing checks.")
    return parser.parse_args()


def _print_progress(event: dict) -> None:
    if event.get("phase") == "attempt_start":
        print(f"[heartbeat] {event['task_id']} [{event['group']}] attempt {event['attempt']}/{event['max_attempts']}", flush=True)
    elif event.get("phase") == "attempt_end":
        status = "PASS" if event.get("final_pass") else "FAIL"
        print(f"[attempt] {event['task_id']} [{event['group']}] {status} failure={event.get('failure_type') or '-'}", flush=True)
    else:
        print(f"[{event.get('current', '?')}/{event.get('total', '?')}] {event.get('task_id')} [{event.get('group')}]", flush=True)


def _build_real_kernel(config, audit_path: Path) -> McpToolKernel:
    registry = NodeRegistry.from_directory(REPO_ROOT / "nodes/catalog")
    session_manager = SessionManager(
        lambda project_id, design_id: PyaedtAdapter(
            project_id=project_id,
            design_id=design_id,
            version=config.aedt.version,
            non_graphical=config.aedt.non_graphical,
        )
    )
    queue = ExecutionQueue(timeout_seconds=config.aedt.timeout)
    node_executor = NodeExecutor(
        registry=registry,
        session_manager=session_manager,
        queue=queue,
        audit_logger=AuditLogger(audit_path),
    )
    return McpToolKernel(
        registry=registry,
        session_manager=session_manager,
        node_executor=node_executor,
        queue=queue,
        ast_guard=AstGuard(),
        dev_mode=False,
    )


def main() -> None:
    args = _parse_args()
    config = load_benchmark_config(REPO_ROOT / "config/benchmark_config.json")
    run_dir = REPO_ROOT / args.run_dir
    audit_path = run_dir / "stage_b_node_audit.jsonl"
    kernel = (
        create_fake_kernel(REPO_ROOT / "nodes/catalog", audit_path=audit_path)
        if args.fake_node_kernel
        else _build_real_kernel(config, audit_path)
    )
    generator = config.build_generator(REPO_ROOT)
    if args.dry_run_node_plan:
        generator = _StaticNodePlanGenerator()
    report = run_stage_b_node_benchmark(
        tasks_dir=REPO_ROOT / config.paths.tasks,
        run_dir=run_dir,
        group_b_generator=config.build_generator(REPO_ROOT),
        group_c_generator=generator,
        group_b_executor=AEDTSubprocessExecutor(
            version=config.aedt.version,
            non_graphical=config.aedt.non_graphical,
            ansysem_root=config.aedt.ansysem_root,
            awp_root=config.aedt.awp_root,
            timeout=config.aedt.timeout,
        ),
        kernel=kernel,
        groups=args.groups,
        task_ids=args.tasks,
        max_attempts=args.max_attempts,
        progress_callback=_print_progress,
    )
    print(f"Stage B report written to: {run_dir / 'stage_b_report.json'}")
    print(f"Group metrics: {report['groups']}")


class _StaticNodePlanGenerator:
    def generate(self, context: str, filename: str | None = None) -> str:
        return (
            '{"plan": [{"node_id": "create_substrate", '
            '"inputs": {"origin": [0, 0, 0], "size": [20, 15, 0.8], "material": "FR4_epoxy"}}]}'
        )


if __name__ == "__main__":
    main()
