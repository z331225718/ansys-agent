from __future__ import annotations

from aedt_agent.ansys_agent.chat import classify_intent, handle_message, run_chat


def test_ansys_agent_chat_classifies_chinese_operator_intents():
    assert classify_intent("开始优化这个case") == "run"
    assert classify_intent("看一下当前进度") == "status"
    assert classify_intent("批准并继续") == "approve_resume"
    assert classify_intent("拒绝这个修改") == "reject"
    assert classify_intent("停止任务") == "stop"


def test_ansys_agent_chat_approve_resume_uses_pending_approval_and_graph():
    class FakeSupervisor:
        def __init__(self):
            self.calls = []

        def status(self):
            return {
                "status": "waiting_approval",
                "graph_run_id": "graph-1",
                "pending_approvals": [{"approval_id": "approval-1"}],
            }

        def approve(
            self,
            *,
            approval_id: str,
            option_id: str,
            comment: str,
            resume: bool = False,
            graph_run_id: str = "",
        ):
            self.calls.append(
                {
                    "approval_id": approval_id,
                    "option_id": option_id,
                    "comment": comment,
                    "resume": resume,
                    "graph_run_id": graph_run_id,
                }
            )
            return {
                "status": "succeeded",
                "agent_status": {
                    "status": "succeeded",
                    "next_safe_action": "report",
                },
            }

    supervisor = FakeSupervisor()

    turn = handle_message(supervisor, "批准并继续")

    assert turn.intent == "approve_resume"
    assert "状态：succeeded" in turn.message
    assert supervisor.calls == [
        {
            "approval_id": "approval-1",
            "option_id": "approve",
            "comment": "approved from ansys-agent chat",
            "resume": True,
            "graph_run_id": "graph-1",
        }
    ]


def test_ansys_agent_chat_once_prints_status():
    class FakeSupervisor:
        def status(self):
            return {
                "status": "not_started",
                "next_safe_action": "preflight",
                "recommended_command": "python -m aedt_agent.ansys_agent preflight --case case.json",
            }

    output = []

    code = run_chat(
        FakeSupervisor(),
        once="看状态",
        output_func=output.append,
    )

    assert code == 0
    assert output == [
        "状态：not_started\n"
        "下一步：preflight\n"
        "建议命令：python -m aedt_agent.ansys_agent preflight --case case.json"
    ]
