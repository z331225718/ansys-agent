from __future__ import annotations

from pathlib import Path

from aedt_agent.chat.workflow_planner import ChatPlannerInput, ChatWorkflowPlanner
from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.workflow.templates import WorkflowTemplateCatalog


def _planner_input(request: str) -> ChatPlannerInput:
    return ChatPlannerInput(
        user_request=request,
        node_catalog=NodeCatalog.from_directory(Path("nodes/catalog")),
        workflow_templates=WorkflowTemplateCatalog.from_directory(Path("workflow_templates")),
    )


def test_default_antenna_request_does_not_select_layout_template():
    result = ChatWorkflowPlanner().plan(_planner_input("做一个天线 S11 仿真"))

    assert result.selected_template != "import_brd_cutout_sparam_tdr"


def test_brd_keywords_can_select_experimental_layout_template():
    result = ChatWorkflowPlanner().plan(_planner_input("导入 brd，选择 SRDS_3_RX1 差分线，cutout 后看 TDR"))

    assert result.selected_template == "import_brd_cutout_sparam_tdr"
