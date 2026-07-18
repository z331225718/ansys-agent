from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aedt_agent.interactive.kernel import InteractiveKernel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ansys-assistant",
        description="Capability-driven interactive Ansys assistant CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("capabilities", help="List machine-readable assistant capabilities.")
    subparsers.add_parser("capabilities-v2", help="List unified live/artifact capabilities and risks.")
    subparsers.add_parser("live-sessions", help="Discover running AEDT sessions without attaching.")
    live_launch = subparsers.add_parser("live-launch", help="Launch AEDT with gRPC and release the wrapper.")
    live_launch.add_argument("--aedt-version", default="2026.1")
    live_launch.add_argument("--port", type=int, default=0)
    live_launch.add_argument("--install-dir")
    live_launch.add_argument("--non-graphical", action="store_true")
    live_launch.add_argument("--timeout", type=float, default=120.0)
    live_info = subparsers.add_parser("live-info", help="Attach, read live project info, and release.")
    target = live_info.add_mutually_exclusive_group(required=True)
    target.add_argument("--pid", type=int)
    target.add_argument("--port", type=int)
    live_info.add_argument("--aedt-version", default="2026.1")

    inspect_parser = subparsers.add_parser("inspect-layout", help="List HFSS 3D Layout paths.")
    _add_project_options(inspect_parser)
    _add_selector_options(inspect_parser, require_width=False)

    parameterize_parser = subparsers.add_parser(
        "parameterize-width",
        help="Preview or apply path-width parameterization in a working copy.",
    )
    _add_project_options(parameterize_parser)
    _add_selector_options(parameterize_parser, require_width=True)
    parameterize_parser.add_argument("--variable-name", required=True)
    parameterize_parser.add_argument(
        "--variable-value",
        help="Initial parameter value. Defaults to --target-width.",
    )
    parameterize_parser.add_argument(
        "--workspace",
        type=Path,
        help="Root directory for the automatically created working copy.",
    )
    parameterize_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the preview. Without this flag the command is read-only except for creating the working copy.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kernel = InteractiveKernel()
    try:
        if args.command == "capabilities":
            _print_json(kernel.list_capabilities())
            return 0
        if args.command == "capabilities-v2":
            from aedt_agent.interactive.catalog_v2 import capability_catalog_v2

            _print_json(capability_catalog_v2())
            return 0
        if args.command in {"live-sessions", "live-launch", "live-info"}:
            from aedt_agent.live.manager import LiveAedtSessionManager

            live = LiveAedtSessionManager()
            try:
                if args.command == "live-sessions":
                    _print_json(live.list_sessions())
                    return 0
                if args.command == "live-launch":
                    opened = live.launch(
                        version=args.aedt_version,
                        port=args.port,
                        install_dir=args.install_dir,
                        non_graphical=args.non_graphical,
                        timeout=args.timeout,
                    )
                    released = live.release(opened["live_session_id"])
                    _print_json({"status": "succeeded", "session": opened, "release": released})
                    return 0
                opened = live.attach(pid=args.pid, port=args.port, version=args.aedt_version)
                try:
                    info = live.project_info(opened["live_session_id"])
                finally:
                    released = live.release(opened["live_session_id"])
                _print_json({"status": "succeeded", "session": opened, "project": info, "release": released})
                return 0
            finally:
                live.close()
        if args.command == "inspect-layout":
            output = _run_inspect(kernel, args)
            _print_json(output)
            return 0
        if args.command == "parameterize-width":
            output = _run_parameterize(kernel, args)
            _print_json(output)
            return 0
        raise ValueError(f"unsupported command: {args.command}")
    except Exception as exc:
        _print_json(
            {
                "status": "failed",
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            },
            stream=sys.stderr,
        )
        return 2


def _run_inspect(kernel: InteractiveKernel, args: argparse.Namespace) -> dict[str, Any]:
    opened = kernel.open_layout_session(
        str(args.project),
        writable=False,
        version=args.aedt_version,
        edb_backend=args.edb_backend,
    )
    session_id = opened["session_id"]
    try:
        result = kernel.execute_capability(
            "layout.paths.list",
            {"session_id": session_id, "selector": _selector_payload(args, parameterized=None)},
        )
    finally:
        closed = kernel.close_layout_session(session_id)
    return {"status": "succeeded", "session": opened, "result": result, "close": closed}


def _run_parameterize(kernel: InteractiveKernel, args: argparse.Namespace) -> dict[str, Any]:
    opened = kernel.open_layout_session(
        str(args.project),
        writable=True,
        workspace=None if args.workspace is None else str(args.workspace),
        version=args.aedt_version,
        edb_backend=args.edb_backend,
    )
    session_id = opened["session_id"]
    result: dict[str, Any]
    try:
        preview = kernel.execute_capability(
            "layout.path_width.parameterize.preview",
            {
                "session_id": session_id,
                "selector": _selector_payload(args, parameterized=False),
                "variable_name": args.variable_name,
                "variable_value": args.variable_value or args.target_width,
            },
        )
        result = {"status": "preview", "session": opened, "preview": preview}
        if args.apply:
            applied = kernel.execute_capability(
                "layout.path_width.parameterize.apply",
                {"session_id": session_id, "preview_id": preview["preview_id"]},
            )
            result = {"status": "verified", "session": opened, "preview": preview, "result": applied}
    finally:
        closed = kernel.close_layout_session(session_id)
    result["close"] = closed
    return result


def _add_project_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, required=True, help="Path to an .aedt project or .aedb directory.")
    parser.add_argument("--aedt-version", default="2026.1")
    parser.add_argument("--edb-backend", choices=("auto", "grpc", "dotnet"), default="auto")


def _add_selector_options(parser: argparse.ArgumentParser, *, require_width: bool) -> None:
    parser.add_argument("--target-width", required=require_width, help="Path width to match, for example 0.1mm.")
    parser.add_argument("--tolerance", default="1nm")
    parser.add_argument("--net", action="append", default=[])
    parser.add_argument("--layer", action="append", default=[])
    parser.add_argument("--primitive-id", action="append", default=[])


def _selector_payload(args: argparse.Namespace, *, parameterized: bool | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tolerance": args.tolerance,
        "nets": list(args.net),
        "layers": list(args.layer),
        "primitive_ids": list(args.primitive_id),
        "parameterized": parameterized,
    }
    if args.target_width is not None:
        payload["target_width"] = args.target_width
    return payload


def _print_json(payload: dict[str, Any], *, stream=None) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), file=stream or sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
