# AEDT-MCP 节点化智能仿真系统 — 最终架构设计与实施规格书

> **文档状态**: 最终设计方案 (Final Architecture & Implementation Spec)
> **基于输入**: AEDT-MCP 设计讨论稿、多方 Agent 评审（务实派、架构派、工程派、风控派综合裁决）
> **核心原则**: 架构上坚持节点化，实施上坚持 MVP 验证，工程上坚持受控执行与语义判卷。

---

## 1. 项目定位与核心边界

### 1.1 系统愿景
构建一个**节点化、类型安全、状态可查、语义可验证**的 AEDT 智能仿真系统。将“面向 API 的自然语言编程”转化为“受控的工程化节点编排”，彻底解决 LLM 在 CAE 领域常见的“代码能跑但物理模型错误（Silent Failure）”问题。

### 1.2 MVP 阶段严格收缩边界 (Red Lines)
基于务实派和风控派的裁决，MVP 阶段（Stage A & B）执行严格的边界控制：
- **领域范围**：**仅限 HFSS 子域**（建模、材料、边界、激励、求解设置、S参数导出）。绝不碰 Icepak、Maxwell、Mechanical 或 EDB。
- **API 范围**：仅覆盖核心业务链路的 **Top 50 高频 API**。
- **基础设施**：不引入图数据库（GitNexus），使用 **SQLite + FTS5** 作为轻量级语义和案例引擎。
- **执行安全**：**严禁向正式用户暴露裸 `execute_script` 工具**，所有执行必须通过强类型的静态节点进行。
- **系统形态**：不做完整 ComfyUI 可视化编辑器，不搞动态节点生成，不做 DAG 并行执行。

---

## 2. 系统总体架构

系统采用“三层执行 + 旁路知识”架构，将大模型的规划能力与小模型的受控生成能力结合。

```text
[ 用户自然语言需求 ]
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 调度层 (Scheduler Layer - 强推理大模型 Claude 3.5 / GPT-4o)  │
│ - 意图理解与参数提取                                          │
│ - 从静态 Node Catalog 中选择工作流 Pattern                      │
│ - 规划节点序列 (DAG) 并进行类型检查 (Type Checking)            │
└───────┬─────────────────────────────────────────────────────┘
        │ 下发任务参数与上下文
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 节点层 (Node Layer - 执行代码生成与验证)                     │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ 静态节点 (e.g., create_wave_port)                       │ │
│ │ ├─ 输入/输出类型约束 (FaceId, PortId)                      │ │
│ │ ├─ Context 组装: API白名单 + 语义约束 + 案例 + 陷阱库      │ │
│ │ ├─ AST 生成 (低配本地大模型 / 确定性逻辑)                  │ │
│ │ ├─ Pre-check (执行前校验) & Post-check (执行后验证)        │ │
│ └─────────────────────────────────────────────────────────┘ │
└───────┬─────────────────────────────────────────────────────┘
        │ 提交受控的 AST/代码片段
        ▼
┌─────────────────────────────────────────────────────────────┐
│ MCP 执行层 (AEDT MCP Server)                                 │
│ - 持久化 pyaedt (.NET interop) / 长连接健康检查                │
│ - 单实例任务队列 (Task Queue) + 全局锁 (Global Lock)           │
│ - AST 审计拦截 (拦截 OS, sys, 网络操作)                        │
└───────┬─────────────────────────────────────────────────────┘
        │ 串行执行
        ▼
[ Ansys AEDT Desktop (Windows 本地环境) ]
```

---

## 3. 核心数据资产设计 (Knowledge Layer)

不堆砌原始代码，而是构建“结构化、高信噪比”的三大知识库（采用 SQLite + FTS5）。

### 3.1 API 语义库 (`api_semantics.sqlite`)
放弃全量 API，精标 Top 50 核心 API。
- **自动抽取**：签名 (Signature)、类型提示 (Type hints)、默认值、基础 Docstring。
- **人工精标 (核心价值)**：隐藏约束 (Hidden constraints)、关联操作。
  - *示例*：`create_wave_port` 必须要求所选 Face 完全贴合 Background，否则边界失效。

### 3.2 结构化案例库 (`workflow_cases/`)
案例不是 `.py` 脚本，而是工作流模板。
- **包含**：任务描述、Workflow 步骤划分、使用的 API 组合、预期的 AEDT 终态。

### 3.3 反直觉陷阱库 (`common_traps/`)
收集导致 Semantic Error（代码不报错但仿真错）的负面知识。
- *示例*：Airbox 尺寸未达到 $\lambda/4$ 导致辐射边界吸收失败；未建 Ground Plane 直接加激励。

---

## 4. 节点与执行引擎规范

### 4.1 静态节点定义 (Node Schema)
首批严格限制在 8 个以内（如 `create_substrate`, `create_patch`, `create_port`, `create_airbox`, `create_setup`, `create_sweep`, `export_sparameters`）。

每个节点必须是一个受控的 JSON/Python 定义：
```python
{
    "id": "create_wave_port",
    "inputs": {
        "app": "Hfss",
        "assignment": "FaceId", # 强类型约束
        "port_name": "str"
    },
    "outputs": {
        "port_id": "PortId",
        "app": "Hfss"
    },
    "api_whitelist": ["Hfss.create_wave_port", "Hfss.modeler.get_face_center"],
    "semantic_constraints": ["Ensure FaceId is completely touching the outer background region."],
    "precheck": "validate_face_exists()", # 执行前检查
    "postcheck": "validate_port_created()" # 执行后验证
}
```

### 4.2 AEDT MCP 安全执行队列
AEDT 是单进程、长连接的 `.NET` 互操作环境，极易崩溃。
- **全局单例与锁**：所有节点提交的代码进入全局队列，**严格串行执行**。
- **健康检查 (Watchdog)**：执行前 ping AEDT session，若假死则触发自动重启与 Checkpoint 恢复。
- **AST 级安全审计**：在 `execute_script` 底层使用 `ast` 模块解析代码，凡包含 `os`, `sys`, `subprocess`, `shutil`, `socket` 或是文件删除操作的，一律在进入队列前阻断。

---

## 5. 三阶段实施路线图 (Roadmap)

### Stage A：离线 API Grounding 与自动判卷 (第 1-3 周)
**目标**：用数据证明“语义约束+案例”比“裸LLM”提升多少命中率，搭建测试基建。
- **行动**：
  1. 梳理 Top 50 HFSS API，构建 SQLite 语义库和陷阱库。
  2. 编写 20-30 个 Benchmark 任务（贴片天线、波导、差分线等）。
  3. **开发自动化判卷脚本 (Validation Script)**：通过断言（如 `assert app.modeler.get_object_material(obj) == "copper"`）客观衡量语义正确性。
  4. 运行三组对照实验：Group A (裸 LLM) vs Group B (注入 docstring) vs Group C (节点白名单 + 语义库 + 陷阱库)。
- **通过红线 (Go/No-Go)**：Group C 相对 Group B 的 Semantic Pass（语义通过率）必须提升 $\ge 15\%$。

### Stage B：受控 MCP 与静态节点原型 (第 4-7 周)
**目标**：接入真实 AEDT，跑通安全队列和核心节点。
- **行动**：
  1. 开发基于 FastMCP 的服务端，实现长连接队列、并发锁和 AST 审计拦截。
  2. 封装首批 5-8 个静态节点。
  3. 闭环测试：用户自然语言 $\rightarrow$ 调度层规划节点 $\rightarrow$ 节点检索上下文 $\rightarrow$ 代码生成 $\rightarrow$ MCP 受控执行 $\rightarrow$ Post-check 反馈。
- **成功指标**：AEDT 崩溃恢复成功率 $\ge 90\%$，节点防范非法参数成功率 $100\%$。

### Stage C：图谱增强与 Semantic Validator (第 8 周以后)
**目标**：引入高阶护城河，解决复杂溯源与深度物理验证。
- **行动**：
  1. 确认 GitNexus 商业授权，或使用 Graphify (MIT) 替代，实现 Process (执行流) 追踪。
  2. 开发深度的 **Semantic Validator**（几何干涉检查、自适应网格收敛检查、S参数非物理异常检查）。
  3. 实现节点由于高频错误触发自动调优的“进化机制”。

---

## 6. 核心评估体系 (Benchmark Metrics)

为避免“Demo 级验收”，系统必须通过三级严格判卷：

| 评估层级 | 验证内容 | 实现方式 | 责任方 |
| :--- | :--- | :--- | :--- |
| **L1: Syntax Pass** | 代码语法正确，无拼写错误 | `ast.parse()` 静态检查 | 代码生成层 |
| **L2: Runtime Pass** | AEDT API 不抛异常，参数合法 | AEDT 执行 + Traceback 捕获 | MCP 执行层 |
| **L3: Semantic Pass** | **物理与几何结果符合预期** | Validation Script (提取 AEDT 内部状态断言) | 节点 Post-check |

---

## 7. 明确的“不为”清单 (Anti-Goals for MVP)

为保证项目按期高质量交付，以下特性在 Stage A/B 期间**严格禁止**投入精力：
1. ❌ **全求解器支持**：不做 Maxwell / Icepak / Mechanical。
2. ❌ **纯对话式免验证执行**：不向普通用户提供底层 `execute_script`，防范毁灭性错误。
3. ❌ **复杂前端 UI**：不花时间做类似 ComfyUI 的拖拽连线前端，初期专注于后端 DAG 和 Agent 规划。
4. ❌ **图数据库硬依赖**：在证明业务 ROI 前，不在生产环境部署 LadybugDB/Neo4j。
5. ❌ **动态节点**：不允许 LLM 在运行时自创节点，所有节点必须走代码级的 Review 和固化。