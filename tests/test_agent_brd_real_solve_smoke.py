from __future__ import annotations

import os
from pathlib import Path

import pytest

from aedt_agent.infrastructure import (
    BrdRealSolveAdapter,
    BrdRealSolveRequest,
    RealAedtEnvironment,
)
from aedt_agent.layout.channel_scoring import (
    parse_tdr_csv,
    parse_touchstone,
)


RUN_REAL = os.getenv("ANSYS_AGENT_RUN_REAL_AEDT") == "1"


@pytest.mark.skipif(
    not RUN_REAL,
    reason="set ANSYS_AGENT_RUN_REAL_AEDT=1 to run AEDT smoke",
)
def test_real_aedt_solve_exports_touchstone_and_tdr(tmp_path):
    project = Path(os.environ["ANSYS_AGENT_REAL_AEDT_PROJECT"])
    setup_name = os.getenv(
        "ANSYS_AGENT_REAL_AEDT_SETUP",
        "Setup1",
    )
    sweep_name = os.getenv(
        "ANSYS_AGENT_REAL_AEDT_SWEEP",
        "Sweep1",
    )
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(parents=True)
    request = BrdRealSolveRequest(
        project_path=project,
        artifact_dir=artifact_dir,
        setup_name=setup_name,
        sweep_name=sweep_name,
        solution_name=f"{setup_name} : {sweep_name}",
        touchstone_name=os.getenv(
            "ANSYS_AGENT_REAL_AEDT_TOUCHSTONE_NAME",
            "channel.s4p",
        ),
        tdr_report_name="AgentTDR",
        tdr_expression=os.environ[
            "ANSYS_AGENT_REAL_AEDT_TDR_EXPRESSION"
        ],
        expected_port_count=int(
            os.getenv("ANSYS_AGENT_REAL_AEDT_PORT_COUNT", "4")
        ),
        tdr_differential_pairs=os.getenv(
            "ANSYS_AGENT_REAL_AEDT_TDR_DIFFERENTIAL_PAIRS",
            "1",
        )
        != "0",
        tdr_observation_port=os.getenv(
            "ANSYS_AGENT_REAL_AEDT_TDR_OBSERVATION_PORT",
            "Diff1",
        ),
        environment=RealAedtEnvironment(
            version=os.getenv(
                "ANSYS_AGENT_REAL_AEDT_VERSION",
                "2026.1",
            ),
            non_graphical=True,
        ),
    )

    result = BrdRealSolveAdapter().run(request)

    assert parse_touchstone(Path(result.touchstone_path))
    assert parse_tdr_csv(Path(result.tdr_path))
    assert Path(result.solved_project).is_file()
    assert Path(result.solve_manifest_path).is_file()
