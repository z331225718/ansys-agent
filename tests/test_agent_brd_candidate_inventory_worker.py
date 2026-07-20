from __future__ import annotations

from dataclasses import dataclass

from aedt_agent.agent.mission import JobRecord
from aedt_agent.agent.workers.brd_candidate_inventory import (
    build_brd_candidate_inventory_job_input,
    run_brd_candidate_inventory_worker,
)
from aedt_agent.agent.workers.registry import WorkerContext


@dataclass(frozen=True)
class FakeInventoryResult:
    inventory_path: str
    manifest_path: str
    inventory: dict
    summary: dict


class FakeInventoryAdapter:
    def run(self, request):
        inventory_path = request.artifact_dir / "candidate_action_inventory.json"
        manifest_path = request.artifact_dir / "candidate_inventory_manifest.json"
        inventory_path.write_text("{}", encoding="utf-8")
        manifest_path.write_text("{}", encoding="utf-8")
        return FakeInventoryResult(
            inventory_path=str(inventory_path),
            manifest_path=str(manifest_path),
            inventory={
                "source": "unit_test_discovery",
                "anti_pad_shape_layers": [
                    {
                        "layer": "L2_GND",
                        "plane_shape_ids": ["101"],
                        "center_padstack_instance_ids": ["501", "502"],
                        "bridge_center_padstack_instance_ids": ["501", "502"],
                        "parasitic_target": "auto_discovered",
                    }
                ],
                "non_functional_pad_layers": [],
            },
            summary={"status": "succeeded", "candidate_action_count": 1},
        )


def test_candidate_inventory_worker_updates_loop_context(tmp_path):
    project = tmp_path / "case.aedt"
    project.write_text("project", encoding="utf-8")
    payload = build_brd_candidate_inventory_job_input(
        project_path=project,
        loop_context={
            "candidate_action_inventory_path": str(
                tmp_path / "candidate_action_inventory.json"
            ),
            "geometry_constraints": {"anti_pad": {"max_radius_mil": 22}},
        },
    )
    job = JobRecord.create(
        job_id="job",
        mission_id="mission",
        capability="brd.candidate_inventory.discover",
        idempotency_key="candidate-inventory",
        input_payload=payload,
        timeout_seconds=60,
        retry_limit=0,
    )

    output = run_brd_candidate_inventory_worker(
        job,
        WorkerContext(worker_id="test", artifacts_dir=str(tmp_path / "artifacts")),
        inventory_adapter=FakeInventoryAdapter(),
    )

    loop_context = output["loop_context"]
    assert output["status"] == "succeeded"
    assert loop_context["candidate_action_inventory"]["source"] == "unit_test_discovery"
    assert loop_context["candidate_action_inventory_path"].endswith(
        "candidate_action_inventory.json"
    )
    assert loop_context["candidate_inventory_summary"]["candidate_action_count"] == 1
    assert output["artifact_refs"] == [
        loop_context["candidate_action_inventory_path"],
        loop_context["candidate_inventory_manifest"],
    ]
