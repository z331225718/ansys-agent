from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from aedt_agent.agent.runtime_factory import create_readonly_runtime_without_workers
from aedt_agent.operator_api import OperatorContractError, OperatorSchemaCatalog, OperatorService
from aedt_agent.operator_bridge.dispatcher import BridgeDispatcher
from aedt_agent.operator_bridge.protocol import BridgeProtocolError, DedicatedPipeWriter, read_request_frame, response_error


def main() -> int:
    parser = argparse.ArgumentParser(description="AEDT Pi read-only operator bridge")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--response-fd", type=int, default=3)
    parser.add_argument("--schema-root", type=Path, default=Path("schemas/operator/v1"))
    args = parser.parse_args()
    _prepare_process_stdio()
    catalog = OperatorSchemaCatalog.load(args.schema_root)
    dispatcher = BridgeDispatcher(OperatorService(create_readonly_runtime_without_workers(args.db), schema_catalog=catalog), catalog)
    writer = DedicatedPipeWriter(args.response_fd)
    while True:
        try:
            request = read_request_frame(sys.stdin.buffer)
        except BridgeProtocolError:
            return 2
        if request is None:
            return 0
        try:
            # Unknown/R3 operation names cannot form a schema-valid response envelope.
            # Reject them at the wire boundary rather than emitting an invalid error response.
            catalog.validate_envelope(request)
        except OperatorContractError:
            return 2
        writer.write(dispatcher.dispatch(request))


def _prepare_process_stdio() -> None:
    # Native DLLs can bypass Python's sys.stdout. Keep protocol traffic off fd 1 entirely.
    try:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
