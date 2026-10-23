# ADR 0001: 采用官方 PyAEDT-MCP 并收敛 ansys-agent 核心领域定位

## 状态
已通过 (Accepted) - 2026-10-23

## 背景
Ansys 官方已推出并持续维护 **PyAEDT-MCP**（包名 `ansys-aedt-mcp`，基于 FastMCP 与 PyAEDT）。
经环境探测与验证，PyAEDT-MCP 官方工具集已涵盖标准的 AEDT 基础设施能力：
1. **生命周期管理**：`check_aedt_installed`、`check_aedt_status`、`launch_aedt`、`connect_to_aedt`、`disconnect_from_aedt`、`clear_aedt`。
2. **工程与设计管理**：`list_projects`、`list_designs`、`open_project`、`save_project`、`create_design`。
3. **求解与验证**：`validate_design`、`analyze_design`、`export_config`。
4. **脚本与执行**：`run_python_code`、`run_python_script`。
5. **结果与可视化**：`export_results`、`screenshot`、`get_model_info`、`get_pyaedt_logs`、`display_html`。

此前，`ansys-agent` 仓库在自建交互服务（`aedt_agent.interactive`）与旧版服务（`aedt_agent.mcp`）中实现了大量重叠的底层会话管理、工程保存、设计创建、求解启动/轮询与原生代码执行工具（如 `list_live_aedt_sessions`、`launch_live_aedt_session`、`attach_live_aedt_session`、`release_live_aedt_session`、`get_live_aedt_project_info`、`preview_live_project_save`、`apply_live_project_save`、`create_live_hfss_design`、`start_live_hfss_analysis`、`get_live_hfss_analysis_status`、`preview_live_hfss_results_export`、`preview_live_open_aedt_python` 等）。

这种重叠带来了：
- 双重维护成本与 API 演进滞后；
- 与官方求解器兼容性（如 2024 R2 / 2026 R1 gRPC 通信）脱节；
- 架构职责不清：兼顾了“通用通用 AEDT 控制器”与“面向 SI/PI 的高阶工程闭环智能体”。

## 决策

1. **全面采用官方 PyAEDT-MCP 作为基础 AEDT 控制平面**：
   - 移除 `ansys-agent` 中所有与官方功能完全重叠的通用生命周期、工程保存、设计创建、求解启动/状态/取消、结果导出以及直接 Python 代码执行工具。
   - 不再自研通用 Session Discovery、Desktop Process Launch / Detach 机制。

2. **坚决收敛并聚焦 ansys-agent 的核心工程与安全价值**：
   `ansys-agent` 不再做“另一个通用的 AEDT MCP”，而是成为专注于**高速射频/微波/SI-PI 领域工程闭环与高安全生产护栏**的 Agent 运行时与能力层：
   - **领域工程专业性**：BRD local-cut 截取、差分对 Via / 反焊盘 (Anti-pad) / 非功能焊盘 (NFP) 优化策略、频域 (SDD11, SDD21) 与时域 (TDR 阻抗波动、峰值位置) 自动评分闭环。
   - **高安全两阶段修改护栏 (Two-Phase Safety Harness)**：
     - 不做无约束的直接修改。所有细粒度模型变更必须遵循 `preview` -> 提取 digest/challenge -> 宿主单次显式审批 (`approval_token`) -> `apply` -> 原生状态回读验证 (`readback`) -> 校验失败自动回滚 (`rollback`)。
     - 严格的工作副本隔离，绝不直接污染或破坏人类专家审查过的源工程文件。
   - **细粒度 3D Layout & HFSS 参数化能力**：
     - 精确的线宽参数化、层叠阻抗微调；
     - 基于坐标与网络的精确 Via / Padstack 批量创建与更新；
     - 平面避让与反焊盘挖空 (circle void / antipad subtract)；
     - 网格剖分 (length-based mesh) 与局部远场球体 (infinite sphere) 结构化参数化。
   - **工程闭环与 YAML Graph 编排**：
     - 面向无人值守/受控优化的 DAG 调度系统（LLM 决策 + 确定性数值评估 + 程序性评分审计）；
     - 完整的 Mission 生命周期、审计日志、事件重放、证据包导出（TDR 曲线保持 artifact-only）。
   - **知识记忆与能力演化**：
     - PyAEDT API Memory 检索、受控探索追踪与向正式 Skill/Harness 的晋升机制。

3. **双 MCP 协作架构模式 (Dual-MCP Pattern)**：
   外层编排者（Claude Code / Codex / Pi Host）同时挂载两个互补的 MCP 服务：
   - `aedt`（官方 PyAEDT-MCP）：处理会话发现、附着、通用工程打开与保存、验证、求解调度、截图与日志读取。
   - `ansys-assistant`（ansys-agent）：处理高安全领域的参数化修改、反焊盘/Via 调整、工作流编排推进与状态审计。
