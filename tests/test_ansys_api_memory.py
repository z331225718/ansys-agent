from __future__ import annotations

import json
from pathlib import Path
import queue
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

import aedt_agent.knowledge.api_memory as api_memory
from aedt_agent.knowledge.api_memory import AnsysApiMemory, SourcePackage, source_inventory_digest
from aedt_agent.knowledge.evidence import ApiMemoryEvidenceVerifier
from aedt_agent.exploration.contracts import ExplorationError


class _FakeMcpStream:
    def __init__(self):
        self.lines: queue.Queue[str] = queue.Queue()

    def readline(self):
        return self.lines.get()

    def close(self):
        self.lines.put("")


class _FakeMcpInput:
    def __init__(self, handler):
        self.handler = handler
        self.messages = []

    def write(self, value):
        self.messages.append(json.loads(value))
        self.handler(self.messages[-1])
        return len(value)

    def flush(self):
        return None


class _FakeMcpProcess:
    def __init__(self):
        self.stdout = _FakeMcpStream()
        self.stderr = _FakeMcpStream()
        self.stdin = _FakeMcpInput(self._handle)
        self.returncode = None
        self.pid = 12345

    def _handle(self, message):
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:
            return
        if method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "serverInfo": {"name": "codebase-memory-mcp", "version": "0.9.0"},
                "capabilities": {"tools": {"listChanged": False}},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": name}
                    for name in (
                        "index_repository",
                        "search_graph",
                        "get_code_snippet",
                        "trace_path",
                        "search_code",
                    )
                ]
            }
        elif method == "tools/call":
            tool = message["params"]["name"]
            if tool == "index_status":
                payload = {"project": "pyaedt-project", "status": "ready", "root_path": "C:/pyaedt"}
            else:
                payload = {"results": [{"name": tool}]}
            result = {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}
        else:
            result = {}
        self.stdout.lines.put(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}))

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 1
        self.stdout.close()
        self.stderr.close()

    def wait(self, timeout=None):
        return self.returncode


class FakeClient:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.allowed_root = None
        self.projects = set()
        self.project_roots = {}
        self.calls = []

    def configure(self):
        self.calls.append(("configure", {}))

    def tool(self, name, arguments, timeout=None):
        self.calls.append((name, arguments))
        if name == "list_projects":
            return {"projects": [{"name": item} for item in sorted(self.projects)]}
        if name == "index_repository":
            self.projects.add(arguments["name"])
            self.project_roots[arguments["name"]] = arguments["repo_path"]
            return {"project": arguments["name"], "status": "indexed"}
        if name == "index_status":
            project = arguments["project"]
            return {
                "project": project,
                "status": "ready" if project in self.projects else "missing",
                "root_path": self.project_roots.get(project),
            }
        if name == "search_graph":
            return {
                "results": [
                    {
                        "name": "width",
                        "qualified_name": f"{arguments['project']}.Line.width",
                        "file_path": "line.py",
                    }
                ]
            }
        if name == "get_code_snippet":
            return {
                "qualified_name": arguments["qualified_name"],
                "file_path": "line.py",
                "source": "def width(self): pass\n",
            }
        if name == "trace_path":
            return {"paths": [{"from": arguments["function_name"], "to": "setter"}]}
        if name == "search_code":
            return {"results": [{"file_path": "tests/test_line.py"}]}
        raise AssertionError(name)


def test_codebase_memory_cli_does_not_inherit_mcp_stdio(tmp_path: Path):
    executable = tmp_path / "codebase-memory-mcp.exe"
    executable.touch()
    captured = {}

    def runner(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout='{"projects": []}', stderr="")

    client = api_memory.CodebaseMemoryCli(
        executable=executable,
        cache_dir=tmp_path / "cbm",
        runner=runner,
    )

    assert client.tool("list_projects", {}) == {"projects": []}
    assert captured["stdin"] is subprocess.DEVNULL


def test_persistent_codebase_memory_mcp_client_reuses_one_verified_stdio_session(tmp_path: Path, monkeypatch):
    executable = tmp_path / "codebase-memory-mcp.exe"
    executable.touch()
    process = _FakeMcpProcess()
    launched = []
    monkeypatch.setenv("AEDT_AGENT_APPROVAL_SECRET", "must-not-reach-native-backend")
    client = api_memory.CodebaseMemoryMcpClient(
        executable=executable,
        cache_dir=tmp_path / "cbm",
        allowed_root=tmp_path,
        popen_factory=lambda *args, **kwargs: launched.append((args, kwargs)) or process,
    )

    assert client.tool("index_status", {"project": "pyaedt-project"}) == {
        "project": "pyaedt-project",
        "status": "ready",
        "root_path": "C:/pyaedt",
    }
    assert client.tool("search_graph", {"project": "pyaedt-project", "query": "void"}) == {
        "results": [{"name": "search_graph"}]
    }
    assert len(launched) == 1
    assert [message["method"] for message in process.stdin.messages] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
        "tools/call",
    ]
    environment = launched[0][1]["env"]
    assert environment["CBM_CACHE_DIR"] == str((tmp_path / "cbm").resolve())
    assert environment["CBM_ALLOWED_ROOT"] == str(tmp_path.resolve())
    assert "AEDT_AGENT_APPROVAL_SECRET" not in environment
    client.close()


def _packages(tmp_path: Path):
    common = tmp_path / "site-packages"
    pyaedt = common / "ansys" / "aedt" / "core"
    pyedb = common / "pyedb"
    pyaedt.mkdir(parents=True)
    pyedb.mkdir(parents=True)
    (pyaedt / "line.py").write_text("class Line: pass\n", encoding="ascii")
    (pyedb / "modeler.py").write_text("class Modeler: pass\n", encoding="ascii")
    pyaedt_digest, pyaedt_count = source_inventory_digest(pyaedt)
    pyedb_digest, pyedb_count = source_inventory_digest(pyedb)
    return [
        SourcePackage("pyaedt", "pyaedt", "1.0.1", str(pyaedt), pyaedt_digest, pyaedt_count, "pyaedt-project"),
        SourcePackage("pyedb", "pyedb", "0.77.0", str(pyedb), pyedb_digest, pyedb_count, "pyedb-project"),
    ]


def test_api_memory_prepares_versioned_indexes_and_detects_stale_source(tmp_path: Path):
    packages = _packages(tmp_path)
    client = FakeClient(tmp_path / "cbm")
    memory = AnsysApiMemory(
        knowledge_root=tmp_path / "knowledge",
        client=client,
        source_locator=lambda: packages,
    )

    prepared = memory.prepare()

    assert prepared["status"] == "ready"
    assert memory.status()["ready"] is True
    assert {call[1].get("name") for call in client.calls if call[0] == "index_repository"} == {
        "pyaedt-project",
        "pyedb-project",
    }
    manifest = json.loads(memory.manifest_path.read_text(encoding="utf-8"))
    assert manifest["packages"][0]["version"] == "1.0.1"
    changed = list(packages)
    changed[0] = SourcePackage(**(packages[0].to_dict() | {"source_digest": "f" * 64}))
    memory.source_locator = lambda: changed
    assert memory.status(force_refresh=True)["status"] == "stale"


def test_api_memory_reindexes_same_digest_project_after_source_root_moves(tmp_path: Path):
    packages = _packages(tmp_path)
    client = FakeClient(tmp_path / "cbm")
    memory = AnsysApiMemory(
        knowledge_root=tmp_path / "knowledge",
        client=client,
        source_locator=lambda: packages,
    )
    memory.prepare()

    relocated_root = tmp_path / "relocated" / "pyaedt"
    relocated_root.mkdir(parents=True)
    (relocated_root / "line.py").write_text("class Line: pass\n", encoding="ascii")
    relocated = list(packages)
    relocated[0] = SourcePackage(**(packages[0].to_dict() | {"source_root": str(relocated_root)}))
    memory.source_locator = lambda: relocated
    client.calls.clear()

    memory.prepare()

    reindexed = [arguments["name"] for name, arguments in client.calls if name == "index_repository"]
    assert reindexed == ["pyaedt-project"]
    assert memory.status()["ready"] is True


def test_api_memory_status_lease_skips_global_enumeration_but_force_refresh_rechecks(tmp_path: Path):
    packages = _packages(tmp_path)
    client = FakeClient(tmp_path / "cbm")
    memory = AnsysApiMemory(
        knowledge_root=tmp_path / "knowledge",
        client=client,
        source_locator=lambda: packages,
        status_lease_seconds=60,
    )
    memory.prepare()
    client.calls.clear()

    assert memory.status()["ready"] is True
    first_calls = list(client.calls)
    assert [name for name, _ in first_calls] == ["index_status", "index_status"]
    assert memory.status()["ready"] is True
    assert client.calls == first_calls
    assert memory.status(force_refresh=True)["ready"] is True
    assert [name for name, _ in client.calls] == [
        "index_status",
        "index_status",
        "index_status",
        "index_status",
    ]


def test_side_by_side_source_roots_have_isolated_projects_and_manifests(
    monkeypatch,
    tmp_path: Path,
):
    sites = [tmp_path / "install-a" / "site-packages", tmp_path / "install-b" / "site-packages"]
    for site in sites:
        pyaedt = site / "ansys" / "aedt" / "core"
        pyedb = site / "pyedb"
        pyaedt.mkdir(parents=True)
        pyedb.mkdir(parents=True)
        (pyaedt / "__init__.py").write_text("class Desktop: pass\n", encoding="ascii")
        (pyedb / "__init__.py").write_text("class Edb: pass\n", encoding="ascii")

    selected = {"site": sites[0]}

    def fake_find_spec(module_name):
        relative = Path("ansys/aedt/core/__init__.py") if module_name == "ansys.aedt.core" else Path(
            "pyedb/__init__.py"
        )
        return SimpleNamespace(origin=str(selected["site"] / relative))

    versions = {"pyaedt": "1.0.1", "pyedb": "0.77.0"}
    monkeypatch.setattr(api_memory.util, "find_spec", fake_find_spec)
    original_version = api_memory.metadata.version
    monkeypatch.setattr(
        api_memory.metadata,
        "version",
        lambda distribution: versions.get(distribution) or original_version(distribution),
    )

    packages_a = api_memory.locate_ansys_sources()
    selected["site"] = sites[1]
    packages_b = api_memory.locate_ansys_sources()

    by_key_a = {item.key: item for item in packages_a}
    by_key_b = {item.key: item for item in packages_b}
    for key in ("pyaedt", "pyedb"):
        assert by_key_a[key].source_digest == by_key_b[key].source_digest
        assert by_key_a[key].project != by_key_b[key].project
        assert len(by_key_a[key].project) <= 96
        assert re.fullmatch(r"[A-Za-z0-9._-]+", by_key_a[key].project)
        assert "install-a" not in by_key_a[key].project
        assert "install-b" not in by_key_b[key].project

    knowledge_root = tmp_path / "knowledge"
    client = FakeClient(tmp_path / "cbm")
    memory_a = AnsysApiMemory(
        knowledge_root=knowledge_root,
        client=client,
        source_locator=lambda: packages_a,
    )
    memory_b = AnsysApiMemory(
        knowledge_root=knowledge_root,
        client=client,
        source_locator=lambda: packages_b,
    )

    memory_a.prepare()
    manifest_a = memory_a.manifest_path
    memory_b.prepare()
    manifest_b = memory_b.manifest_path

    assert manifest_a != manifest_b
    assert manifest_a.is_file()
    assert manifest_b.is_file()
    assert memory_a.status()["ready"] is True
    assert memory_b.status()["ready"] is True
    assert client.projects == {
        *(item.project for item in packages_a),
        *(item.project for item in packages_b),
    }


def test_api_memory_queries_are_bounded_and_version_evidenced(tmp_path: Path):
    packages = _packages(tmp_path)
    client = FakeClient(tmp_path / "cbm")
    memory = AnsysApiMemory(
        knowledge_root=tmp_path / "knowledge",
        client=client,
        source_locator=lambda: packages,
    )
    memory.prepare()

    search = memory.search("line width", package="pyaedt", limit=3)
    assert search["query_id"].startswith("query-")
    assert search["results"][0]["package_version"] == "1.0.1"
    inspected = memory.inspect(search["results"][0]["qualified_name"], package="pyaedt")
    assert len(inspected["results"]["snippet_digest"]) == 64
    assert inspected["operation_evidence"] == {
        "package": "pyaedt",
        "package_version": "1.0.1",
        "project": "pyaedt-project",
        "symbol": "pyaedt-project.Line.width",
        "source_path": "line.py",
        "snippet_digest": inspected["results"]["snippet_digest"],
        "query_id": inspected["query_id"],
    }
    assert memory.trace("Line.width", package="pyaedt")["kind"] == "trace"
    assert memory.search_source("width", examples_only=True)["kind"] == "examples"


def test_inspect_evidence_can_be_used_directly_by_exploration_validator(tmp_path: Path):
    from aedt_agent.exploration.validator import OperationValidator

    packages = _packages(tmp_path)
    memory = AnsysApiMemory(
        knowledge_root=tmp_path / "knowledge",
        client=FakeClient(tmp_path / "cbm"),
        source_locator=lambda: packages,
    )
    memory.prepare()
    symbol = memory.search("line width", package="pyaedt", limit=1)["results"][0]["qualified_name"]
    evidence = memory.inspect(symbol, package="pyaedt")["operation_evidence"]
    result = OperationValidator(package_versions={"pyaedt": "1.0.1", "pyedb": "0.77.0"}).validate(
        {
            "schema_version": "ansys-operation-plan/v1",
            "intent": "read one line width",
            "target": {
                "product": "hfss3dlayout",
                "project_name": "Board",
                "design_name": "Layout1",
            },
            "risk": "read_only",
            "evidence": [evidence],
            "steps": [{"id": "read-width", "op": "read_attr", "path": "modeler.lines.line1.width"}],
            "readback": [],
            "rollback": [],
        }
    )

    assert result["status"] == "validated"
    assert result["mutation_count"] == 0


def test_runtime_evidence_verifier_replays_inspect_and_rejects_forged_query_id():
    evidence = {
        "package": "pyaedt",
        "package_version": "1.0.1",
        "project": "pyaedt-project",
        "symbol": "pyaedt-project.Line.width",
        "source_path": "line.py",
        "snippet_digest": "a" * 64,
        "query_id": "query-real",
    }

    class Memory:
        def status(self):
            return {
                "status": "ready",
                "ready": True,
                "manifest": {
                    "manifest_digest": "manifest-current",
                    "packages": [
                        {
                            "key": "pyaedt",
                            "version": "1.0.1",
                            "project": "pyaedt-project",
                        }
                    ],
                },
            }

        def inspect(self, symbol, *, package):
            assert symbol == evidence["symbol"]
            assert package == "pyaedt"
            return {"operation_evidence": dict(evidence)}

    verifier = ApiMemoryEvidenceVerifier(memory_factory=Memory)
    verified = verifier.verify([evidence])
    assert verified["status"] == "verified"
    assert verified["manifest_digest"] == "manifest-current"

    forged = {**evidence, "query_id": "query-invented"}
    with pytest.raises(ExplorationError) as error:
        verifier.verify([forged])
    assert error.value.code == "evidence_unverified"


def test_api_memory_mcp_exposes_query_only_tools(monkeypatch, tmp_path: Path):
    packages = _packages(tmp_path)
    memory = AnsysApiMemory(
        knowledge_root=tmp_path / "knowledge",
        client=FakeClient(tmp_path / "cbm"),
        source_locator=lambda: packages,
    )
    memory.prepare()

    class FakeFastMCP:
        def __init__(self, name, **kwargs):
            self.name = name
            self.instructions = kwargs.get("instructions")
            self.tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn

            return register

    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=FakeFastMCP))
    from aedt_agent.knowledge.server import create_server

    server = create_server(memory=memory)
    assert set(server.tools) == {
        "get_ansys_api_memory_status",
        "search_ansys_api",
        "inspect_ansys_symbol",
        "trace_ansys_call",
        "search_ansys_source",
        "find_ansys_example",
    }
    assert "index_repository" not in server.tools
    assert "delete_project" not in server.tools
    from aedt_agent.desktop.launcher import _DESKTOP_API_MEMORY_MCP_TOOLS

    assert set(server.tools) == set(_DESKTOP_API_MEMORY_MCP_TOOLS)
