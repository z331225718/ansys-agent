from __future__ import annotations

import inspect
from typing import Any, Callable

from aedt_agent.exploration.contracts import ExplorationError


class ApiMemoryEvidenceVerifier:
    """Re-query local API Memory so a Runtime plan cannot invent source evidence."""

    def __init__(self, memory_factory: Callable[[], Any] | None = None) -> None:
        if memory_factory is None:
            from aedt_agent.knowledge.api_memory import AnsysApiMemory

            memory_factory = AnsysApiMemory
        self.memory_factory = memory_factory

    def verify(self, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            memory = self.memory_factory()
            status_method = memory.status
            status_parameters = inspect.signature(status_method).parameters
            status = (
                status_method(force_refresh=True)
                if "force_refresh" in status_parameters
                else status_method()
            )
        except Exception as exc:
            raise ExplorationError(
                "evidence_unavailable",
                f"Ansys API Memory could not verify evidence: {type(exc).__name__}: {exc}",
            ) from exc
        if not isinstance(status, dict) or status.get("ready") is not True:
            state = status.get("status") if isinstance(status, dict) else "invalid"
            raise ExplorationError(
                "evidence_unavailable",
                f"Ansys API Memory is not ready: {state}",
            )
        manifest = status.get("manifest") if isinstance(status.get("manifest"), dict) else {}
        packages = {
            str(item.get("key")): item
            for item in manifest.get("packages", [])
            if isinstance(item, dict) and item.get("key")
        }
        verified = []
        for item in evidence:
            package = str(item["package"])
            current = packages.get(package)
            if (
                current is None
                or str(current.get("version")) != str(item["package_version"])
                or str(current.get("project")) != str(item["project"])
            ):
                raise ExplorationError(
                    "evidence_stale",
                    f"{package} evidence does not match the current API Memory manifest",
                )
            try:
                inspect_method = memory.inspect
                parameters = inspect.signature(inspect_method).parameters
                if "_verified_status" in parameters:
                    inspected = inspect_method(
                        str(item["symbol"]),
                        package=package,
                        _verified_status=status,
                    )
                else:
                    inspected = inspect_method(str(item["symbol"]), package=package)
            except Exception as exc:
                raise ExplorationError(
                    "evidence_unverified",
                    f"API Memory could not reproduce evidence for {item['symbol']}: {type(exc).__name__}: {exc}",
                ) from exc
            canonical = inspected.get("operation_evidence")
            if not isinstance(canonical, dict):
                raise ExplorationError(
                    "evidence_unverified",
                    "API Memory inspect response did not contain operation_evidence",
                )
            fields = (
                "package",
                "package_version",
                "project",
                "symbol",
                "source_path",
                "snippet_digest",
                "query_id",
            )
            mismatch = next(
                (field for field in fields if str(canonical.get(field)) != str(item.get(field))),
                None,
            )
            if mismatch is not None:
                raise ExplorationError(
                    "evidence_unverified",
                    f"API Memory evidence mismatch: {mismatch}",
                )
            verified.append(
                {
                    "package": package,
                    "package_version": str(item["package_version"]),
                    "symbol": str(item["symbol"]),
                    "query_id": str(item["query_id"]),
                    "snippet_digest": str(item["snippet_digest"]),
                }
            )
        return {
            "status": "verified",
            "manifest_digest": str(manifest.get("manifest_digest") or ""),
            "evidence": verified,
        }
