from __future__ import annotations

from pathlib import Path

from aedt_agent.benchmark.models import BenchmarkTask, load_tasks


STAGE_A_V2_TASK_IDS = [
    "L1_create_substrate",
    "L1_assign_material",
    "L1_create_wave_port",
    "L1_create_setup",
    "L2_microstrip_line",
    "L2_dipole_antenna",
    "L2_patch_with_probe_feed",
    "L2_simple_filter",
    "Trap_missing_ground",
    "Trap_waveport_wrong_face",
]


def load_stage_a_v2_tasks(tasks_dir: Path) -> list[BenchmarkTask]:
    by_id = {task.task_id: task for task in load_tasks(tasks_dir)}
    missing = [task_id for task_id in STAGE_A_V2_TASK_IDS if task_id not in by_id]
    if missing:
        raise ValueError(f"Missing Stage A v2 task files: {', '.join(missing)}")
    return [by_id[task_id] for task_id in STAGE_A_V2_TASK_IDS]
