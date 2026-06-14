from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

from aedt_agent.agent.graph_executors import (
    GraphNodeExecutionContext,
    GraphNodeExecutionResult,
    GraphNodeExecutorRegistry,
    execute_graph_node,
)
from aedt_agent.agent.graph_scheduler import ReadyNode, ready_nodes
from aedt_agent.agent.graph_template import GraphTemplate, graph_template_from_mapping
from aedt_agent.agent.mission import (
    GraphHandoffRecord,
    GraphHandoffStatus,
    GraphRunRecord,
    GraphRunStatus,
    JobStatus,
    MissionState,
    NodeRunRecord,
    NodeRunStatus,
)


def create_graph_run(
    runtime,
    mission_id: str,
    template: GraphTemplate,
    *,
    initial_payload: dict[str, Any] | None = None,
    max_steps: int = 32,
) -> GraphRunRecord:
    mission = runtime.get_mission(mission_id)
    allowed_capabilities = {
        node.capability for node in template.nodes if node.kind == "worker"
    }
    unsupported_jobs = [
        job.capability
        for job in runtime.list_jobs(mission_id)
        if job.status == JobStatus.QUEUED and job.capability not in allowed_capabilities
    ]
    if unsupported_jobs:
        raise ValueError(
            "queued job capability is not allowed by graph template: "
            + ", ".join(sorted(set(unsupported_jobs)))
        )
    seed = dict(initial_payload) if initial_payload is not None else _derive_initial_payload(runtime, mission_id)
    graph_run = runtime.store.create_graph_run(
        GraphRunRecord.create(
            graph_run_id=str(uuid4()),
            mission_id=mission_id,
            template_id=template.template_id,
            template_version=template.version,
            plan_version=mission.plan_version,
            template_snapshot=template.to_json_dict(),
            initial_payload=seed,
            max_steps=max_steps,
        )
    )
    return runtime.store.update_graph_run_status(graph_run.graph_run_id, GraphRunStatus.RUNNING)


def advance_graph(
    runtime,
    graph_run_id: str,
    *,
    worker_id: str = "graph",
    max_workers: int = 4,
    registry: GraphNodeExecutorRegistry | None = None,
) -> dict[str, Any]:
    graph_run = _require_graph_run(runtime, graph_run_id)
    if graph_run.status in {
        GraphRunStatus.SUCCEEDED,
        GraphRunStatus.FAILED,
        GraphRunStatus.CANCELED,
    }:
        return graph_status(runtime, graph_run_id)
    template = graph_template_from_mapping(
        graph_run.template_snapshot,
        source=f"graph run {graph_run_id} snapshot",
    )

    waiting_runs = [
        run
        for run in runtime.store.list_node_runs(graph_run_id)
        if run.status == NodeRunStatus.WAITING_APPROVAL
    ]
    if waiting_runs:
        return _resume_waiting_gates(runtime, graph_run, template, waiting_runs, worker_id, registry)

    if graph_run.step_count >= graph_run.max_steps:
        return _fail_graph(
            runtime,
            graph_run,
            "graph_step_limit",
            f"graph max_steps reached: {graph_run.max_steps}",
        )

    node_runs = runtime.store.list_node_runs(graph_run_id)
    pending = runtime.store.list_graph_handoffs(
        graph_run_id,
        status=GraphHandoffStatus.PENDING,
    )
    ready = ready_nodes(
        template,
        node_runs,
        pending,
        initial_payload=graph_run.initial_payload,
    )
    if not ready:
        return _settle_graph(runtime, graph_run, template, node_runs, pending)

    created = _create_wave_node_runs(runtime, graph_run, ready, node_runs)
    worker_ready = [item for item in ready if item.node.kind == "worker"]
    if worker_ready:
        _prepare_mission_for_workers(runtime, graph_run.mission_id)
    results = _execute_wave(
        runtime,
        graph_run,
        template,
        ready,
        created,
        worker_id=worker_id,
        max_workers=max_workers,
        registry=registry,
    )

    unmatched_failure: tuple[NodeRunRecord, GraphNodeExecutionResult] | None = None
    waiting_for_approval = False
    edge_error: dict[str, Any] | None = None
    for item, node_run, result in zip(ready, created, results, strict=True):
        if result.status == NodeRunStatus.WAITING_APPROVAL:
            runtime.store.update_node_run_status(
                node_run.node_run_id,
                NodeRunStatus.WAITING_APPROVAL,
                output_payload=result.output_payload,
                edge_decision=result.outcome,
                error=result.error,
            )
            waiting_for_approval = True
        else:
            runtime.store.complete_node_run(
                node_run.node_run_id,
                result.status,
                output_payload=result.output_payload,
                artifact_refs=result.artifact_refs,
                evidence_package_id=result.evidence_package_id,
                edge_decision=result.outcome,
                error=result.error,
            )
            matching_edges = [
                edge
                for edge in template.edges
                if edge.from_node == item.node.node_id and edge.on == result.outcome
            ]
            if result.outcome in {"failed", "rejected"} and not matching_edges:
                unmatched_failure = (node_run, result)
            for edge in matching_edges:
                error = _create_edge_handoff(
                    runtime,
                    graph_run,
                    edge,
                    node_run,
                    result.output_payload,
                )
                if error is not None:
                    edge_error = error
                    break
        runtime.store.consume_graph_handoffs(item.handoff_ids, node_run.node_run_id)
        if edge_error is not None:
            break

    graph_run = runtime.store.increment_graph_step(graph_run_id)
    if edge_error is not None:
        return _fail_graph(
            runtime,
            graph_run,
            edge_error["code"],
            edge_error["message"],
        )
    if unmatched_failure is not None:
        node_run, result = unmatched_failure
        return _fail_graph(
            runtime,
            graph_run,
            "unhandled_node_outcome",
            f"node {node_run.node_id} emitted {result.outcome} without a matching edge",
            details={"node_run_id": node_run.node_run_id, "error": result.error},
        )
    if waiting_for_approval:
        runtime.store.update_graph_run_status(
            graph_run_id,
            GraphRunStatus.WAITING_APPROVAL,
            current_node_id=next(
                node_run.node_id
                for node_run, result in zip(created, results, strict=True)
                if result.status == NodeRunStatus.WAITING_APPROVAL
            ),
        )
        return graph_status(runtime, graph_run_id)

    refreshed_runs = runtime.store.list_node_runs(graph_run_id)
    refreshed_pending = runtime.store.list_graph_handoffs(
        graph_run_id,
        status=GraphHandoffStatus.PENDING,
    )
    return _settle_graph(runtime, graph_run, template, refreshed_runs, refreshed_pending)


def run_graph(
    runtime,
    mission_id: str,
    template: GraphTemplate,
    *,
    initial_payload: dict[str, Any] | None = None,
    max_steps: int = 32,
    worker_id: str = "graph",
    max_workers: int = 4,
    registry: GraphNodeExecutorRegistry | None = None,
) -> dict[str, Any]:
    graph_run = create_graph_run(
        runtime,
        mission_id,
        template,
        initial_payload=initial_payload,
        max_steps=max_steps,
    )
    return _run_until_blocked(
        runtime,
        graph_run.graph_run_id,
        worker_id=worker_id,
        max_workers=max_workers,
        registry=registry,
    )


def resume_graph(
    runtime,
    graph_run_id: str,
    *,
    worker_id: str = "graph-resume",
    max_workers: int = 4,
    registry: GraphNodeExecutorRegistry | None = None,
) -> dict[str, Any]:
    graph_run = _require_graph_run(runtime, graph_run_id)
    if graph_run.status == GraphRunStatus.WAITING_APPROVAL:
        runtime.store.update_graph_run_status(graph_run_id, GraphRunStatus.RUNNING)
    return _run_until_blocked(
        runtime,
        graph_run_id,
        worker_id=worker_id,
        max_workers=max_workers,
        registry=registry,
    )


def graph_status(runtime, graph_run_id: str) -> dict[str, Any]:
    graph_run = _require_graph_run(runtime, graph_run_id)
    node_runs = runtime.store.list_node_runs(graph_run_id)
    handoffs = runtime.store.list_graph_handoffs(graph_run_id)
    return {
        "status": graph_run.status.value,
        "template_id": graph_run.template_id,
        "mission_id": graph_run.mission_id,
        "graph_run": graph_run.to_json_dict(),
        "node_runs": [run.to_json_dict() for run in node_runs],
        "handoffs": [handoff.to_json_dict() for handoff in handoffs],
        "jobs": [job.to_json_dict() for job in runtime.list_jobs(graph_run.mission_id)],
    }


def run_graph_once(
    runtime,
    mission_id: str,
    template: GraphTemplate,
    *,
    worker_id: str = "graph",
) -> dict[str, Any]:
    report = run_graph(runtime, mission_id, template, worker_id=worker_id)
    compatibility = dict(report)
    compatibility["status"] = (
        "passed"
        if report["status"] == GraphRunStatus.SUCCEEDED.value
        else report["status"]
    )
    worker_runs = [run for run in report["node_runs"] if run["node_kind"] == "worker"]
    if worker_runs:
        worker_run = worker_runs[-1]
        compatibility["executed_node"] = template.node(worker_run["node_id"]).to_json_dict()
        bound_job_id = runtime.store.get_graph_node_job(
            report["graph_run"]["graph_run_id"],
            worker_run["node_id"],
            len([run for run in worker_runs if run["node_id"] == worker_run["node_id"]]),
        )
        if bound_job_id is not None:
            job = runtime.get_job(bound_job_id)
            compatibility["executed_job"] = {
                "job_id": job.job_id,
                "status": job.status.value,
                "artifact_refs": job.artifact_refs,
                "output_payload": job.output_payload,
            }
    scorecard_runs = [run for run in report["node_runs"] if run["node_role"] == "scorecard"]
    if scorecard_runs:
        compatibility["scorecard"] = scorecard_runs[-1]["output_payload"]
    evidence = runtime.store.list_evidence_packages(mission_id)
    if evidence:
        compatibility["evidence_package"] = evidence[-1].to_json_dict()
    return compatibility


def run_graph_sequential(
    runtime,
    mission_id: str,
    template: GraphTemplate,
    *,
    worker_id: str = "graph",
) -> dict[str, Any]:
    return run_graph_once(runtime, mission_id, template, worker_id=worker_id)


def _run_until_blocked(
    runtime,
    graph_run_id: str,
    *,
    worker_id: str,
    max_workers: int,
    registry: GraphNodeExecutorRegistry | None,
) -> dict[str, Any]:
    while True:
        report = advance_graph(
            runtime,
            graph_run_id,
            worker_id=worker_id,
            max_workers=max_workers,
            registry=registry,
        )
        if report["status"] in {
            GraphRunStatus.SUCCEEDED.value,
            GraphRunStatus.FAILED.value,
            GraphRunStatus.CANCELED.value,
            GraphRunStatus.WAITING_APPROVAL.value,
        }:
            return report


def _create_wave_node_runs(
    runtime,
    graph_run: GraphRunRecord,
    ready: list[ReadyNode],
    existing: list[NodeRunRecord],
) -> list[NodeRunRecord]:
    next_sequence = max((run.sequence for run in existing), default=0) + 1
    created: list[NodeRunRecord] = []
    for offset, item in enumerate(ready):
        node_run = runtime.store.create_node_run(
            NodeRunRecord.create(
                node_run_id=str(uuid4()),
                graph_run_id=graph_run.graph_run_id,
                mission_id=graph_run.mission_id,
                node_id=item.node.node_id,
                node_role=item.node.role,
                node_kind=item.node.kind,
                sequence=next_sequence + offset,
                input_payload=item.input_payload,
            )
        )
        runtime.store.update_node_run_status(node_run.node_run_id, NodeRunStatus.RUNNING)
        created.append(runtime.store.get_node_run(node_run.node_run_id))
    return created


def _execute_wave(
    runtime,
    graph_run: GraphRunRecord,
    template: GraphTemplate,
    ready: list[ReadyNode],
    node_runs: list[NodeRunRecord],
    *,
    worker_id: str,
    max_workers: int,
    registry: GraphNodeExecutorRegistry | None,
) -> list[GraphNodeExecutionResult]:
    results: list[GraphNodeExecutionResult | None] = [None] * len(ready)
    worker_indexes = [index for index, item in enumerate(ready) if item.node.kind == "worker"]
    program_indexes = [index for index in range(len(ready)) if index not in worker_indexes]

    for index in program_indexes:
        results[index] = execute_graph_node(
            _execution_context(
                runtime,
                graph_run,
                template,
                ready[index],
                node_runs[index],
                worker_id,
            ),
            registry=registry,
        )
    if worker_indexes:
        worker_count = max(1, min(max_workers, len(worker_indexes)))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="aedt-graph") as executor:
            futures = {
                index: executor.submit(
                    execute_graph_node,
                    _execution_context(
                        runtime,
                        graph_run,
                        template,
                        ready[index],
                        node_runs[index],
                        f"{worker_id}:{ready[index].node.node_id}:{ready[index].run_index}",
                    ),
                    registry=registry,
                )
                for index in worker_indexes
            }
            for index in worker_indexes:
                results[index] = futures[index].result()
    return [result for result in results if result is not None]


def _execution_context(
    runtime,
    graph_run: GraphRunRecord,
    template: GraphTemplate,
    ready: ReadyNode,
    node_run: NodeRunRecord,
    worker_id: str,
) -> GraphNodeExecutionContext:
    return GraphNodeExecutionContext(
        runtime=runtime,
        graph_run=graph_run,
        node_run=node_run,
        node=ready.node,
        template=template,
        input_payload=ready.input_payload,
        run_index=ready.run_index,
        worker_id=worker_id,
    )


def _resume_waiting_gates(
    runtime,
    graph_run: GraphRunRecord,
    template: GraphTemplate,
    waiting_runs: list[NodeRunRecord],
    worker_id: str,
    registry: GraphNodeExecutorRegistry | None,
) -> dict[str, Any]:
    for node_run in waiting_runs:
        node = template.node(node_run.node_id)
        run_index = len(
            [
                run
                for run in runtime.store.list_node_runs(graph_run.graph_run_id)
                if run.node_id == node.node_id and run.sequence <= node_run.sequence
            ]
        )
        result = execute_graph_node(
            GraphNodeExecutionContext(
                runtime=runtime,
                graph_run=graph_run,
                node_run=node_run,
                node=node,
                template=template,
                input_payload=node_run.input_payload,
                run_index=run_index,
                worker_id=worker_id,
            ),
            registry=registry,
        )
        if result.status == NodeRunStatus.WAITING_APPROVAL:
            runtime.store.update_graph_run_status(
                graph_run.graph_run_id,
                GraphRunStatus.WAITING_APPROVAL,
                current_node_id=node.node_id,
            )
            return graph_status(runtime, graph_run.graph_run_id)
        runtime.store.complete_node_run(
            node_run.node_run_id,
            result.status,
            result.output_payload,
            result.artifact_refs,
            evidence_package_id=result.evidence_package_id,
            edge_decision=result.outcome,
            error=result.error,
        )
        matching_edges = [
            edge
            for edge in template.edges
            if edge.from_node == node.node_id and edge.on == result.outcome
        ]
        if result.outcome in {"failed", "rejected"} and not matching_edges:
            return _fail_graph(
                runtime,
                graph_run,
                "unhandled_node_outcome",
                f"node {node.node_id} emitted {result.outcome} without a matching edge",
            )
        for edge in matching_edges:
            error = _create_edge_handoff(
                runtime,
                graph_run,
                edge,
                node_run,
                result.output_payload,
            )
            if error is not None:
                return _fail_graph(runtime, graph_run, error["code"], error["message"])
    runtime.store.update_graph_run_status(graph_run.graph_run_id, GraphRunStatus.RUNNING)
    refreshed = _require_graph_run(runtime, graph_run.graph_run_id)
    return _settle_graph(
        runtime,
        refreshed,
        template,
        runtime.store.list_node_runs(graph_run.graph_run_id),
        runtime.store.list_graph_handoffs(
            graph_run.graph_run_id,
            status=GraphHandoffStatus.PENDING,
        ),
    )


def _create_edge_handoff(runtime, graph_run, edge, node_run, payload) -> dict[str, str] | None:
    traversals = len(
        [
            handoff
            for handoff in runtime.store.list_graph_handoffs(graph_run.graph_run_id)
            if handoff.edge_id == edge.edge_id
        ]
    )
    if traversals >= edge.max_traversals:
        return {
            "code": "edge_traversal_limit",
            "message": f"edge {edge.edge_id} reached max_traversals {edge.max_traversals}",
        }
    runtime.store.create_graph_handoff(
        GraphHandoffRecord.create(
            handoff_id=str(uuid4()),
            graph_run_id=graph_run.graph_run_id,
            mission_id=graph_run.mission_id,
            edge_id=edge.edge_id,
            source_node_run_id=node_run.node_run_id,
            from_node=edge.from_node,
            to_node=edge.to_node,
            outcome=edge.on,
            payload=dict(payload),
        )
    )
    return None


def _settle_graph(runtime, graph_run, template, node_runs, pending) -> dict[str, Any]:
    waiting = [run for run in node_runs if run.status == NodeRunStatus.WAITING_APPROVAL]
    if waiting:
        runtime.store.update_graph_run_status(
            graph_run.graph_run_id,
            GraphRunStatus.WAITING_APPROVAL,
            current_node_id=waiting[0].node_id,
        )
        return graph_status(runtime, graph_run.graph_run_id)
    ready = ready_nodes(
        template,
        node_runs,
        pending,
        initial_payload=graph_run.initial_payload,
    )
    if ready:
        runtime.store.update_graph_run_status(
            graph_run.graph_run_id,
            GraphRunStatus.RUNNING,
            current_node_id=",".join(item.node.node_id for item in ready),
        )
        return graph_status(runtime, graph_run.graph_run_id)
    if pending:
        limit_error = _pending_limit_error(template, node_runs, pending)
        if limit_error is not None:
            return _fail_graph(runtime, graph_run, limit_error["code"], limit_error["message"])
        return _fail_graph(
            runtime,
            graph_run,
            "graph_deadlock",
            "pending handoffs exist but no graph node is ready",
        )
    runtime.store.update_graph_run_status(graph_run.graph_run_id, GraphRunStatus.SUCCEEDED)
    _complete_mission(runtime, graph_run.mission_id)
    return graph_status(runtime, graph_run.graph_run_id)


def _pending_limit_error(template, node_runs, pending) -> dict[str, str] | None:
    for handoff in pending:
        node = template.node(handoff.to_node)
        run_count = len([run for run in node_runs if run.node_id == node.node_id])
        if run_count >= node.max_runs:
            return {
                "code": "node_run_limit",
                "message": f"node {node.node_id} reached max_runs {node.max_runs}",
            }
    return None


def _fail_graph(
    runtime,
    graph_run: GraphRunRecord,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime.store.update_graph_run_status(
        graph_run.graph_run_id,
        GraphRunStatus.FAILED,
        current_node_id=graph_run.current_node_id,
        error={"code": code, "message": message, "details": details or {}},
    )
    _fail_mission(runtime, graph_run.mission_id, code, message)
    return graph_status(runtime, graph_run.graph_run_id)


def _prepare_mission_for_workers(runtime, mission_id: str) -> None:
    mission = runtime.get_mission(mission_id)
    if mission.state == MissionState.CREATED:
        runtime.store.update_mission_state(mission_id, MissionState.PLANNING)
        runtime.store.update_mission_state(mission_id, MissionState.WAITING_WORKER)
    elif mission.state in {MissionState.PLANNING, MissionState.EVALUATING}:
        runtime.store.update_mission_state(mission_id, MissionState.WAITING_WORKER)


def _complete_mission(runtime, mission_id: str) -> None:
    mission = runtime.get_mission(mission_id)
    if mission.state in {MissionState.COMPLETED, MissionState.FAILED, MissionState.CANCELED}:
        return
    if mission.state == MissionState.CREATED:
        runtime.store.update_mission_state(mission_id, MissionState.PLANNING)
        runtime.store.update_mission_state(mission_id, MissionState.WAITING_WORKER)
        runtime.store.update_mission_state(mission_id, MissionState.EVALUATING)
    elif mission.state == MissionState.PLANNING:
        runtime.store.update_mission_state(mission_id, MissionState.WAITING_WORKER)
        runtime.store.update_mission_state(mission_id, MissionState.EVALUATING)
    elif mission.state == MissionState.WAITING_WORKER:
        runtime.store.update_mission_state(mission_id, MissionState.EVALUATING)
    elif mission.state == MissionState.WAITING_APPROVAL:
        return
    runtime.store.update_mission_state(mission_id, MissionState.COMPLETED)
    runtime.store.set_mission_final_outcome(
        mission_id,
        {
            "code": "graph_completed",
            "reason": "yaml graph completed",
            "decision": "completed",
        },
    )


def _fail_mission(runtime, mission_id: str, code: str, message: str) -> None:
    mission = runtime.get_mission(mission_id)
    if mission.state in {MissionState.COMPLETED, MissionState.FAILED, MissionState.CANCELED}:
        return
    if mission.state == MissionState.CREATED:
        runtime.store.update_mission_state(mission_id, MissionState.PLANNING)
    runtime.store.update_mission_state(mission_id, MissionState.FAILED)
    runtime.store.set_mission_final_outcome(
        mission_id,
        {"code": code, "reason": message, "decision": "failed"},
    )


def _derive_initial_payload(runtime, mission_id: str) -> dict[str, Any]:
    queued = [job for job in runtime.list_jobs(mission_id) if job.status == JobStatus.QUEUED]
    if not queued:
        return {}
    payload = dict(queued[0].input_payload)
    action_id = payload.get("action_id")
    if action_id:
        try:
            action = runtime.store.get_action(str(action_id))
        except KeyError:
            pass
        else:
            payload = {**action.to_json_dict(), **payload}
    return payload


def _require_graph_run(runtime, graph_run_id: str) -> GraphRunRecord:
    graph_run = runtime.store.get_graph_run(graph_run_id)
    if graph_run is None:
        raise KeyError(f"graph run not found: {graph_run_id}")
    return graph_run
