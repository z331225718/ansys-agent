from pathlib import Path

from aedt_agent.chat.repair_context import summarize_repair_context
from aedt_agent.chat.workflow_planner import ChatPlannerInput, ChatWorkflowPlanner
from aedt_agent.nodes.catalog import NodeCatalog
from aedt_agent.workflow.templates import WorkflowTemplateCatalog


def _planner_input(request: str) -> ChatPlannerInput:
    return ChatPlannerInput(
        user_request=request,
        node_catalog=NodeCatalog.from_directory(Path("nodes/catalog")),
        workflow_templates=WorkflowTemplateCatalog.from_directory(Path("workflow_templates")),
    )


def test_chat_planner_selects_microstrip_template_and_fills_frequency():
    output = ChatWorkflowPlanner().plan(_planner_input("Create a microstrip S-parameter workflow at 5GHz stop at 20GHz"))

    defaults = {parameter.name: parameter.default for parameter in output.generated_workflow.parameters}
    assert output.selected_template == "microstrip_sparameter"
    assert output.confidence > 0.8
    assert output.validation_errors == []
    assert defaults["frequency"] == "5GHz"
    assert defaults["sweep_stop"] == "20GHz"


def test_chat_planner_selects_wave_port_template():
    output = ChatWorkflowPlanner().plan(_planner_input("I need a wave port on a face"))

    assert output.selected_template == "wave_port_setup"
    assert output.generated_workflow.workflow_id == "wave_port_setup_v1"
    assert output.missing_information == []


def test_chat_planner_selects_radiation_airbox_template_and_padding():
    output = ChatWorkflowPlanner().plan(_planner_input("Create an antenna radiation airbox with padding 15 mm"))

    defaults = {parameter.name: parameter.default for parameter in output.generated_workflow.parameters}
    assert output.selected_template == "radiation_airbox_setup"
    assert defaults["airbox_padding"] == 15.0


def test_chat_planner_selects_dipole_template_before_generic_antenna():
    output = ChatWorkflowPlanner().plan(_planner_input("做一个2.4GHz偶极子天线，扫频到4GHz，看S11和方向图"))

    defaults = {parameter.name: parameter.default for parameter in output.generated_workflow.parameters}
    assert output.selected_template == "dipole_antenna_s11_farfield"
    assert output.generated_workflow.workflow_id == "dipole_antenna_s11_farfield_v1"
    assert defaults["frequency"] == "2.4GHz"
    assert defaults["sweep_stop"] == "4GHz"
    assert output.validation_errors == []


def test_chat_planner_dipole_frequency_updates_derived_arm_length():
    output = ChatWorkflowPlanner().plan(_planner_input("做一个偶极子天线，工作在 2.5GHz，看 S11"))

    defaults = {parameter.name: parameter.default for parameter in output.generated_workflow.parameters}

    assert output.selected_template == "dipole_antenna_s11_farfield"
    assert defaults["frequency"] == "2.5GHz"
    assert defaults["dipole_arm_length_mm"] == 28.48
    assert output.validation_errors == []


def test_chat_planner_generates_simple_setup_workflow():
    output = ChatWorkflowPlanner().plan(_planner_input("Create an HFSS setup at 3GHz"))

    assert output.selected_template is None
    assert output.generated_workflow.workflow_id == "generated_setup"
    assert output.validation_errors == []
    assert output.confidence > 0.6


def test_chat_planner_reports_missing_information_when_request_is_ambiguous():
    output = ChatWorkflowPlanner().plan(_planner_input("Please help me simulate this"))

    assert output.generated_workflow is None
    assert output.missing_information == ["simulation_type"]
    assert output.confidence == 0.2


def test_chat_planner_validation_errors_become_missing_information():
    output = ChatWorkflowPlanner().plan(_planner_input("Create a setup"))

    assert output.generated_workflow.workflow_id == "generated_setup_missing_frequency"
    assert output.missing_information == ["frequency"]
    assert output.validation_errors[0]["code"] == "missing_input"
    assert output.confidence == 0.4


def test_repair_context_summary_handles_validation_and_step_failures():
    assert summarize_repair_context({"reason": "workflow_validation_failed", "errors": [{}, {}]}) == "Workflow validation failed with 2 error(s)."
    assert (
        summarize_repair_context(
            {
                "reason": "workflow_step_failed",
                "failed_step_id": "port",
                "error_type": "ValueError",
                "error_message": "bad assignment",
            }
        )
        == "Workflow step failed: port (ValueError: bad assignment)."
    )
    assert summarize_repair_context({"reason": "model_validation_failed", "failed_checks": [{}]}) == "Model validation failed with 1 failed check(s)."
