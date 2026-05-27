from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from aedt_agent.demo.config import load_demo_config
from aedt_agent.demo.import_cutout import build_import_cutout_request, run_fake_import_cutout, run_real_import_cutout
from aedt_agent.demo.preflight import run_stage_c_preflight
from aedt_agent.layout.acceptance import write_brd_acceptance_summary
from aedt_agent.layout.progress import BrdWorkflowProgressWriter
from aedt_agent.layout.workflow_run import import_cutout_summary_to_workflow_run
from aedt_agent.reporting.stage_c_brd_report import render_brd_acceptance_html


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Stage C BRD/MCM model-build and package production acceptance artifacts.")
    parser.add_argument("--adapter", choices=["real", "fake"], default="real")
    parser.add_argument("--params", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--config", default="config/demo_config.example.json")
    parser.add_argument("--local-config", default="config/demo_config.local.json")
    parser.add_argument("--allow-preflight-fail", action="store_true")
    parser.add_argument("--allow-failed", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    params = _read_params(Path(args.params))
    params["artifact_dir"] = str(run_dir)
    (run_dir / "params.json").write_text(json.dumps(params, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    config = load_demo_config(example_path=Path(args.config), local_path=Path(args.local_config))
    preflight = run_stage_c_preflight(config, parameters=params)
    (run_dir / "preflight.json").write_text(json.dumps(preflight.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not preflight.ok and not args.allow_preflight_fail:
        summary = _write_acceptance(run_dir)
        _print_summary(summary, run_dir)
        raise SystemExit(1)

    request = build_import_cutout_request(params)
    progress = BrdWorkflowProgressWriter(
        run_dir / "workflow_run.json",
        layout_file=str(request.layout_file),
        signal_nets=request.signal_net_patterns,
        reference_nets=request.reference_net_patterns,
    )

    def on_progress(event: dict[str, object]) -> None:
        step_id = str(event.get("step_id") or "")
        label = str(event.get("label") or step_id)
        status = str(event.get("status") or "running")
        output = {key: value for key, value in event.items() if key not in {"step_id", "label", "status", "error_type", "error_message"}}
        if status == "running":
            progress.step_running(step_id, label, output)
        elif status == "succeeded":
            progress.step_succeeded(step_id, label, output)
        elif status == "failed":
            progress.step_failed(step_id, label, str(event.get("error_type") or ""), str(event.get("error_message") or ""))
        print(f"[brd-progress] {step_id} {status} {label}", flush=True)

    try:
        if args.adapter == "fake":
            result = run_fake_import_cutout(request, progress_callback=on_progress)
        else:
            result = run_real_import_cutout(
                request,
                aedt_version=config.aedt.version,
                cadence_launcher=config.aedt.cadence_launcher,
                ansysem_root=config.aedt.ansysem_root,
                awp_root=config.aedt.awp_root,
                non_graphical=config.aedt.non_graphical,
                progress_callback=on_progress,
            )
        (run_dir / "import_cutout_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        import_cutout_summary_to_workflow_run(result).write_json(run_dir / "workflow_run.json")
    except Exception as exc:
        progress.finish_failed(type(exc).__name__, str(exc))
        (run_dir / "stderr.log").write_text(str(exc) + "\n", encoding="utf-8")
    else:
        (run_dir / "stdout.log").write_text("Stage C BRD acceptance workflow completed.\n", encoding="utf-8")
        (run_dir / "stderr.log").write_text("", encoding="utf-8")

    summary = _write_acceptance(run_dir)
    _print_summary(summary, run_dir)
    raise SystemExit(0 if summary["status"] == "succeeded" or args.allow_failed else 1)


def _write_acceptance(run_dir: Path) -> dict[str, object]:
    summary = write_brd_acceptance_summary(run_dir)
    (run_dir / "acceptance_report.html").write_text(render_brd_acceptance_html(summary), encoding="utf-8")
    return summary


def _print_summary(summary: dict[str, object], run_dir: Path) -> None:
    print(f"Stage C BRD acceptance: {summary['status']}")
    print(f"Report: {run_dir / 'acceptance_report.html'}")


def _read_params(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("--params must point to a JSON object")
    return data


if __name__ == "__main__":
    main()
