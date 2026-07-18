from __future__ import annotations

import argparse
import contextlib
import sys

from aedt_agent.live.backend import LiveAedtBackend, LiveBackendError
from aedt_agent.live.protocol import ProtocolError, WorkerRequest, WorkerResponse
from aedt_agent.live.versioning import DEFAULT_AEDT_VERSION, normalize_aedt_version


def serve(
    input_stream,
    output_stream,
    *,
    backend_factory=LiveAedtBackend,
    version: str = DEFAULT_AEDT_VERSION,
) -> int:
    backend = backend_factory(version=normalize_aedt_version(version))
    try:
        for line in input_stream:
            if not line.strip():
                continue
            request_id = "unknown"
            request = None
            try:
                request = WorkerRequest.from_json(line)
                request_id = request.request_id
                with contextlib.redirect_stdout(sys.stderr):
                    result = backend.execute(request.target, request.command, request.arguments)
                response = WorkerResponse.success(request_id, result)
            except ProtocolError as exc:
                response = WorkerResponse.failure(request_id, "protocol_error", str(exc))
            except LiveBackendError as exc:
                response = WorkerResponse.failure(request_id, exc.code, str(exc))
            except Exception as exc:
                response = WorkerResponse.failure(request_id, "backend_error", str(exc))
            output_stream.write(response.to_json() + "\n")
            output_stream.flush()
            if request is not None and request.command == "release":
                return 0
    finally:
        try:
            backend.release()
        except Exception:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one version-bound live AEDT broker worker")
    parser.add_argument("--version", default=DEFAULT_AEDT_VERSION)
    args = parser.parse_args(argv)
    try:
        version = normalize_aedt_version(args.version)
    except ValueError as exc:
        parser.error(str(exc))
    return serve(sys.stdin, sys.stdout, version=version)


if __name__ == "__main__":
    raise SystemExit(main())
