from __future__ import annotations

import argparse
import os
from multiprocessing.connection import Listener

from aedt_agent.interactive.process_manager import serve_layout_worker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--authkey")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    environment_authkey = os.environ.pop("AEDT_AGENT_LAYOUT_WORKER_AUTHKEY", "")
    authkey_text = args.authkey or environment_authkey
    try:
        authkey = bytes.fromhex(authkey_text)
    except ValueError as exc:
        raise ValueError("PyEDB worker auth key is invalid") from exc
    if len(authkey) < 32:
        raise ValueError("PyEDB worker auth key is missing or too short")
    listener = Listener((args.host, args.port), authkey=authkey)
    try:
        connection = listener.accept()
        serve_layout_worker(connection)
    finally:
        listener.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
