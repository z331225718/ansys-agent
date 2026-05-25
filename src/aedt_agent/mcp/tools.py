from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from aedt_agent.mcp.audit_log import AuditLogger
from aedt_agent.mcp.ast_guard import AstGuard
from aedt_agent.mcp.execution_queue import ExecutionQueue
from aedt_agent.mcp.fake_aedt import FakeAedtAdapter
from aedt_agent.mcp.node_executor import NodeExecutor
from aedt_agent.mcp.node_schemas import describe_node_schema
from aedt_agent.mcp.pyaedt_adapter import PyaedtAdapter
from aedt_agent.mcp.session_manager import SessionManager
from aedt_agent.mcp.types import ExecutionResult, ExecutionStatus
from aedt_agent.nodes.registry import NodeRegistry


class McpToolKernel:
    def __init__(
        self,
        registry: NodeRegistry,
        session_manager: SessionManager,
        node_executor: NodeExecutor,
        queue: ExecutionQueue,
        ast_guard: AstGuard,
        dev_mode: bool = False,
        include_experimental: bool = False,
    ) -> None:
        self.registry = registry
        self.session_manager = session_manager
        self.node_executor = node_executor
        self.queue = queue
        self.ast_guard = ast_guard
        self.dev_mode = dev_mode
        self.include_experimental = include_experimental

    def create_session(self, project_id: str, design_id: str) -> dict[str, str]:
        session = self.session_manager.create_session(project_id, design_id)
        return asdict(session.ref)

    def release_session(self, session_id: str) -> dict[str, str]:
        self.session_manager.release_session(session_id)
        return {"released": session_id}

    def list_available_nodes(self) -> list[str]:
        return [node.node_id for node in self.registry.list_nodes(include_experimental=self.include_experimental)]

    def describe_node(self, node_id: str) -> dict[str, Any]:
        node = self.registry.get(node_id)
        description = describe_node_schema(node_id)
        description["summary"] = node.summary
        description["allowed_apis"] = node.allowed_apis
        description["status"] = node.status
        description["track"] = node.track
        return description

    def execute_node(self, node_id: str, inputs: dict[str, Any], session_id: str) -> ExecutionResult:
        return self.node_executor.execute_node(session_id=session_id, node_id=node_id, inputs=inputs)

    def get_model_info(self, session_id: str) -> dict[str, Any]:
        return self.session_manager.snapshot(session_id)

    def execute_script_restricted(self, code: str, session_id: str) -> ExecutionResult:
        if not self.dev_mode:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                transaction_id=f"txn-{uuid4().hex}",
                error_type="DevModeDisabled",
                error_message="execute_script_restricted is only available in dev mode",
            )
        guard = self.ast_guard.validate(code)
        if not guard.passed:
            return ExecutionResult(
                status=ExecutionStatus.REJECTED,
                transaction_id=f"txn-{uuid4().hex}",
                error_type="AstGuardViolation",
                error_message="; ".join(guard.violations),
            )
        session = self.session_manager.get_session(session_id)
        return self.queue.submit_callable(
            session=session.ref,
            fn=lambda: session.adapter.execute_node_callable(lambda app: _exec_dev_code(app, code)),
            node_id=None,
        )


def create_fake_kernel(
    node_catalog_dir: Path,
    audit_path: Path | None = None,
    dev_mode: bool = False,
    include_experimental: bool = False,
) -> McpToolKernel:
    registry = NodeRegistry.from_directory(node_catalog_dir)
    session_manager = SessionManager(lambda project_id, design_id: FakeAedtAdapter(project_id, design_id))
    queue = ExecutionQueue(timeout_seconds=5.0)
    node_executor = NodeExecutor(
        registry=registry,
        session_manager=session_manager,
        queue=queue,
        audit_logger=None if audit_path is None else AuditLogger(audit_path),
    )
    return McpToolKernel(
        registry=registry,
        session_manager=session_manager,
        node_executor=node_executor,
        queue=queue,
        ast_guard=AstGuard(),
        dev_mode=dev_mode,
        include_experimental=include_experimental,
    )


def create_real_kernel(
    node_catalog_dir: Path,
    audit_path: Path | None = None,
    dev_mode: bool = False,
    timeout_seconds: float = 120.0,
    version: str | None = None,
    non_graphical: bool = True,
    ansysem_root: str = "",
    awp_root: str = "",
    include_experimental: bool = False,
) -> McpToolKernel:
    registry = NodeRegistry.from_directory(node_catalog_dir)

    def adapter_factory(project_id: str, design_id: str) -> PyaedtAdapter:
        return PyaedtAdapter(
            project_id=project_id,
            design_id=design_id,
            version=version or os.environ.get("AEDT_AGENT_AEDT_VERSION", "2026.1"),
            non_graphical=non_graphical,
            ansysem_root=ansysem_root or os.environ.get("AEDT_AGENT_ANSYSEM_ROOT", ""),
            awp_root=awp_root or os.environ.get("AEDT_AGENT_AWP_ROOT", ""),
        )

    session_manager = SessionManager(adapter_factory)
    queue = ExecutionQueue(timeout_seconds=timeout_seconds)
    node_executor = NodeExecutor(
        registry=registry,
        session_manager=session_manager,
        queue=queue,
        audit_logger=None if audit_path is None else AuditLogger(audit_path),
    )
    return McpToolKernel(
        registry=registry,
        session_manager=session_manager,
        node_executor=node_executor,
        queue=queue,
        ast_guard=AstGuard(),
        dev_mode=dev_mode,
        include_experimental=include_experimental,
    )


def create_kernel(
    adapter: str,
    node_catalog_dir: Path,
    audit_path: Path | None = None,
    dev_mode: bool = False,
    timeout_seconds: float = 120.0,
    version: str | None = None,
    non_graphical: bool = True,
    ansysem_root: str = "",
    awp_root: str = "",
    include_experimental: bool = False,
) -> McpToolKernel:
    if adapter == "fake":
        return create_fake_kernel(
            node_catalog_dir=node_catalog_dir,
            audit_path=audit_path,
            dev_mode=dev_mode,
            include_experimental=include_experimental,
        )
    if adapter == "real":
        return create_real_kernel(
            node_catalog_dir=node_catalog_dir,
            audit_path=audit_path,
            dev_mode=dev_mode,
            timeout_seconds=timeout_seconds,
            version=version,
            non_graphical=non_graphical,
            ansysem_root=ansysem_root,
            awp_root=awp_root,
            include_experimental=include_experimental,
        )
    raise ValueError("adapter must be fake or real")


def _exec_dev_code(app: Any, code: str) -> dict[str, Any]:
    namespace = {"app": app}
    exec(code, {"__builtins__": {}}, namespace)
    return {"dev_script_executed": True}
