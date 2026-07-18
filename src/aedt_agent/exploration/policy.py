from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

from aedt_agent.exploration.contracts import ApiEvidence, ExplorationError, OperationStep


_PUBLIC_PATH = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*")
_FORBIDDEN_SEGMENTS = {
    "odesktop",
    "odesign",
    "oeditor",
    "oproject",
    "oboundary",
    "omodule",
    "logger",
    "settings",
    "__class__",
    "__dict__",
    "__globals__",
    "__subclasses__",
}
_FORBIDDEN_WORDS = {
    "save",
    "close",
    "quit",
    "release",
    "delete",
    "remove",
    "analyze",
    "analyse",
    "solve",
    "stop_simulations",
    "export",
    "import",
    "execute",
    "eval",
    "exec",
    "run_program",
    "system",
    "subprocess",
    "socket",
    "open_file",
}
_SAFE_READ_CALLS = frozenset(
    {
        # Each entry is source-reviewed for the exact installed API version. New
        # versions and methods stay unavailable until they are audited here.
        ("pyaedt", "1.3.0", "Desktop", "get_available_toolkits"),
    }
)


class ExplorationPolicy:
    version = "2"

    @property
    def digest(self) -> str:
        payload = {
            "version": self.version,
            "forbidden_segments": sorted(_FORBIDDEN_SEGMENTS),
            "forbidden_words": sorted(_FORBIDDEN_WORDS),
            "safe_read_calls": [list(item) for item in sorted(_SAFE_READ_CALLS)],
            "write_operation": "set_attr_with_server_snapshot_only",
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def validate_path(self, path: str) -> None:
        if not _PUBLIC_PATH.fullmatch(path):
            raise ExplorationError("path_forbidden", f"object path is not a public dotted path: {path}")
        segments = [segment.lower() for segment in path.split(".")]
        if any(segment.startswith("_") for segment in segments):
            raise ExplorationError("path_forbidden", "private attributes are forbidden")
        if any(segment in _FORBIDDEN_SEGMENTS for segment in segments):
            raise ExplorationError("path_forbidden", "raw COM and runtime internals are forbidden")
        if any(segment in _FORBIDDEN_WORDS for segment in segments):
            raise ExplorationError("operation_forbidden", f"dangerous operation path is forbidden: {path}")

    def evidence_bindings(
        self,
        path: str,
        evidence: Iterable[ApiEvidence],
    ) -> list[dict[str, str]]:
        """Return source identities whose class member matches an operation path."""
        member = path.rsplit(".", 1)[-1]
        bindings = []
        for item in evidence:
            owner, evidenced_member = _symbol_owner_member(item.symbol)
            if owner is None or evidenced_member.lower() != member.lower():
                continue
            bindings.append(
                {
                    "package": item.package,
                    "package_version": item.package_version,
                    "owner": owner,
                    "member": evidenced_member,
                    "symbol": item.symbol,
                    "query_id": item.query_id,
                    "snippet_digest": item.snippet_digest,
                }
            )
        return bindings

    def classify(
        self,
        step: OperationStep,
        *,
        evidence_bindings: Iterable[dict[str, Any]] = (),
    ) -> str:
        self.validate_path(step.path)
        if step.op == "read_attr":
            return "read_only"
        if step.op == "set_attr":
            return "reversible_edit"
        if step.op == "call":
            approved = {
                (
                    str(item.get("package", "")),
                    str(item.get("package_version", "")),
                    str(item.get("owner", "")),
                    str(item.get("member", "")),
                )
                for item in evidence_bindings
            }
            if approved.intersection(_SAFE_READ_CALLS):
                return "read_only"
        raise ExplorationError(
            "operation_unclassified",
            "method calls require an exact package/version/class/member entry in the audited "
            f"read-only allowlist: {step.path}",
        )


def _symbol_owner_member(symbol: str) -> tuple[str | None, str]:
    parts = [part for part in str(symbol).split(".") if part]
    if len(parts) < 2:
        return None, parts[-1] if parts else ""
    return parts[-2], parts[-1]
