from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


InputFunc = Callable[[str], str]
OutputFunc = Callable[[str], None]


@dataclass(frozen=True)
class ChatTurn:
    intent: str
    message: str
    payload: dict[str, Any]
    exit_requested: bool = False


def run_chat(
    supervisor,
    *,
    once: str = "",
    input_func: InputFunc = input,
    output_func: OutputFunc = print,
) -> int:
    if once:
        turn = safe_handle_message(supervisor, once)
        output_func(turn.message)
        execute_blocking_intent(supervisor, turn)
        return _exit_code(turn)

    output_func("Pi Agent ready. 输入：开始优化 / 看状态 / 批准并继续 / 停止 / 帮助 / 退出")
    while True:
        try:
            text = input_func("pi> ")
        except EOFError:
            output_func("bye")
            return 0
        turn = safe_handle_message(supervisor, text)
        if turn.message:
            output_func(turn.message)
        execute_blocking_intent(supervisor, turn)
        if turn.exit_requested:
            return _exit_code(turn)


def safe_handle_message(supervisor, text: str) -> ChatTurn:
    try:
        return handle_message(supervisor, text)
    except Exception as exc:
        return ChatTurn(
            intent="error",
            message=f"执行失败：{type(exc).__name__}: {exc}",
            payload={"error": {"type": type(exc).__name__, "message": str(exc)}},
        )


def handle_message(supervisor, text: str) -> ChatTurn:
    intent = classify_intent(text)
    if intent == "empty":
        return ChatTurn("empty", "我在。可以输入“开始优化”“看状态”“批准并继续”。", {})
    if intent == "exit":
        return ChatTurn("exit", "bye", {}, exit_requested=True)
    if intent == "help":
        return ChatTurn("help", _help_text(), {})
    if intent == "status":
        payload = supervisor.status()
        return ChatTurn(intent, _format_status(payload), payload)
    if intent == "preflight":
        payload = supervisor.preflight()
        return ChatTurn(intent, _format_preflight(payload), payload)
    if intent == "run":
        payload = supervisor.run()
        return ChatTurn(intent, _format_run(payload), payload)
    if intent == "resume":
        status = supervisor.status()
        payload = supervisor.resume(graph_run_id=str(status.get("graph_run_id") or ""))
        return ChatTurn(intent, _format_run(payload), payload)
    if intent == "approve_resume":
        status = supervisor.status()
        approval_id = _first_approval_id(status)
        if not approval_id:
            return ChatTurn(intent, "当前没有 pending approval。", {"pi_status": status})
        payload = supervisor.approve(
            approval_id=approval_id,
            option_id="approve",
            comment="approved from Pi Agent chat",
            resume=True,
            graph_run_id=str(status.get("graph_run_id") or ""),
        )
        return ChatTurn(intent, _format_run(payload), payload)
    if intent == "approve":
        status = supervisor.status()
        approval_id = _first_approval_id(status)
        if not approval_id:
            return ChatTurn(intent, "当前没有 pending approval。", {"pi_status": status})
        payload = supervisor.approve(
            approval_id=approval_id,
            option_id="approve",
            comment="approved from Pi Agent chat",
        )
        return ChatTurn(intent, _format_approval(payload), payload)
    if intent == "reject":
        status = supervisor.status()
        approval_id = _first_approval_id(status)
        if not approval_id:
            return ChatTurn(intent, "当前没有 pending approval。", {"pi_status": status})
        payload = supervisor.reject(
            approval_id=approval_id,
            comment="rejected from Pi Agent chat",
        )
        return ChatTurn(intent, _format_approval(payload), payload)
    if intent == "stop":
        status = supervisor.status()
        payload = supervisor.stop(
            graph_run_id=str(status.get("graph_run_id") or ""),
            reason="operator requested stop from Pi Agent chat",
        )
        return ChatTurn(intent, _format_run(payload), payload)
    if intent == "web":
        return ChatTurn(
            intent,
            "正在启动 Pi operator panel；此终端会作为 web server 保持运行。",
            {},
        )
    status = supervisor.status()
    return ChatTurn(
        "unknown",
        "我还不能可靠理解这句话。当前状态如下：\n" + _format_status(status),
        {"pi_status": status},
    )


def execute_blocking_intent(supervisor, turn: ChatTurn) -> None:
    if turn.intent == "web":
        supervisor.web()


def classify_intent(text: str) -> str:
    normalized = " ".join(text.strip().casefold().split())
    if not normalized:
        return "empty"
    if normalized in {"q", "quit", "exit", "bye", "退出", "结束"}:
        return "exit"
    if _has_any(normalized, {"help", "帮助", "怎么用", "命令"}):
        return "help"
    if _has_any(normalized, {"web", "页面", "面板", "dashboard", "可视化"}):
        return "web"
    if _has_any(normalized, {"批准并继续", "通过并继续", "同意并继续", "approve and resume", "approve resume"}):
        return "approve_resume"
    if _has_any(normalized, {"拒绝", "不同意", "reject", "否决"}):
        return "reject"
    if _has_any(normalized, {"批准", "通过", "同意", "approve"}):
        return "approve"
    if _has_any(normalized, {"停止", "取消", "中止", "stop", "cancel"}):
        return "stop"
    if _has_any(normalized, {"继续", "恢复", "下一步", "resume", "continue"}):
        return "resume"
    if _has_any(normalized, {"状态", "进度", "status", "看看", "看一下", "现在怎么样"}):
        return "status"
    if _has_any(normalized, {"预检", "检查配置", "检查环境", "preflight", "validate"}):
        return "preflight"
    if _has_any(normalized, {"开始", "启动", "执行", "跑", "优化", "仿真", "闭环", "run", "start"}):
        return "run"
    return "unknown"


def _has_any(text: str, needles: set[str]) -> bool:
    return any(needle in text for needle in needles)


def _first_approval_id(status: dict[str, Any]) -> str:
    approvals = status.get("pending_approvals")
    if isinstance(approvals, list):
        for item in approvals:
            if isinstance(item, dict) and item.get("approval_id"):
                return str(item["approval_id"])
    approval = status.get("approval")
    if isinstance(approval, dict) and approval.get("approval_id"):
        return str(approval["approval_id"])
    return ""


def _format_status(payload: dict[str, Any]) -> str:
    lines = [
        f"状态：{payload.get('status', 'unknown')}",
        f"下一步：{payload.get('next_safe_action', '')}",
    ]
    active_node = payload.get("active_node")
    if active_node:
        lines.append(f"当前节点：{active_node}")
    graph_run_id = payload.get("graph_run_id")
    if graph_run_id:
        lines.append(f"graph_run_id：{graph_run_id}")
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        metric_text = _compact_metrics(metrics)
        if metric_text:
            lines.append(f"指标：{metric_text}")
    approvals = payload.get("pending_approvals")
    if isinstance(approvals, list) and approvals:
        lines.append(f"待审批：{len(approvals)} 个，输入“批准并继续”可批准第一个并恢复")
    command = payload.get("recommended_command")
    if command:
        lines.append(f"建议命令：{command}")
    return "\n".join(line for line in lines if line)


def _format_preflight(payload: dict[str, Any]) -> str:
    status = payload.get("status", "unknown")
    failed = payload.get("failed_checks") or []
    if failed:
        return f"预检：{status}\n失败项：{', '.join(str(item) for item in failed)}"
    return f"预检：{status}"


def _format_run(payload: dict[str, Any]) -> str:
    pi_status = payload.get("pi_status")
    if isinstance(pi_status, dict):
        return _format_status(pi_status)
    status = payload.get("status", "unknown")
    if "reason" in payload:
        return f"状态：{status}\n原因：{payload['reason']}"
    return f"状态：{status}"


def _format_approval(payload: dict[str, Any]) -> str:
    status = payload.get("status", "unknown")
    approval = payload.get("approval")
    approval_id = ""
    if isinstance(approval, dict):
        approval_id = str(approval.get("approval_id") or "")
    lines = [f"审批：{status}"]
    if approval_id:
        lines.append(f"approval_id：{approval_id}")
    pi_status = payload.get("pi_status")
    if isinstance(pi_status, dict):
        lines.append(_format_status(pi_status))
    return "\n".join(lines)


def _compact_metrics(metrics: dict[str, Any]) -> str:
    keys = (
        "round_index",
        "score_status",
        "touchstone_kind",
        "return_loss_trace",
        "rl_worst_db",
        "tdr_observation_port",
        "tdr_peak_deviation_ohm",
        "objective_total_cost",
    )
    pairs = []
    for key in keys:
        value = metrics.get(key)
        if value not in ("", None, []):
            pairs.append(f"{key}={value}")
    return ", ".join(pairs)


def _help_text() -> str:
    return "\n".join(
        [
            "我能执行这些意图：",
            "- 开始优化 / 开始仿真：run",
            "- 看状态 / 进度：status",
            "- 检查环境 / 预检：preflight",
            "- 批准 / 批准并继续 / 拒绝：approval flow",
            "- 继续 / 下一步：resume",
            "- 停止：stop",
            "- 打开页面：web",
            "- 退出：exit",
        ]
    )


def _exit_code(turn: ChatTurn) -> int:
    status = str(turn.payload.get("status") or "")
    if status in {"failed", "canceled", "preflight_failed"}:
        return 2
    if turn.intent == "error":
        return 1
    return 0


def as_json(turn: ChatTurn) -> str:
    return json.dumps(
        {
            "intent": turn.intent,
            "message": turn.message,
            "payload": turn.payload,
            "exit_requested": turn.exit_requested,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
