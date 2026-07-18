from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _log(tool: str, arguments: dict[str, Any]) -> None:
    path = os.getenv("MCP_BENCH_LOG")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False) + "\n")


def _evidence(symbol: str, query_id: str) -> dict[str, str]:
    member = symbol.rsplit(".", 1)[-1]
    return {
        "package": "pyaedt",
        "package_version": "1.0.1",
        "project": "ansys-pyaedt-1.0.1-benchmark",
        "symbol": symbol,
        "source_path": f"ansys/aedt/core/modeler/cad/object_3d_layout.py#{member}",
        "snippet_digest": hashlib.sha256(symbol.encode("utf-8")).hexdigest(),
        "query_id": query_id,
    }


def create_server():
    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install the benchmark dependencies") from exc

    server = FastMCP(
        "ansys-api-memory-benchmark",
        instructions=(
            "Read-only, version-bound PyAEDT/PyEDB knowledge. This server cannot operate AEDT. "
            "Copy operation_evidence into a declarative exploration plan; source evidence is not approval."
        ),
    )

    @server.tool()
    async def get_ansys_api_memory_status() -> dict:
        _log("get_ansys_api_memory_status", {})
        return {
            "status": "ready",
            "ready": True,
            "manifest": {
                "manifest_digest": "b" * 64,
                "packages": [
                    {
                        "key": "pyaedt",
                        "version": "1.0.1",
                        "project": "ansys-pyaedt-1.0.1-benchmark",
                    },
                    {
                        "key": "pyedb",
                        "version": "0.77.0",
                        "project": "ansys-pyedb-0.77.0-benchmark",
                    },
                ],
            },
        }

    @server.tool()
    async def search_ansys_api(query: str, package: str = "auto", limit: int = 10) -> dict:
        _log("search_ansys_api", {"query": query, "package": package, "limit": limit})
        symbol = _symbol_for(query)
        query_id = "query-benchmark-search"
        return {
            "query_id": query_id,
            "kind": "search",
            "results": [
                {
                    "package": "pyaedt",
                    "package_version": "1.0.1",
                    "qualified_name": symbol,
                    "kind": "property",
                }
            ],
            "next_step": {"tool": "inspect_ansys_symbol", "qualified_name": symbol, "package": "pyaedt"},
        }

    @server.tool()
    async def inspect_ansys_symbol(qualified_name: str, package: str) -> dict:
        _log("inspect_ansys_symbol", {"qualified_name": qualified_name, "package": package})
        query_id = "query-benchmark-inspect"
        return {
            "query_id": query_id,
            "kind": "inspect",
            "qualified_name": qualified_name,
            "source": "@property\ndef material_name(self): ...",
            "operation_evidence": _evidence(qualified_name, query_id),
        }

    @server.tool()
    async def trace_ansys_call(
        symbol: str,
        package: str,
        direction: str = "both",
        depth: int = 2,
    ) -> dict:
        _log(
            "trace_ansys_call",
            {"symbol": symbol, "package": package, "direction": direction, "depth": depth},
        )
        return {"query_id": "query-benchmark-trace", "symbol": symbol, "callers": [], "callees": []}

    @server.tool()
    async def search_ansys_source(pattern: str, package: str = "auto", limit: int = 10) -> dict:
        _log("search_ansys_source", {"pattern": pattern, "package": package, "limit": limit})
        return {"query_id": "query-benchmark-source", "results": []}

    @server.tool()
    async def find_ansys_example(pattern: str, package: str = "auto", limit: int = 10) -> dict:
        _log("find_ansys_example", {"pattern": pattern, "package": package, "limit": limit})
        return {"query_id": "query-benchmark-example", "results": []}

    return server


def _symbol_for(query: str) -> str:
    lowered = query.lower()
    if "material" in lowered:
        return "ansys.aedt.core.modeler.cad.object_3d_layout.Line3dLayout.material_name"
    return "ansys.aedt.core.modeler.cad.object_3d_layout.Line3dLayout.width"


def main() -> None:
    create_server().run(show_banner=False)


if __name__ == "__main__":
    main()
