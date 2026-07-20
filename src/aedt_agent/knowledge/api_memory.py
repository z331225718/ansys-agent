from __future__ import annotations

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


class AnsysApiMemory:
    def __init__(
        self,
        *,
        knowledge_root: str | Path | None = None,
        client: CodebaseMemoryCli | None = None,
        source_locator: Callable[[], list[SourcePackage]] = locate_ansys_sources,
    ) -> None:
        self.knowledge_root = Path(knowledge_root or default_knowledge_root()).resolve()
        self.source_locator = source_locator
        self.client = client or CodebaseMemoryCli(cache_dir=self.knowledge_root / "cbm" / _backend_version())

    @property
    def manifest_path(self) -> Path:
        """Return the manifest owned by the currently installed source set."""

        return self._manifest_path_for(self.source_locator())

    def prepare(self, *, force: bool = False) -> dict[str, Any]:
        packages = self.source_locator()
        manifest_path = self._manifest_path_for(packages)
        common_root = Path(os.path.commonpath([item.source_root for item in packages])).resolve()
        self.client.allowed_root = common_root
        self.client.configure()
        projects = self.client.tool("list_projects", {}).get("projects", [])
        known = {str(item.get("name")) for item in projects if isinstance(item, dict)}
        indexed = []
        for package in packages:
            current_status = None
            if not force and package.project in known:
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
        return {"status": "ready", "manifest": manifest, "indexes": indexed}

    def status(self) -> dict[str, Any]:
        current = self.source_locator()
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
        try:
            projects = self.client.tool("list_projects", {}).get("projects", [])
            known = {str(item.get("name")) for item in projects if isinstance(item, dict)}
        except ApiMemoryError as exc:
            return {"status": "unavailable", "ready": False, "error": str(exc), "manifest": manifest}
        missing_projects = [item.project for item in current if item.project not in known]
        stale_projects = []
        for item in current:
            if item.project in missing_projects:
                continue
            try:
                project_status = self.client.tool("index_status", {"project": item.project})
            except ApiMemoryError as exc:
                return {"status": "unavailable", "ready": False, "error": str(exc), "manifest": manifest}
            if not _index_matches_package(project_status, item):
                stale_projects.append(item.project)
        ready = not stale and not missing_projects and not stale_projects
        return {
            "status": "ready" if ready else "stale",
            "ready": ready,
            "stale_packages": stale,
            "missing_projects": missing_projects,
            "stale_projects": stale_projects,
            "manifest": manifest,
        }

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
