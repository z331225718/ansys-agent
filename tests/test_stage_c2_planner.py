from pathlib import Path
from typing import Any

from aedt_agent.demo.config import PlannerConfig
from aedt_agent.demo.service import DemoService


class FakeRepairClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def propose_workflow(self, request: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"request": request, "context": context})
        if len(self.calls) == 1:
            return {
                "workflow_id": "bad_wave_port",
                "name": "Bad Wave Port",
                "nodes": [{"id": "port", "node_id": "create_port", "inputs": {"name": "Port1", "port_type": "wave", "assignment": None}}],
            }
        return context["templates"]["wave_port_setup"]["workflow"]


def test_demo_service_plan_includes_deterministic_attempt_metadata():
    service = DemoService(Path("."))

    plan = service.plan({"user_request": "create a microstrip s-parameter simulation"})

    assert plan["planner_mode"] == "deterministic"
    assert plan["repair_count"] == 0
    assert len(plan["attempts"]) == 1
    assert plan["attempts"][0]["validation"]["passed"] is True


def test_demo_service_llm_mode_falls_back_without_client_or_key():
    service = DemoService(Path("."), planner_config=PlannerConfig(mode="llm"))

    plan = service.plan({"user_request": "create a microstrip s-parameter simulation"})

    assert plan["planner_mode"] == "deterministic"
    assert plan["fallback_reason"] == "llm_not_configured"
    assert plan["selected_template"] == "microstrip_sparameter"


def test_demo_service_llm_repair_loop_records_failed_and_fixed_attempts():
    client = FakeRepairClient()
    service = DemoService(
        Path("."),
        planner_config=PlannerConfig(mode="llm", api_key="local-test-key", max_repair_attempts=3),
        llm_client=client,
    )

    plan = service.plan({"user_request": "create a wave port setup"})

    assert plan["planner_mode"] == "llm"
    assert plan["repair_count"] == 1
    assert len(plan["attempts"]) == 2
    assert plan["attempts"][0]["validation"]["passed"] is False
    assert plan["attempts"][1]["validation"]["passed"] is True
    assert plan["validation_errors"] == []
    assert len(client.calls) == 2
    assert client.calls[1]["context"]["previous_errors"][0]["code"] == "wrong_input_type"
