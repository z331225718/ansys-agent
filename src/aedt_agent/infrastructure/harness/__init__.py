"""Controlled worker process execution contracts and adapters."""

from aedt_agent.infrastructure.harness.contracts import (
    HARNESS_PROTOCOL_VERSION,
    HarnessError,
    HarnessProtocolError,
    HarnessRequest,
    HarnessResult,
    HarnessStatus,
)
from aedt_agent.infrastructure.harness.workspace import (
    HarnessWorkspace,
    HarnessWorkspaceError,
    HarnessWorkspacePolicy,
    build_child_environment,
)
from aedt_agent.infrastructure.harness.resources import (
    CompositeResourceLease,
    ResourceAcquireTimeout,
    ResourceGate,
    ResourceLease,
)
from aedt_agent.infrastructure.harness.local_process import (
    LocalProcessHarness,
    ProcessTreeController,
)
from aedt_agent.infrastructure.harness.recovery import (
    HarnessRecoveryClassification,
    HarnessRecoveryRecord,
    HarnessRecoveryScanner,
)

__all__ = [
    "HARNESS_PROTOCOL_VERSION",
    "HarnessError",
    "HarnessProtocolError",
    "HarnessRequest",
    "HarnessRecoveryClassification",
    "HarnessRecoveryRecord",
    "HarnessRecoveryScanner",
    "HarnessResult",
    "HarnessStatus",
    "HarnessWorkspace",
    "HarnessWorkspaceError",
    "HarnessWorkspacePolicy",
    "LocalProcessHarness",
    "ProcessTreeController",
    "CompositeResourceLease",
    "ResourceAcquireTimeout",
    "ResourceGate",
    "ResourceLease",
    "build_child_environment",
]
