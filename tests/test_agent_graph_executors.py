from __future__ import annotations

from aedt_agent.agent.approvals import ApprovalService
from aedt_agent.agent.graph_executors import (
    GraphNodeExecutionContext,
    GraphNodeExecutorRegistry,
    execute_graph_node,
)
from aedt_agent.agent.graph_template import GraphNode, GraphTemplate
from aedt_agent.agent.handoff import HandoffSchema
from aedt_agent.agent.mission import (
    GraphRunRecord,
    JobStatus,
    MissionRecord,
    NodeRunRecord,
    NodeRunStatus,
)
from aedt_agent.agent.orchestrator import AgentRuntime
from aedt_agent.agent.workers import InMemoryWorkerRegistry
from aedt_agent.infrastructure import SQLiteMissionStore


def _runtime(tmp_path, capability="fake.worker", worker=None):
    registry = InMemoryWorkerRegistry()
    if worker is not None:
        registry.register(capability, worker)
    store = SQLiteMissionStore(tmp_path / "mission.db")
    runtime = AgentRuntime(store, registry=registry)
    mission = runtime.create_mission("goal", [], [])
    graph_run = store.create_graph_run(
        GraphRunRecord.create("graph-1", mission.mission_id, "test", 1, 1)
    )
    return runtime, mission, graph_run


def _template(node, schemas=None):
    return GraphTemplate("test", 1, "", [node], [], schemas or {})


def _context(runtime, graph_run, node, template, payload, *, status=NodeRunStatus.CREATED):
    node_run = NodeRunRecord.create(
        "node-run-1",
        graph_run.graph_run_id,
        graph_run.mission_id,
        node.node_id,
        node.role,
        node.kind,
        1,
        payload,
    )
    if status == NodeRunStatus.WAITING_APPROVAL:
        node_run = node_run.with_completion(status, payload, [])
    return GraphNodeExecutionContext(
        runtime=runtime,
        graph_run=graph_run,
        node_run=node_run,
        node=node,
        template=template,
        input_payload=payload,
        run_index=1,
        worker_id="graph",
    )


def test_planner_uses_initial_payload_without_claiming_llm_call(tmp_path):
    runtime, _, graph_run = _runtime(tmp_path)
    node = GraphNode("planner", "planner", "llm", output_schema="request")
    template = _template(node, {"request": HandoffSchema("request", ["value"])})

    result = execute_graph_node(_context(runtime, graph_run, node, template, {"value": 7}))

    assert result.status == NodeRunStatus.SUCCEEDED
    assert result.outcome == "succeeded"
    assert result.output_payload["value"] == 7
    assert result.output_payload["planning_source"] == "graph_initial_payload"
    assert "llm" not in result.output_payload


def test_validator_returns_failed_result_for_invalid_handoff(tmp_path):
    runtime, _, graph_run = _runtime(tmp_path)
    node = GraphNode(
        "validator",
        "validator",
        "program",
        input_schema="request",
        output_schema="request",
    )
    template = _template(node, {"request": HandoffSchema("request", ["value"])})

    result = execute_graph_node(_context(runtime, graph_run, node, template, {}))

    assert result.status == NodeRunStatus.FAILED
    assert result.outcome == "failed"
    assert result.error["error_class"] == "handoff_validation"


def test_worker_binds_existing_matching_queued_job(tmp_path):
    runtime, mission, graph_run = _runtime(
        tmp_path,
        worker=lambda job, context: {"value": job.input_payload["value"] + 1},
    )
    job = runtime.create_job(mission.mission_id, "fake.worker", "existing", {"value": 4})
    node = GraphNode(
        "worker",
        "worker",
        "worker",
        capability="fake.worker",
        input_schema="request",
        output_schema="result",
    )
    template = _template(
        node,
        {
            "request": HandoffSchema("request", ["value"]),
            "result": HandoffSchema("result", ["value"]),
        },
    )

    result = execute_graph_node(_context(runtime, graph_run, node, template, {"value": 4}))

    assert result.status == NodeRunStatus.SUCCEEDED
    assert result.output_payload["value"] == 5
    assert runtime.store.get_graph_node_job("graph-1", "worker", 1) == job.job_id
    assert runtime.get_job(job.job_id).status == JobStatus.SUCCEEDED


def test_worker_creates_graph_scoped_job_when_matching_job_is_missing(tmp_path):
    runtime, mission, graph_run = _runtime(
        tmp_path,
        worker=lambda job, context: {"value": job.input_payload["value"]},
    )
    node = GraphNode(
        "worker",
        "worker",
        "worker",
        capability="fake.worker",
        input_schema="request",
        output_schema="result",
    )
    template = _template(
        node,
        {
            "request": HandoffSchema("request", ["value"]),
            "result": HandoffSchema("result", ["value"]),
        },
    )

    result = execute_graph_node(_context(runtime, graph_run, node, template, {"value": 8}))
    jobs = runtime.list_jobs(mission.mission_id)

    assert result.status == NodeRunStatus.SUCCEEDED
    assert len(jobs) == 1
    assert jobs[0].idempotency_key == "graph:graph-1:worker:1"


def test_scorecard_emits_evidence_and_passed_outcome(tmp_path):
    runtime, mission, graph_run = _runtime(tmp_path)
    artifact = tmp_path / "evidence.json"
    artifact.write_text("{}", encoding="utf-8")
    job = runtime.create_job(mission.mission_id, "fake.done", "done", {})
    runtime.store.complete_job(
        job.job_id,
        {"evidence_summary": {"status": "ok"}},
        [str(artifact)],
    )
    node = GraphNode(
        "scorecard",
        "scorecard",
        "program",
        output_schema="scorecard",
    )
    template = _template(
        node,
        {"scorecard": HandoffSchema("scorecard", ["status", "checks"])},
    )

    result = execute_graph_node(_context(runtime, graph_run, node, template, {}))

    assert result.status == NodeRunStatus.SUCCEEDED
    assert result.outcome == "passed"
    assert result.evidence_package_id is not None
    assert runtime.store.list_evidence_packages(mission.mission_id)


def test_human_gate_creates_approval_and_waits(tmp_path):
    runtime, mission, graph_run = _runtime(tmp_path)
    node = GraphNode("gate", "approval_gate", "human_gate")
    template = _template(node)

    result = execute_graph_node(
        _context(runtime, graph_run, node, template, {"status": "passed"})
    )

    approvals = runtime.store.list_approvals(mission.mission_id)
    assert result.status == NodeRunStatus.WAITING_APPROVAL
    assert result.outcome == "waiting_approval"
    assert len(approvals) == 1
    assert result.output_payload["approval_id"] == approvals[0].approval_id


def test_human_gate_does_not_adopt_unrelated_pending_approval(tmp_path):
    runtime, mission, graph_run = _runtime(tmp_path)
    unrelated = ApprovalService(runtime.store).request_approval(
        mission.mission_id,
        "unrelated_action",
        [{"id": "approve", "label": "Approve"}],
    )
    node = GraphNode("gate", "approval_gate", "human_gate")
    template = _template(node)

    result = execute_graph_node(
        _context(runtime, graph_run, node, template, {"status": "passed"})
    )

    approvals = runtime.store.list_approvals(mission.mission_id)
    assert result.status == NodeRunStatus.WAITING_APPROVAL
    assert result.output_payload["approval_id"] != unrelated.approval_id
    assert len(approvals) == 2


def test_human_gate_resumes_same_node_after_approval(tmp_path):
    runtime, mission, graph_run = _runtime(tmp_path)
    approval = ApprovalService(runtime.store).request_approval(
        mission.mission_id,
        "graph_gate",
        [{"id": "approve", "label": "Approve"}],
    )
    ApprovalService(runtime.store).approve(approval.approval_id, "approve")
    node = GraphNode("gate", "approval_gate", "human_gate")
    template = _template(node)
    context = _context(
        runtime,
        graph_run,
        node,
        template,
        {"approval_id": approval.approval_id},
        status=NodeRunStatus.WAITING_APPROVAL,
    )

    result = execute_graph_node(context)

    assert result.status == NodeRunStatus.SUCCEEDED
    assert result.outcome == "approved"
    assert result.output_payload["approval_id"] == approval.approval_id
    assert len(runtime.store.list_approvals(mission.mission_id)) == 1


def test_human_gate_uses_requested_reason_and_passes_validated_input(
    tmp_path,
):
    runtime, mission, graph_run = _runtime(tmp_path)
    node = GraphNode("gate", "approval_gate", "human_gate")
    template = _template(node)
    payload = {
        "project_path": "approved.aedt",
        "setup_name": "Setup1",
        "approval_reason": "approve_real_brd_solve",
        "approval_options": [
            {"id": "approve", "label": "批准真实求解"},
            {"id": "reject", "label": "拒绝真实求解"},
        ],
    }

    waiting = execute_graph_node(
        _context(runtime, graph_run, node, template, payload)
    )

    approval = runtime.store.get_approval(
        waiting.output_payload["approval_id"]
    )
    assert approval.reason == "approve_real_brd_solve"
    assert approval.options == payload["approval_options"]
    ApprovalService(runtime.store).approve(
        approval.approval_id,
        "approve",
    )
    resumed = execute_graph_node(
        _context(
            runtime,
            graph_run,
            node,
            template,
            {
                **payload,
                "approval_id": approval.approval_id,
            },
            status=NodeRunStatus.WAITING_APPROVAL,
        )
    )

    assert resumed.output_payload["project_path"] == "approved.aedt"
    assert resumed.output_payload["setup_name"] == "Setup1"
    assert "approval_reason" not in resumed.output_payload
    assert "approval_options" not in resumed.output_payload


def test_human_gate_preserves_action_digest_compatibility(tmp_path):
    runtime, mission, graph_run = _runtime(tmp_path)
    node = GraphNode("gate", "approval_gate", "human_gate")
    template = _template(node)
    payload = {
        "action_id": "action-1",
        "digest": "digest-1",
        "approval_reason": "approve_action",
        "approval_options": [
            {
                "id": "approve",
                "label": "Approve",
                "action_id": "action-1",
                "action_digest": "digest-1",
            }
        ],
    }

    result = execute_graph_node(
        _context(runtime, graph_run, node, template, payload)
    )

    assert result.output_payload["action_id"] == "action-1"
    assert result.output_payload["digest"] == "digest-1"


def test_custom_program_handler_is_dispatched_by_registry(tmp_path):
    runtime, _, graph_run = _runtime(tmp_path)
    node = GraphNode("custom", "aggregate", "program", handler="aggregate")
    template = _template(node)
    registry = GraphNodeExecutorRegistry()
    registry.register(
        "aggregate",
        lambda context: {
            "status": NodeRunStatus.SUCCEEDED,
            "outcome": "succeeded",
            "output_payload": {"total": 3},
        },
    )

    result = execute_graph_node(
        _context(runtime, graph_run, node, template, {}),
        registry=registry,
    )

    assert result.output_payload == {"total": 3}


def test_code_writer_rejects_forbidden_import_before_handoff_validation(
    tmp_path,
    monkeypatch,
):
    runtime, _, graph_run = _runtime(tmp_path)
    node = GraphNode(
        "writer",
        "worker",
        "agent",
        capability="code_writer",
        system_prompt="write code",
        output_schema="build_evidence",
        constraints={
            "allowed_imports": ["pyedb"],
            "forbidden_patterns": ["os.system"],
        },
    )
    template = GraphTemplate(
        "test",
        1,
        "",
        [node],
        [],
        {
            "build_evidence": HandoffSchema(
                "build_evidence",
                ["status", "code", "artifact_refs", "project_path", "port_count"],
            )
        },
    )

    def fake_complete(*args, **kwargs):
        return (
            '{"status":"succeeded","code":"import os\\nos.system(\\\"x\\\")",'
            '"artifact_refs":[],"project_path":"p.aedt","port_count":2}'
        )

    monkeypatch.setenv("AEDT_AGENT_LLM_API_KEY", "test")
    monkeypatch.setattr("aedt_agent.agent.llm.llm_complete", fake_complete)

    result = execute_graph_node(
        _context(runtime, graph_run, node, template, {})
    )

    assert result.status == NodeRunStatus.FAILED
    assert result.error["error_class"] == "code_agent_validation"
    assert "forbidden import" in result.error["message"]


def test_agent_node_uses_decision_as_edge_outcome(tmp_path, monkeypatch):
    runtime, _, graph_run = _runtime(tmp_path)
    node = GraphNode(
        "decider",
        "decision_maker",
        "agent",
        system_prompt="decide",
        profile="low_cost",
        constraints={"allowed_decisions": ["continue", "complete"]},
    )
    template = _template(node)

    def fake_complete(*args, **kwargs):
        return '{"decision":"continue","reason":"bounded next step"}'

    monkeypatch.setenv("AEDT_AGENT_LLM_API_KEY", "test")
    monkeypatch.setattr("aedt_agent.agent.llm.llm_complete", fake_complete)

    result = execute_graph_node(
        _context(runtime, graph_run, node, template, {"score": {"status": "fail"}})
    )

    assert result.status == NodeRunStatus.SUCCEEDED
    assert result.outcome == "continue"
    assert result.output_payload["reason"] == "bounded next step"


def test_agent_node_uses_handler_fallback_after_llm_error(tmp_path, monkeypatch):
    runtime, _, graph_run = _runtime(tmp_path)
    registry = GraphNodeExecutorRegistry()
    registry.register(
        "fallback.handler",
        lambda context: {
            "status": "succeeded",
            "outcome": "continue",
            "output_payload": {"decision": "continue", "reason": "fallback"},
            "artifact_refs": [],
        },
    )
    node = GraphNode(
        "decider",
        "decision_maker",
        "agent",
        system_prompt="decide",
        handler="fallback.handler",
        profile="low_cost",
    )
    template = _template(node)

    def fake_complete(*args, **kwargs):
        raise TimeoutError("gateway timeout")

    monkeypatch.setenv("AEDT_AGENT_LLM_API_KEY", "test")
    monkeypatch.setattr("aedt_agent.agent.llm.llm_complete", fake_complete)

    result = execute_graph_node(
        _context(runtime, graph_run, node, template, {"score": {"status": "fail"}}),
        registry=registry,
    )

    assert result.status == NodeRunStatus.SUCCEEDED
    assert result.outcome == "continue"
    assert result.output_payload["reason"] == "fallback"
    assert result.output_payload["agent_fallback"]["status"] == "used"
    assert result.output_payload["agent_fallback"]["reason"] == "gateway timeout"
