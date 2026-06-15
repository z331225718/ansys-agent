# Pi 集成 PoC 技术规格

## 状态

- 日期: 2026-06-16
- 状态: 提案 / PoC 阶段
- 范围: 将 Pi (earendil-works/pi) 作为 ansys-agent 的外层会话与交互适配层

## 背景

ansys-agent 的核心 agent runtime（Mission/Job/Event/Approval/Graph）已经在 Python 中独立运行。
Pi 是一个本地 Agent 框架，提供：
- 多会话管理
- 流式交互（streaming）
- 多 provider 支持
- 安全沙箱

## 集成策略

**Pi 不拥有核心运行时。** Pi 作为外层适配器，通过以下 API 与 ansys-agent 交互：

```
[Pi Session] ←→ [ansys-agent CLI / API]
                      │
                      ├── mission create --brd-local-cut-model-review ...
                      ├── mission list
                      ├── graph run <template> <mission_id>
                      ├── graph status <graph_run_id>
                      ├── approval decide <approval_id> --approve
                      └── artifact query <mission_id> ...
```

## 集成点

### 1. CLI 包装

Pi 的 tool 系统直接调用 `python -m aedt_agent.agent` CLI：

```python
# Pi tool definition
@tool
def ansys_create_mission(goal: str, layout_file: str, signal_nets: list[str], bbox: str) -> dict:
    """Create an Ansys AEDT mission for BRD channel optimization."""
    return run_cli(["mission", "create", "--goal", goal,
                    "--brd-local-cut-model-review",
                    "--layout-file", layout_file,
                    "--signal-net", *signal_nets,
                    "--bbox", bbox])
```

### 2. Event Stream → Pi Session

ansys-agent 的 Event stream 通过 `mission events <mission_id>` 命令暴露。
Pi 轮询或订阅 events，展示在 session UI 中。

### 3. Approval → Pi Interactive

ansys-agent 的 approval gate 生成 `approval_id`。
Pi 检测到 `WAITING_APPROVAL` 状态后，提示用户选择 approve/reject。
用户输入通过 `approval decide <approval_id> --approve` 传回。

### 4. 安全边界

- Pi 的沙箱限制 ansys-agent 的文件系统访问
- ansys-agent 的环境 profile 控制 AEDT 版本、license、超时
- 两层安全：Pi session-level + ansys-agent mission-level

## 不做什么

- 不让 Pi 管理 AEDT 生命周期
- 不让 Pi 直接调用 PyAEDT
- 不让 Pi 做数值评估或工程决策
- 不把 Pi 的 session 状态与 ansys-agent 的 mission 状态耦合

## PoC 验收标准

1. Pi session 中可以创建 BRD model-review mission
2. Event stream 实时展示在 Pi UI
3. Approval 交互在 Pi 中完成
4. 完整链路不依赖 VLM
