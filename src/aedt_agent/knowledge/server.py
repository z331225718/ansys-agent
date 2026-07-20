from __future__ import annotations

import asyncio

from aedt_agent.knowledge.api_memory import AnsysApiMemory


def create_server(*, memory: AnsysApiMemory | None = None):
    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install the desktop extra to run the API memory MCP server") from exc

    api = memory or AnsysApiMemory()
    server = FastMCP(
        "ansys-api-memory",
        instructions=(
            "This is a read-only, version-bound source knowledge server for the installed PyAEDT and PyEDB. "
            "It cannot execute AEDT operations. Use returned query_id, package version, source digest, and "
            "qualified symbol as evidence for an exploratory operation plan. Never treat source presence as approval."
        ),
    )

    @server.tool()
    async def get_ansys_api_memory_status() -> dict:
        """Report whether the PyAEDT/PyEDB graphs match the currently installed source."""
        return await asyncio.to_thread(api.status)

    @server.tool()
    async def search_ansys_api(query: str, package: str = "auto", limit: int = 10) -> dict:
        """Search bounded symbols and signatures in the current version graphs."""
        return await asyncio.to_thread(api.search, query, package=package, limit=limit)

    @server.tool()
    async def inspect_ansys_symbol(qualified_name: str, package: str) -> dict:
        """Return one bounded snippet and a ready-to-use declarative operation_evidence object."""
        return await asyncio.to_thread(api.inspect, qualified_name, package=package)

    @server.tool()
    async def trace_ansys_call(
        symbol: str,
        package: str,
        direction: str = "both",
        depth: int = 2,
    ) -> dict:
        """Trace callers and callees for one current-version symbol."""
        return await asyncio.to_thread(api.trace, symbol, package=package, direction=direction, depth=depth)

    @server.tool()
    async def search_ansys_source(pattern: str, package: str = "auto", limit: int = 10) -> dict:
        """Search bounded source occurrences without exposing arbitrary repository selection."""
        return await asyncio.to_thread(api.search_source, pattern, package=package, limit=limit)

    @server.tool()
    async def find_ansys_example(pattern: str, package: str = "auto", limit: int = 10) -> dict:
        """Search only tests, examples, and documentation paths for a bounded pattern."""
        return await asyncio.to_thread(
            api.search_source,
            pattern,
            package=package,
            examples_only=True,
            limit=limit,
        )

    return server


def main() -> None:
    create_server().run(show_banner=False)


if __name__ == "__main__":
    main()
