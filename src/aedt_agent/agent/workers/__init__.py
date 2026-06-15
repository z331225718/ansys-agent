"""Leaseable worker contracts and adapters."""

from aedt_agent.agent.workers.brd_action_propose import (
    BRD_ACTION_PROPOSE_CAPABILITY,
    build_action_propose_job_input,
    run_action_propose_worker,
)
from aedt_agent.agent.workers.brd_channel_score import (
    BRD_CHANNEL_SCORE_CAPABILITY,
    build_brd_channel_score_job_input,
    run_brd_channel_score_worker,
)
from aedt_agent.agent.workers.brd_evidence_compare import (
    BRD_EVIDENCE_COMPARE_CAPABILITY,
    build_evidence_compare_job_input,
    run_evidence_compare_worker,
)
from aedt_agent.agent.workers.brd_local_cut import (
    BRD_LOCAL_CUT_BUILD_CAPABILITY,
    build_brd_local_cut_job_input,
    run_brd_local_cut_worker,
)
from aedt_agent.agent.workers.brd_recorded_void_action import (
    BRD_RECORDED_VOID_ACTION_CAPABILITY,
    build_brd_recorded_void_action_job_input,
    run_brd_recorded_void_action_worker,
)
from aedt_agent.agent.workers.brd_real_solve import (
    BRD_REAL_SOLVE_CAPABILITY,
    build_brd_real_solve_job_input,
    run_brd_real_solve_worker,
)
from aedt_agent.agent.workers.registry import (
    InMemoryWorkerRegistry,
    WorkerContext,
    WorkerExecutionResult,
    WorkerReportedError,
    WorkerRegistration,
    classify_worker_error,
)

__all__ = [
    "BRD_ACTION_PROPOSE_CAPABILITY",
    "BRD_CHANNEL_SCORE_CAPABILITY",
    "BRD_EVIDENCE_COMPARE_CAPABILITY",
    "BRD_LOCAL_CUT_BUILD_CAPABILITY",
    "BRD_RECORDED_VOID_ACTION_CAPABILITY",
    "BRD_REAL_SOLVE_CAPABILITY",
    "InMemoryWorkerRegistry",
    "WorkerContext",
    "WorkerExecutionResult",
    "WorkerReportedError",
    "WorkerRegistration",
    "build_action_propose_job_input",
    "build_brd_channel_score_job_input",
    "build_brd_local_cut_job_input",
    "build_brd_recorded_void_action_job_input",
    "build_brd_real_solve_job_input",
    "build_evidence_compare_job_input",
    "classify_worker_error",
    "run_action_propose_worker",
    "run_brd_channel_score_worker",
    "run_brd_local_cut_worker",
    "run_brd_recorded_void_action_worker",
    "run_brd_real_solve_worker",
    "run_evidence_compare_worker",
]
