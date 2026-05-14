import pytest

from aedt_agent.benchmark.node_plan_parser import NodePlanParseError, extract_node_plan


def test_extract_node_plan_uses_last_valid_plan_json():
    text = 'notes {"plan": []}\nfinal {"plan": [{"node_id": "create_substrate", "inputs": {"material": "FR4_epoxy"}}]}'

    plan = extract_node_plan(text)

    assert len(plan.plan) == 1
    assert plan.plan[0].node_id == "create_substrate"


def test_extract_node_plan_rejects_non_json_text():
    with pytest.raises(NodePlanParseError):
        extract_node_plan("app.modeler.create_box([0,0,0], [1,1,1])")
