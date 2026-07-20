from __future__ import annotations

import atexit
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from importlib import metadata, util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable


class ApiMemoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourcePackage:
    key: str
    distribution: str
    version: str
    source_root: str
    source_digest: str
    file_count: int
    project: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_knowledge_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (base / "AnsysAgent" / "knowledge").resolve()


def locate_ansys_sources() -> list[SourcePackage]:
    specs = (
        ("pyaedt", "pyaedt", "ansys.aedt.core"),
        ("pyedb", "pyedb", "pyedb"),
    )
    packages = []
    for key, distribution, module_name in specs:
        spec = util.find_spec(module_name)
        if spec is None or spec.origin is None:
            raise ApiMemoryError(f"installed source package was not found: {distribution}")
        root = Path(spec.origin).resolve().parent
        version = metadata.version(distribution)
        digest, count = source_inventory_digest(root)
        project = _codebase_project_name(
            key=key,
            version=version,
            source_digest=digest,
            source_root=root,
        )
        packages.append(SourcePackage(key, distribution, version, str(root), digest, count, project))
    return packages


def source_inventory_digest(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix in {".py", ".pyi"})
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest(), len(files)


class CodebaseMemoryCli:
    def __init__(
        self,
        *,
        executable: str | Path | None = None,
        cache_dir: str | Path | None = None,
        allowed_root: str | Path | None = None,
        timeout_seconds: float = 60,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.executable = _find_executable(executable)
        root = default_knowledge_root()
        self.cache_dir = Path(cache_dir or root / "cbm" / _backend_version()).resolve()
        self.allowed_root = Path(allowed_root).resolve() if allowed_root else None
        self.timeout_seconds = timeout_seconds
        self.runner = runner

    def configure(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._run(["config", "set", "auto_watch", "false"], timeout=30)

    def tool(self, name: str, arguments: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        if name not in {
            "index_repository",
            "list_projects",
            "index_status",
            "search_graph",
            "get_code_snippet",
            "trace_path",
            "search_code",
            "get_architecture",
            "get_graph_schema",
        }:
            raise ApiMemoryError(f"codebase-memory tool is not allowed: {name}")
        command = ["cli", name]
        for key, value in arguments.items():
            if value is None:
                continue
            flag = "--" + key.replace("_", "-")
            if isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, (list, dict)):
                text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
            else:
                text = str(value)
            command.extend([flag, text])
        result = self._run(command, timeout=timeout)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ApiMemoryError(f"invalid codebase-memory response for {name}") from exc
        if not isinstance(payload, dict):
            raise ApiMemoryError(f"codebase-memory response for {name} was not an object")
        return payload

    def _run(self, arguments: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["CBM_CACHE_DIR"] = str(self.cache_dir)
        if self.allowed_root is not None:
            environment["CBM_ALLOWED_ROOT"] = str(self.allowed_root)
        try:
            return self.runner(
                [str(self.executable), *arguments],
                check=True,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=timeout or self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise ApiMemoryError(f"codebase-memory command failed: {detail.strip()[:1000]}") from exc


class CodebaseMemoryMcpClient:
    """Serialized, private stdio client for the pinned native codebase-memory server."""

    _ALLOWED_TOOLS = frozenset(
        {
            "index_repository",
            "index_status",
            "search_graph",
            "get_code_snippet",
            "trace_path",
            "search_code",
            "get_architecture",
            "get_graph_schema",
        }
    )
    _READ_ONLY_TOOLS = _ALLOWED_TOOLS - {"index_repository"}
    _ENVIRONMENT_KEYS = (
        "APPDATA",
        "COMSPEC",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    )

    def __init__(
        self,
        *,
        executable: str | Path | None = None,
        cache_dir: str | Path | None = None,
        allowed_root: str | Path | None = None,
        timeout_seconds: float = 30,
        popen_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.executable = _find_executable(executable)
        root = default_knowledge_root()
        self.cache_dir = Path(cache_dir or root / "cbm" / _backend_version()).resolve()
        self._allowed_root = Path(allowed_root).resolve() if allowed_root else None
        self.timeout_seconds = timeout_seconds
        self.popen_factory = popen_factory
        self._lock = threading.RLock()
        self._process: Any | None = None
        self._responses: Any = None
        self._stderr: deque[str] = deque(maxlen=32)
        self._next_request_id = 1
        atexit.register(self.close)

    @property
    def allowed_root(self) -> Path | None:
        return self._allowed_root

    @allowed_root.setter
    def allowed_root(self, value: str | Path | None) -> None:
        resolved = Path(value).resolve() if value else None
        with self._lock:
            if resolved == self._allowed_root:
                return
            self._close_locked()
            self._allowed_root = resolved

    def configure(self) -> None:
        # Configuration is not exposed by the native MCP server. Keep this short-lived
        # compatibility call outside the read-only query transport.
        with self._lock:
            self._close_locked()
            CodebaseMemoryCli(
                executable=self.executable,
                cache_dir=self.cache_dir,
                allowed_root=self.allowed_root,
            ).configure()

    def tool(self, name: str, arguments: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        if name not in self._ALLOWED_TOOLS:
            raise ApiMemoryError(f"codebase-memory tool is not allowed: {name}")
        timeout_seconds = timeout or self.timeout_seconds
        attempts = 2 if name in self._READ_ONLY_TOOLS else 1
        last_error: ApiMemoryError | None = None
        for _ in range(attempts):
            try:
                with self._lock:
                    return self._tool_locked(name, arguments, timeout_seconds)
            except ApiMemoryError as exc:
                last_error = exc
                with self._lock:
                    self._close_locked()
        assert last_error is not None
        raise last_error

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _tool_locked(self, name: str, arguments: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        self._ensure_started_locked(timeout_seconds)
        response = self._request_locked(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout_seconds,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise ApiMemoryError(f"codebase-memory returned no result for {name}")
        if result.get("isError") is True:
            raise ApiMemoryError(f"codebase-memory tool failed: {self._result_error(result)}")
        payload = result.get("structuredContent")
        if payload is None:
            payload = _mcp_text_payload(result)
        if not isinstance(payload, dict):
            raise ApiMemoryError(f"invalid codebase-memory response for {name}")
        return payload

    def _ensure_started_locked(self, timeout_seconds: float) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._close_locked()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in self._ENVIRONMENT_KEYS and value
        }
        environment["CBM_CACHE_DIR"] = str(self.cache_dir)
        if self.allowed_root is not None:
            environment["CBM_ALLOWED_ROOT"] = str(self.allowed_root)
        try:
            process = self.popen_factory(
                [str(self.executable)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(self.allowed_root or self.cache_dir),
                env=environment,
            )
        except OSError as exc:
            raise ApiMemoryError(f"unable to start codebase-memory MCP: {exc}") from exc
        if process.stdin is None or process.stdout is None or process.stderr is None:
            self._terminate_process(process)
            raise ApiMemoryError("codebase-memory MCP did not expose stdio pipes")
        import queue

        self._process = process
        responses = queue.Queue()
        self._responses = responses
        self._stderr.clear()
        threading.Thread(target=self._read_stdout, args=(process.stdout, responses), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(process.stderr,), daemon=True).start()
        try:
            initialize = self._request_locked(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "ansys-api-memory", "version": "1"},
                },
                timeout_seconds,
            )
            server_info = initialize.get("result", {}).get("serverInfo", {})
            if (
                not isinstance(server_info, dict)
                or server_info.get("name") != "codebase-memory-mcp"
                or str(server_info.get("version")) != _backend_version()
            ):
                raise ApiMemoryError("codebase-memory MCP handshake returned an unexpected server version")
            self._notify_locked("notifications/initialized", {})
            tools = self._request_locked("tools/list", {}, timeout_seconds)
            advertised = {
                str(item.get("name"))
                for item in tools.get("result", {}).get("tools", [])
                if isinstance(item, dict)
            }
            required = {"index_repository", "search_graph", "get_code_snippet", "trace_path", "search_code"}
            if not required.issubset(advertised):
                raise ApiMemoryError("codebase-memory MCP did not advertise the required read-only tools")
        except Exception:
            self._close_locked()
            raise

    def _request_locked(self, method: str, params: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        if self._process is None or self._process.stdin is None or self._responses is None:
            raise ApiMemoryError("codebase-memory MCP is not running")
        request_id = self._next_request_id
        self._next_request_id += 1
        self._write_locked({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ApiMemoryError(f"codebase-memory MCP timed out during {method}: {self._stderr_tail()}")
            try:
                response = self._responses.get(timeout=remaining)
            except Exception as exc:
                raise ApiMemoryError(f"codebase-memory MCP timed out during {method}: {self._stderr_tail()}") from exc
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise ApiMemoryError(f"codebase-memory MCP error during {method}: {response['error']}")
            return response

    def _notify_locked(self, method: str, params: dict[str, Any]) -> None:
        self._write_locked({"jsonrpc": "2.0", "method": method, "params": params})

    def _write_locked(self, message: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise ApiMemoryError("codebase-memory MCP is not running")
        try:
            self._process.stdin.write(json.dumps(message, ensure_ascii=True, separators=(",", ":")) + "\n")
            self._process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise ApiMemoryError(f"unable to write to codebase-memory MCP: {exc}") from exc

    def _read_stdout(self, stream: Any, responses: Any) -> None:
        for line in iter(stream.readline, ""):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                self._stderr.append(f"invalid stdout: {line[:200]}")
                continue
            if isinstance(payload, dict):
                responses.put(payload)

    def _read_stderr(self, stream: Any) -> None:
        for line in iter(stream.readline, ""):
            self._stderr.append(line.strip()[:1000])

    def _close_locked(self) -> None:
        process = self._process
        self._process = None
        self._responses = None
        if process is not None:
            self._terminate_process(process)

    @staticmethod
    def _terminate_process(process: Any) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt" and isinstance(process, subprocess.Popen):
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                process.wait(timeout=2)
                return
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            process.terminate()
            process.wait(timeout=2)
            return
        except (OSError, subprocess.SubprocessError):
            pass

    def _stderr_tail(self) -> str:
        return " | ".join(item for item in self._stderr if item)[-1000:] or "no stderr"

    @staticmethod
    def _result_error(result: dict[str, Any]) -> str:
        try:
            return json.dumps(result.get("content"), ensure_ascii=True)[:1000]
        except (TypeError, ValueError):
            return "unknown tool error"


class AnsysApiMemory:
    def __init__(
        self,
        *,
        knowledge_root: str | Path | None = None,
        client: Any | None = None,
        source_locator: Callable[[], list[SourcePackage]] = locate_ansys_sources,
        status_lease_seconds: float = 10,
    ) -> None:
        self.knowledge_root = Path(knowledge_root or default_knowledge_root()).resolve()
        self.source_locator = source_locator
        self.client = client or CodebaseMemoryMcpClient(cache_dir=self.knowledge_root / "cbm" / _backend_version())
        self.status_lease_seconds = status_lease_seconds
        self._status_lease: tuple[float, dict[str, Any]] | None = None
        self._status_lock = threading.RLock()

    @property
    def manifest_path(self) -> Path:
        """Return the manifest owned by the currently installed source set."""

        return self._manifest_path_for(self.source_locator())

    def prepare(self, *, force: bool = False) -> dict[str, Any]:
        packages = self.source_locator()
        manifest_path = self._manifest_path_for(packages)
        self._configure_client_for(packages)
        self._status_lease = None
        self.client.configure()
        indexed = []
        for package in packages:
            current_status = None
            if not force:
                try:
                    current_status = self.client.tool(
                        "index_status",
                        {"project": package.project},
                    )
                except ApiMemoryError:
                    # A stale project can point outside the newly allowed source root.
                    # Re-indexing the current installed package is the only recovery path.
                    current_status = None
            if force or not _index_matches_package(current_status, package):
                result = self.client.tool(
                    "index_repository",
                    {
                        "repo_path": package.source_root,
                        "name": package.project,
                        "mode": "fast",
                        "persistence": False,
                    },
                    timeout=180,
                )
            else:
                result = {"project": package.project, "status": "current"}
            verified = self.client.tool("index_status", {"project": package.project})
            if not _index_matches_package(verified, package):
                raise ApiMemoryError(f"codebase-memory index is not ready for current source: {package.key}")
            result = {**result, "index_status": verified}
            indexed.append(result)
        manifest = {
            "schema_version": 2,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "backend": {"name": "codebase-memory-mcp", "version": _backend_version()},
            "cache_dir": str(self.client.cache_dir),
            "source_set_id": manifest_path.stem,
            "packages": [item.to_dict() for item in packages],
        }
        manifest["manifest_digest"] = _digest({key: value for key, value in manifest.items() if key != "created_at"})
        _atomic_json_write(manifest_path, manifest)
        self._status_lease = None
        return {"status": "ready", "manifest": manifest, "indexes": indexed}

    def status(self, *, force_refresh: bool = False) -> dict[str, Any]:
        with self._status_lock:
            if not force_refresh and self._status_lease is not None:
                expires_at, cached = self._status_lease
                if time.monotonic() < expires_at:
                    return cached
        current = self.source_locator()
        self._configure_client_for(current)
        manifest_path = self._manifest_path_for(current)
        if not manifest_path.is_file():
            return {"status": "missing", "ready": False, "packages": [item.to_dict() for item in current]}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"status": "invalid", "ready": False, "error": str(exc)}
        recorded = {item["key"]: item for item in manifest.get("packages", []) if isinstance(item, dict)}
        stale = [
            item.key
            for item in current
            if item.key not in recorded
            or recorded[item.key].get("version") != item.version
            or recorded[item.key].get("source_digest") != item.source_digest
            or recorded[item.key].get("source_root") != item.source_root
        ]
        missing_projects = []
        stale_projects = []
        for item in current:
            try:
                project_status = self.client.tool("index_status", {"project": item.project})
            except ApiMemoryError as exc:
                return {"status": "unavailable", "ready": False, "error": str(exc), "manifest": manifest}
            if not _index_matches_package(project_status, item):
                if str(project_status.get("status", "")).casefold() == "missing":
                    missing_projects.append(item.project)
                else:
                    stale_projects.append(item.project)
        ready = not stale and not missing_projects and not stale_projects
        response = {
            "status": "ready" if ready else "stale",
            "ready": ready,
            "stale_packages": stale,
            "missing_projects": missing_projects,
            "stale_projects": stale_projects,
            "manifest": manifest,
        }
        if ready and self.status_lease_seconds > 0:
            with self._status_lock:
                self._status_lease = (time.monotonic() + self.status_lease_seconds, response)
        return response

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()

    def _configure_client_for(self, packages: list[SourcePackage]) -> None:
        self.client.allowed_root = Path(os.path.commonpath([item.source_root for item in packages])).resolve()

    def _manifest_path_for(self, packages: list[SourcePackage]) -> Path:
        identity = [
            {
                "key": item.key,
                "distribution": item.distribution,
                "source_root_digest": _canonical_source_root_digest(Path(item.source_root)),
            }
            for item in sorted(packages, key=lambda package: package.key)
        ]
        return self.knowledge_root / "manifests" / f"source-set-{_digest(identity)[:24]}.json"

    def search(self, query: str, *, package: str = "auto", limit: int = 10) -> dict[str, Any]:
        query = _bounded_text(query, "query", 300)
        limit = _bounded_limit(limit)
        manifest, packages = self._ready_packages(package)
        results = []
        for item in packages:
            payload = self.client.tool(
                "search_graph",
                {"project": item["project"], "query": query, "limit": limit},
            )
            for result in payload.get("results", [])[:limit]:
                if isinstance(result, dict):
                    results.append({"package": item["key"], "package_version": item["version"], **result})
        results = results[:limit]
        return self._evidence_response("search", {"query": query, "package": package, "limit": limit}, manifest, results)

    def inspect(
        self,
        qualified_name: str,
        *,
        package: str,
        _verified_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        qualified_name = _bounded_text(qualified_name, "qualified_name", 500)
        manifest, packages = self._ready_packages(package, status=_verified_status)
        item = packages[0]
        payload = self.client.tool(
            "get_code_snippet",
            {"project": item["project"], "qualified_name": qualified_name, "include_neighbors": False},
        )
        source = str(payload.get("source") or "")[:20000]
        payload["source"] = source
        payload.update(
            {
                "package": item["key"],
                "package_version": item["version"],
                "project": item["project"],
                "snippet_digest": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            }
        )
        response = self._evidence_response(
            "inspect",
            {"qualified_name": qualified_name, "package": package},
            manifest,
            payload,
        )
        response["operation_evidence"] = {
            "package": item["key"],
            "package_version": item["version"],
            "project": item["project"],
            "symbol": str(payload.get("qualified_name") or qualified_name),
            "source_path": str(payload.get("file_path") or ""),
            "snippet_digest": payload["snippet_digest"],
            "query_id": response["query_id"],
        }
        return response

    def trace(self, symbol: str, *, package: str, direction: str = "both", depth: int = 2) -> dict[str, Any]:
        symbol = _bounded_text(symbol, "symbol", 500)
        if direction not in {"callers", "callees", "both"}:
            raise ValueError("direction must be callers, callees, or both")
        if type(depth) is not int or not 1 <= depth <= 5:
            raise ValueError("depth must be an integer from 1 to 5")
        manifest, packages = self._ready_packages(package)
        item = packages[0]
        payload = self.client.tool(
            "trace_path",
            {"project": item["project"], "function_name": symbol, "direction": direction, "depth": depth},
        )
        return self._evidence_response(
            "trace",
            {"symbol": symbol, "package": package, "direction": direction, "depth": depth},
            manifest,
            payload,
        )

    def search_source(
        self,
        pattern: str,
        *,
        package: str = "auto",
        examples_only: bool = False,
        limit: int = 10,
    ) -> dict[str, Any]:
        pattern = _bounded_text(pattern, "pattern", 300)
        limit = _bounded_limit(limit)
        manifest, packages = self._ready_packages(package)
        results = []
        for item in packages:
            arguments: dict[str, Any] = {
                "project": item["project"],
                "pattern": pattern,
                "mode": "compact",
                "context": 2,
                "limit": limit,
            }
            if examples_only:
                arguments["path_filter"] = r"(^|/)(tests?|examples?|doc)(/|$)"
            payload = self.client.tool("search_code", arguments)
            results.append({"package": item["key"], "package_version": item["version"], "result": payload})
        return self._evidence_response(
            "examples" if examples_only else "source_search",
            {"pattern": pattern, "package": package, "limit": limit},
            manifest,
            results,
        )

    def _ready_packages(
        self,
        package: str,
        *,
        status: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        status = status or self.status()
        if not status.get("ready"):
            raise ApiMemoryError(f"Ansys API memory is not current: {status.get('status')}")
        manifest = status["manifest"]
        packages = list(manifest["packages"])
        if package != "auto":
            if package not in {"pyaedt", "pyedb"}:
                raise ValueError("package must be auto, pyaedt, or pyedb")
            packages = [item for item in packages if item["key"] == package]
        if not packages:
            raise ApiMemoryError(f"Ansys API package is not indexed: {package}")
        return manifest, packages

    @staticmethod
    def _evidence_response(kind: str, request: dict[str, Any], manifest: dict[str, Any], results: Any) -> dict[str, Any]:
        identity = {
            "kind": kind,
            "request": request,
            "manifest_digest": manifest["manifest_digest"],
            "packages": [
                {"key": item["key"], "version": item["version"], "source_digest": item["source_digest"]}
                for item in manifest["packages"]
            ],
        }
        return {
            "query_id": "query-" + _digest({**identity, "results": results})[:24],
            **identity,
            "results": results,
        }


def _find_executable(value: str | Path | None) -> Path:
    candidates = []
    if value:
        candidates.append(Path(value))
    suffix = ".exe" if os.name == "nt" else ""
    candidates.append(Path(sys.executable).resolve().parent / f"codebase-memory-mcp{suffix}")
    discovered = shutil.which("codebase-memory-mcp")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ApiMemoryError("codebase-memory-mcp executable was not found; install the knowledge extra")


def _mcp_text_payload(result: dict[str, Any]) -> Any:
    content = result.get("content")
    if not isinstance(content, list):
        return None
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    return None


def _index_matches_package(status: Any, package: SourcePackage) -> bool:
    if not isinstance(status, dict) or str(status.get("status", "")).casefold() != "ready":
        return False
    root_path = status.get("root_path")
    if not isinstance(root_path, str) or not root_path.strip():
        return False
    try:
        indexed_root = os.path.normcase(str(Path(root_path).resolve()))
        package_root = os.path.normcase(str(Path(package.source_root).resolve()))
    except (OSError, ValueError):
        return False
    return indexed_root == package_root


def _canonical_source_root(root: Path) -> str:
    resolved = root.expanduser().resolve()
    return os.path.normcase(os.path.normpath(str(resolved)))


def _canonical_source_root_digest(root: Path) -> str:
    return hashlib.sha256(_canonical_source_root(root).encode("utf-8")).hexdigest()


def _codebase_project_name(
    *,
    key: str,
    version: str,
    source_digest: str,
    source_root: Path,
) -> str:
    """Build a bounded, opaque project identity for one physical installation."""

    safe_key = re.sub(r"[^A-Za-z0-9._-]+", "-", key).strip("-")[:16] or "package"
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", version).strip("-")[:24] or "unknown"
    root_digest = _canonical_source_root_digest(source_root)
    identity_digest = _digest(
        {
            "key": key,
            "version": version,
            "source_digest": source_digest,
            "source_root_digest": root_digest,
        }
    )
    # Keep the name under common backend identifier limits without exposing a path.
    return (
        f"ansys-{safe_key}-{safe_version}-{source_digest[:12]}-"
        f"{root_digest[:12]}-{identity_digest[:10]}"
    )


def _backend_version() -> str:
    try:
        return metadata.version("codebase-memory-mcp")
    except metadata.PackageNotFoundError:
        return "unknown"


def _bounded_text(value: str, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > maximum:
        raise ValueError(f"{name} must contain at most {maximum} characters")
    return value


def _bounded_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 25:
        raise ValueError("limit must be an integer from 1 to 25")
    return value


def _digest(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    temporary.replace(path)
