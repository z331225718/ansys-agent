# AEDT 节点化智能仿真系统 — 设计规格书

> Stage A + B 设计文档，覆盖离线 grounding 实验验证到 MCP 执行层构建。
> 基于 AEDT-MCP-Design-Discussion.md、LLM_A.md、LLM_B.md、LLM_C.md 四份讨论稿综合而成。

---

## 1. 项目定位与目标

### 1.1 定位

**AEDT 节点化智能仿真系统**：将 Ansys AEDT 的复杂 UI 操作转化为自然语言驱动的节点化工作流。

- **短期目标**：为 AEDT 工程师提效——用自然语言完成建模、激励、求解、后处理等操作
- **长期愿景**：降低 AEDT 上手门槛——节点化拆解使新手能理解和学习完整仿真流程

### 1.2 核心目标

| 目标 | 度量 | 优先级 |
|---|---|---|
| API 准确性 | pyaedt 代码命中率 > 裸 LLM 基线 | P0 |
| 可验证性 | 每步操作结果可检查，语义错误可捕获 | P0 |
| 可复用性 | 工作流模块跨项目复用 | P1 |
| 可进化性 | 节点可独立优化，系统整体能力持续提升 | P1 |
| 成本可控 | 复杂判断用大模型，简单生成用小模型 | P2 |

### 1.3 限定范围

- **仅 HFSS 子域**：建模、激励、setup、sweep、S 参数导出
- **不碰**：Icepak、Maxwell、Mechanical、Optimetrics、Layout、EDB、复杂后处理
- **目标用户**：AEDT 工程师（第一版），未来扩展到新手

---

## 2. Stage A：离线 API Grounding 实验

### 2.1 目标

验证两个核心假设：

1. **Grounding 假设**：先检索 API 语义再生成代码，命中率是否显著高于裸模型
2. **Scoped 假设**：限定 API 白名单 + 简化 prompt，命中率是否显著高于裸模型

两个假设是同一问题的两种解法——Grounding 通过"给 AI 更多知识"提升命中率，Scoped 通过"让 AI 面对更少选择"提升命中率。两者互补，不是二选一。

### 2.2 Benchmark 任务（按失败模式选择）

| 任务 | 暴露的失败模式 | 为什么选它 |
|---|---|---|
| 微带贴片天线 | 步骤遗漏（忘建 ground/airbox） | 经典多步骤流程，容易漏中间环节 |
| 波导滤波器 | 参数约束（耦合孔尺寸与频率的物理关系） | 需要物理经验，纯 API 知识不够 |
| 差分对走线 | 枚举值错误（solver 类型、边界条件类型） | 大量枚举参数，容易选错 |
| 同轴馈电 | 前置依赖（port 面必须完全接触背景） | 典型的"隐性约束"，静默失败 |

每个任务包含：
- 自然语言需求描述
- reference script（手写正确 pyaedt 代码）
- expected outputs（预期 AEDT 内部状态）
- validation criteria（验证断言脚本）

### 2.3 三组对比实验

| 组别 | 策略 | 验证什么 |
|---|---|---|
| Baseline | 裸模型直接生成 | 底线水平 |
| Grounded | 先 retrieval 再生成 | "给 AI 更多知识"的收益 |
| Scoped | API 白名单 + 简化 prompt 再生成 | "让 AI 面对更少选择"的收益 |

Scoped 组实现方式：在 prompt 中加入"你只能使用以下 API：..."，成本几乎为零，但信息量很大。

### 2.4 评估指标与自动化判卷

**三级评估**：

| 级别 | 检查内容 | 自动化方式 |
|---|---|---|
| Syntax pass | 代码语法正确，可导入 | `ast.parse()` + `import` 检查 |
| Runtime pass | 代码可执行，无异常 | 沙箱执行 + traceback 捕获 |
| Semantic pass | 物理模型正确，结果合理 | 验证断言脚本检查 AEDT 状态 |

**自动化判卷脚本**：每个 benchmark 任务附带 validation script，包含对 AEDT 内部状态的断言（如 `assert material == "copper"`），将 Semantic pass 从主观判断转化为客观度量。

### 2.5 三层知识资产

```
knowledge/
  api_semantics.json       ← 签名、参数、约束（结构化）
  workflow_cases/          ← 完整工作流模板（正面知识）
    hfss_patch_antenna.yaml
    waveguide_filter.yaml
    differential_pair.yaml
    coaxial_feed.yaml
  anti_patterns/           ← 常见陷阱（负面知识）
    waveport_no_contact.md
    negative_airbox_silent.md
    forgot_ground_plane.md
```

- **api_semantics.json**：API 语义层，结构化存储签名、参数、约束
- **workflow_cases/**：正面知识，教会 AI "该怎么做"
- **anti_patterns/**：负面知识，教会 AI "不该怎么做"

三者互补，检索时一起拉取。

### 2.6 API 语义抽取策略

```
自动提取（覆盖 ~70%）：签名 + type hints + default values + docstring（Parameters/Raises/Notes）
半自动提取（覆盖 ~20%）：用 LLM 分析方法体中的条件判断和异常抛出
人工标注（覆盖 ~10%）：隐性约束、静默失败、物理经验
```

pyaedt 的 docstring 是 NumPy 风格，其中已包含大量约束信息（参数类型、语义约束、前置条件），用正则 + 简单解析即可提取大部分内容，无需 LLM 理解。

**二八法则**：先基于 4 个 benchmark 任务反向梳理 Top 50 核心 API，只对这 50 个做深度抽取和强人工标注。

### 2.7 存储方案

**SQLite + FTS5**（第一版）

- 轻量、零外部依赖、支持全文检索
- 适合 API 量 < 1000 的场景
- 企业内网友好（无 DLL/Node.js 依赖问题）
- 后续可按需升级到向量库（ChromaDB/Qdrant）

不引入图数据库（GitNexus/LadybugDB），因为 Stage A 的检索需求本质是 API semantic retrieval，不是全程序调用图分析。

### 2.8 数据流

```
用户需求
  → retrieve API（SQLite + FTS5）
  → retrieve workflow cases
  → retrieve anti-patterns
  → assemble context
  → generate code
  → 自动化判卷
```

---

## 3. Stage B：MCP 执行层 + 融合机制

### 3.1 目标

根据 Stage A 实验结果，建立 MCP 执行层，并将 Grounding 和 Scoped 两条路线融合。

**核心原则**：节点化与图谱不是二选一，而是互补——图谱提供"知识"（API 语义、约束、示例），节点化提供"约束"（API 白名单、输入输出类型、执行边界）。两者融合形成完整的节点化智能仿真系统。

节点化即使命中率不提升也有独立价值：可进化性（节点独立优化）、可复用性（跨项目复用）、可观测性（每步结果可检查）。

### 3.2 AEDT MCP Server

**技术栈**：FastMCP（Python）

**5 个 MCP 工具**：

| 工具 | 功能 | 实现 |
|---|---|---|
| `search_api(query)` | 查 API 语义层 | SQLite + FTS5 检索 |
| `execute_script(code)` | 执行 pyaedt 代码 | 进程内 .NET interop |
| `get_model_info()` | 查 AEDT 当前状态 | pyaedt 实例属性 |
| `list_examples(task_type)` | 案例检索 | 从 workflow_cases/ 匹配 |
| `submit_solve()` | 提交求解 | pyaedt `analysis.solve()` |

### 3.3 持久化 pyaedt 实例

pyaedt 通过 pythonnet (.NET interop) 操作 AEDT Desktop，不是文件 IPC。

**架构**：

```
AI Client → MCP Server (FastMCP) → 持久化 pyaedt 实例 → AEDT Desktop
```

MCP Server 启动时初始化 `Hfss` 实例，后续工具调用直接操作该实例。

**关键约束**：
- pyaedt 实例是 .NET interop 对象，不能序列化，必须进程内透传
- 单 AEDT 实例本质上是单线程的，并行节点需排队串行执行
- 需要 Windows + AEDT Desktop 环境

### 3.4 Auto-fix 循环

```
执行代码 → 成功 → 返回结果
         → 失败 → 读取 traceback → 修正代码 → 重试（最多 3 次）
                            → 3 次仍失败 → 返回调度层重规划
```

### 3.5 执行队列

单 AEDT 实例串行化设计：

```python
class AEDTExecutionQueue:
    """AEDT 单实例执行队列，所有操作串行化。
    pyaedt 是同步的（.NET interop），队列管理异步提交、同步执行。"""

    def __init__(self, pyaedt_instance):
        self._instance = pyaedt_instance
        self._lock = asyncio.Lock()  # 保证同一时刻只有一个执行

    async def submit(self, code: str) -> ExecutionResult:
        """提交代码到执行队列，等待结果。内部串行执行。"""
        ...
```

### 3.6 Grounding + Scoped 融合机制

Stage B 的核心任务不是选择一条路线，而是建立两条路线的融合：

```
用户需求
    ↓
调度层（大模型）
    ↓ 规划 DAG
    ├── 节点 A（Scoped: API 白名单 3 个方法）
    │     ├── API 语义信息（Grounding: 从语义层检索约束和示例）
    │     ├── Anti-patterns（Grounding: 从陷阱库检索）
    │     └── 小模型生成代码
    ├── 节点 B（Scoped: API 白名单 5 个方法）
    │     ├── API 语义信息（Grounding）
    │     └── 中等模型生成代码
    └── ...
    ↓
执行层（AEDT MCP）
    ↓ 执行 + auto-fix
    ↓
反馈层（成功 → 下个节点 / 失败 → 重规划 / 长期 → 节点进化）
```

每个节点同时具备：
- **Scoped 约束**：API 白名单限定选择空间
- **Grounding 知识**：从语义层/案例库/陷阱库检索补充上下文

### 3.7 节点定义规范（最小化）

Stage B 先定义 5~10 个核心节点，按仿真流程分：

```python
NODE_CONFIG = {
    "id": "create_substrate",
    "name": "创建基板",
    "category": "geometry",
    "model": "qwen2.5-coder-7b",
    "api_scope": [
        "Hfss.modeler.create_box",
        "Hfss.assign_material",
    ],
    "inputs": {
        "hfss_app": {"type": "Hfss", "required": True},
        "length":   {"type": "float", "default": 10e-3},
        "width":    {"type": "float", "default": 10e-3},
        "material": {"type": "str",   "default": "FR4_epoxy"},
    },
    "outputs": {
        "substrate_id": {"type": "ObjectId"},
        "hfss_app":     {"type": "Hfss"},
    },
    "system_prompt": "你是一个 HFSS 几何建模专家。你只能使用以下 API：...",
}
```

节点粒度标准：一个节点对应一个"工程师会心算完成"的操作单元。

### 3.8 类型系统（最小化）

```
Hfss      — Hfss 应用实例（透传整个 app）
ObjectId  — 3D 模型中的物体 ID
FaceId    — 物体的某个面 ID
BoundaryId — 边界条件 ID
PortId    — 端口 ID
SetupId   — 求解设置 ID
float     — 标量
str       — 字符串
```

调度层在编排时做类型检查：`create_wave_port` 需要 `FaceId` 输入，但上游只输出 `ObjectId`，中间必须插一个 `select_face` 节点。

### 3.9 节点进化机制

```python
NODE_EVOLUTION = {
    "create_substrate": {
        "version": 1,
        "eval_metrics": {
            "api_hit_rate": 0.0,      # 待 Stage A benchmark 填充
            "runtime_error_rate": 0.0,
            "avg_tokens": 0,
        }
    }
}
```

进化触发条件：
- **报错驱动**：某类错误反复出现 → 优化 system_prompt 或 API 白名单
- **评估驱动**：benchmark 命中率低于阈值 → 调优
- **成本驱动**：更小的模型也能达标 → 降级模型

---

## 4. 项目目录结构

```
ansys-agent/
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-05-08-aedt-node-sim-design.md  ← 本文档
├── knowledge/
│   ├── api_semantics.json
│   ├── workflow_cases/
│   │   ├── hfss_patch_antenna.yaml
│   │   ├── waveguide_filter.yaml
│   │   ├── differential_pair.yaml
│   │   └── coaxial_feed.yaml
│   └── anti_patterns/
│       ├── waveport_no_contact.md
│       ├── negative_airbox_silent.md
│       └── forgot_ground_plane.md
├── benchmarks/
│   ├── tasks/
│   │   ├── hfss_patch_antenna.md
│   │   ├── waveguide_filter.md
│   │   ├── differential_pair.md
│   │   └── coaxial_feed.md
│   ├── reference_scripts/
│   │   ├── hfss_patch_antenna.py
│   │   ├── waveguide_filter.py
│   │   ├── differential_pair.py
│   │   └── coaxial_feed.py
│   └── validation/
│       ├── validate_patch_antenna.py
│       ├── validate_waveguide_filter.py
│       ├── validate_differential_pair.py
│       └── validate_coaxial_feed.py
├── src/
│   ├── extractor/
│   │   └── build_api_semantics.py   ← API 语义抽取器
│   ├── retrieval/
│   │   ├── semantic_store.py        ← SQLite + FTS5 存储
│   │   └── retriever.py             ← 检索逻辑
│   ├── pipeline/
│   │   └── grounding_pipeline.py    ← Stage A prompt chain
│   ├── mcp_server/
│   │   └── aedt_mcp_server.py       ← Stage B AEDT MCP Server
│   ├── nodes/
│   │   ├── node_registry.py         ← 节点注册表
│   │   ├── base_node.py             ← 节点基类
│   │   └── nodes/                   ← 具体节点定义
│   │       ├── create_substrate.py
│   │       ├── create_patch.py
│   │       ├── create_feed.py
│   │       ├── add_boundary.py
│   │       ├── create_setup.py
│   │       └── ...
│   ├── executor/
│   │   └── aedt_queue.py            ← 执行队列
│   └── evaluator/
│       └── benchmark_runner.py      ← 自动化判卷
├── tests/
└── pyproject.toml
```

---

## 5. 风险与待决策

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| Grounding 假设不成立 | 后续架构价值下降 | Stage A 三组对比实验快速验证 |
| pyaedt .NET interop 不稳定 | MCP 执行层不可用 | gRPC 模式备选 |
| AEDT 单进程限制 | 并行节点无法真正并行 | 执行队列串行化 |
| API 语义自动提取质量低 | Grounding 效果差 | 人工标注 Top 50 核心 API |
| pyaedt 频繁发版 | 图谱/语义层过时 | 版本同步脚本 + CI |

**待决策**：
- benchmark 使用的 LLM 模型选择（Claude Sonnet vs GPT-4 vs 本地模型）
- 本地环境是否有 AEDT Desktop 可用
- 小模型（7B-14B）的部署方式（本地 Ollama vs API）

---

## 6. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-05-08 | Stage A + B 设计规格书，综合四份讨论稿 |
