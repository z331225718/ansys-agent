---
name: ansys-engineering-rules
description: ansys-agent 工程纪律：Dual-MCP 工具路由、两阶段安全修改、TDR/SDD 证据边界。任何 AEDT 相关任务都必须先加载。
---

# Ansys 工程硬护栏（DSH 模式）

## 工具路由（不可违反）

- AEDT 会话/工程生命周期、求解、验证、导出、截图、日志、通用脚本 → **官方 PyAEDT-MCP（mcp__aedt__*）**。
- 参数化 3D Layout / HFSS 几何与材料修改（via、anti-pad、线宽、boundary、mesh、变量）→ **ansys-assistant（mcp__ansys_assistant__*）的 preview→apply 两阶段工具**。绝不允许用官方 run_python_code 绕过审批做生产性修改。
- PyAEDT API 语义不确定时 → 先查 **ansys-api-memory（mcp__ansys_api_memory__*）**，再行动。
- 多轮通道优化 → 走 ansys-agent YAML Graph（mission run-loop / advance-graph），不要手工逐步驱动求解。

## 证据边界

- 原始 S 参数与 TDR 曲线 **artifact-only**：报告中只给有界指标（最差值/频率、TDR 峰值偏差与位置、artifact 路径与 SHA256），不贴全曲线。
- 当前 reviewed loop 为差分：Touchstone 四端口 s4p；RL=SDD11、IL=SDD21；默认 TDR 观察 Diff1。把早/晚期 TDR 特征映射到物理结构之前，必须确认 Diff1 观察的是哪一端；证据缺失则在审批点停下，不猜测。

## 修改纪律

- 每个 AEDT 变更操作必须先 preview 拿 snapshot digest，经用户审批（token）后 apply，再 readback 验证；验证失败自动回滚，绝不手工补写。
- 反焊盘圆形 void 半径 ≤ 22mil；非功能焊盘信号圆半径 ∈ [7.875mil, 10mil]。
- 反焊盘作用于平面/参考/电源 shape，不直接改 padstack antipad_by_layer；非功能焊盘是 through/buried via 管柱层上的显式信号网络圆形 shape。
- 缺失层、shape 证据、圆心来源、半径约束、tdr_observation_port 或回滚证据的几何提案：请求审批或直接判校验失败。
