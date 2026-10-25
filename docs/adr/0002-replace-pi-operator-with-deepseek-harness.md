# ADR 0002: 采用 DeepSeek Harness (DSH) 替代 Pi Operator 作为主 Agent Shell

## 状态
已通过 (Accepted) - 2026-10-24

## 1. 背景与问题

此前为了摆脱对 Claude Code 商业客户端的单一绑定，仓库启动了基于 `@earendil-works/pi-agent-core` 与 `@earendil-works/pi-ai` 的 **Pi Operator** 试验（见 `docs/pi-operator/` 与 `integrations/pi-operator/`）。

然而在实施过程中，Pi 方案暴露出沉重的自研架构包袱与瓶颈：
1. **庞大的私有协议与胶水代码**：
   - 为让 Node.js 端的 Pi 与 Python 端的 ansys-agent 通信，编写了 Windows 命名管道 IPC (`operator_bridge`)、双向分帧、JSON Schema 生成打包工具 (`build_operator_schema_bundle.py`) 以及状态轮询。
2. **写权限安全落地阻断**：
   - Pi 仅停留在只读观察者阶段（P0-P3）。在推进到 P4 写操作时被安全阻断门暂停——因为若要在无天然沙箱和原生审批机制的通用 Node.js 进程中安全执行写操作，需要额外实现一套 command journal、authoritative digest、writer lease、preflight snapshot 等庞杂逻辑。
3. **UI 与交互体验简陋**：
   - 基于终端 readline 的 TUI 难以直观呈现复杂的电磁仿真报表、TDR/S 参数曲线图表、以及多智能体协同状态。

## 2. 考察结论：DeepSeek Harness (DSH) 的核心优势

DeepSeek Harness (DSH) 是官方专业级 Agent 运行时与桌面/Web GUI 环境。经全面代码与功能考察，DSH 天然具备替代 Pi 的全部核心能力，且无需额外编写私有桥接层：

| 评估维度 | Pi Operator (`@earendil-works/pi-*`) | DeepSeek Harness (DSH) | 选型结论 |
|---|---|---|---|
| **协议标准** | 自研命名管道 + JSONL + 自生成 JSON Schema | **行业标准 Model Context Protocol (MCP)** | **DSH 完胜**：零私有协议代码，原生读取 `.mcp.json` |
| **界面形态** | 单色终端 readline TUI | **现代化 Web GUI (http://127.0.0.1:57694) + 桌面应用** | **DSH 完胜**：支持 Markdown、富文本、产物卡片、交互式审批 |
| **安全与审批** | 需在 Node.js 中手工构建沙箱与 Token 租约 | **原生权限沙箱** (`workspace-write` / `danger-full-access`) + **原生确认** (`ask_user_question`) | **DSH 完胜**：天然满足生产级写保护与审批要求 |
| **智能体编排** | 需自行实现 ReAct 循环与上下文压缩 | **原生支持 Agent Teams、子智能体 (`subagent`)、任务看板与目标驱动** | **DSH 完胜**：开箱即用复杂工程优化长程推进 |
| **扩展机制** | 需自研 Observer Harness 与插件加载器 | **原生支持 Agent Skills (`.agents/skills/`) 与 MCP 资源** | **DSH 完胜**：完全复用本项目现有的工程 Skills |
| **维护成本** | 需维护 `integrations/pi-operator` (大量 node_modules, TS 编译, IPC) | **外部成熟宿主，仓库只需输出标准的 MCP 与 CLI** | **DSH 完胜**：可彻底剔除数万行桥接代码 |

## 3. 架构决策

1. **废弃 Pi Operator 自研外壳**：
   - 终止 `integrations/pi-operator`、`src/aedt_agent/operator_api`、`src/aedt_agent/operator_bridge` 及 `schemas/operator` 的进一步开发。
   - 不再将人力消耗在自研 IPC 管道和协议分帧上。
2. **确立 DSH 为首选 Agent Host**：
   - **交互界面**：用户使用 DSH Web GUI / 桌面客户端进行人机对话、任务下发与审批。
   - **工具通道**：DSH 通过标准 `.mcp.json` 加载：
     - `aedt`（官方 PyAEDT-MCP，负责底层 AEDT 会话、求解与通用操作）；
     - `ansys-assistant`（负责高安全参数化修改、反焊盘/Via 调整）；
     - `ansys-api-memory`（负责知识库检索）。
   - **执行通道**：DSH 原生支持执行 Python / PowerShell 命令，可直接调用 `aedt-agent mission ...` 启动长程 YAML Graph 闭环。
   - **技能承载**：DSH 直接加载 `.agents/skills/ansys-brd-via-optimization` 等领域技能。

## 4. 实施阶段

- **阶段一 (已完成)**：完成 DSH 调研与评估，在 `.mcp.json` 中确立双 MCP 架构。
- **阶段二**：完成 `aedt_access` 抽象层落地，默认直连官方 MCP 端口。
- **阶段三**：清理废弃的 `integrations/pi-operator` 目录及相关桥接代码，更新文档指南。
