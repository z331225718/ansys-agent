# AEDT-MCP 节点化智能仿真系统 — 最终设计方案

> 本文档基于四方辩论（务实派/架构派/工程派/迭代派）的综合评判结果。
> 辩论日期：2026-05-08

---

## 1. 核心裁决

### 1.1 知识图谱工具选型

**判定：采用"务实派+迭代派"的轻量方案，保留"架构派"的扩展路径**

| 阶段 | 存储方案 | 原因 |
|------|----------|------|
| **Stage A（验证阶段）** | SQLite + FTS5 + JSON | 离线部署友好、零运维负担、快速迭代 |
| **Stage B（节点化阶段）** | SQLite + 向量库(可选) | 语义搜索增强，但仍轻量 |
| **Stage C（成熟阶段）** | 可选引入GitNexus | 仅当Process追踪价值被验证后 |

**关键裁决依据**：
- LLM_A和LLM_B_reviewA的共识：**"不要一开始上图数据库"** 是成熟工程直觉
- PolyForm Noncommercial许可的商业风险真实存在
- 真正的retrieval需求是API语义，不是全程序调用图
- **保留扩展性**：SQLite存储可设计为"方法全限定名"索引，后续可桥接GitNexus

---

### 1.2 开发路线优先级

**判定：采用"务实派"三阶段路线，融合"迭代派"自动化判卷**

```
Stage A（1-2周）离线API Grounding实验
├── clone pyaedt源码
├── build_api_semantics.py（抽取Top 50核心API）
├── 收集HFSS examples（antennas/filters/waveguide）
├── BM25/FTS5检索 + Prompt chain
├── Benchmark数据集（3-5个任务）
├── 自动化判卷（Validation Script）
└── 人工评估：syntax/runtime/semantic pass率

Stage B（2-4周）MCP + AEDT联动
├── FastMCP Server（5工具）
├── 持久化pyaedt实例（.NET interop）
├── execute_script + auto-fix loop
├── Queue机制（单线程AEDT）
└── 核心节点实现（建基板/建端口/设求解）

Stage C（后续）架构扩展
├── 节点化完整实现（类型系统）
├── 可选GitNexus（需验证Process价值）
├── Semantic validation（物理正确性）
├── 节点进化机制
└── Multimodal（截图分析）
```

**关键裁决依据**：
- **LLM_A核心洞察**：不验证"图谱是否有用"，后续工程都是无根之木
- **LLM_B_reviewA补充**：自动化判卷实现高频量化迭代
- 架构派的三层设计正确，但**优先级应后置**

---

### 1.3 节点化实现策略

**判定：Stage B开始节点化，采用"架构派"类型系统+工程派并发管理**

**首批核心节点（工程派建议）**：

| 节点ID | 功能 | API白名单 | 模型 |
|--------|------|-----------|------|
| `create_substrate` | 建基板 | `Hfss.modeler.create_box`, `assign_material` | Qwen2.5-Coder-7B |
| `create_patch` | 建贴片 | `create_rectangle`, `set_working_coordinate_system` | 7B |
| `create_wave_port` | 建波导端口 | `create_wave_port`, `assign_coaxial` | Claude Sonnet（需物理判断）|
| `create_airbox` | 建空气盒 | `create_airbox`, `assign_radiation_boundary` | 7B |
| `create_setup` | 求解设置 | `create_setup`, `create_frequency_scan` | 7B |

**类型系统（架构派设计）**：

```python
TYPES = {
    "Hfss": "应用实例（透传）",
    "ObjectId": "3D物体ID",
    "FaceId": "面ID",
    "PortId": "端口ID",
    "SetupId": "求解设置ID",
    "Material": "材料定义",
}
```

**并发管理（工程派设计）**：
- AEDT MCP Server内部Queue + 状态锁
- DAG中并行节点排队到AEDT实例串行执行

---

### 1.4 验证体系与质量保证

**判定：融合四方观点，三层验证体系**

| 层级 | 方法 | 阶段 | 负责方 |
|------|------|------|--------|
| **L1：自动化判卷** | Validation Script + assert | Stage A | 迭代派 |
| **L2：人工评估** | syntax/runtime/semantic pass统计 | Stage A | 务实派 |
| **L3：节点进化** | 报错驱动/评估驱动/成本驱动 | Stage C | 架构派 |

**Benchmark数据集设计（务实派+迭代派）**：

```
tasks/
├── hfss_patch_antenna.md
│   ├── 自然语言需求："设计2.4GHz贴片天线"
│   ├── reference_script（手写pyaedt）
│   ├── validation_script（自动判卷）
│   └── expected_outputs（S参数/材料/边界）
├── waveguide_filter.md
├── microstrip_line.md
└── differential_pair.md
```

**反直觉陷阱库（工程派+迭代派）**：

```json
{
  "trap_id": "waveport_background_contact",
  "api": "Hfss.create_wave_port",
  "warning": "定义WavePort前必须确保端口面与背景完全接触",
  "error_type": "boundary_condition_error",
  "fix": "先检查端口面是否接触背景，否则调整几何或使用lumped port"
}
```

---

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│  Stage A：离线API Grounding实验（验证核心假设）               │
│                                                              │
│  Benchmark数据集 → API语义抽取器 → SQLite+FTS5               │
│  → Prompt chain → 自动化判卷 → Baseline vs Grounded对比      │
│                                                              │
│  输出：API命中率/运行时成功率/修复次数量化数据                 │
└─────────────────────────────────────────────────────────────┘
                    ↓ 验证成功后进入Stage B
┌─────────────────────────────────────────────────────────────┐
│  Stage B：MCP + 节点化原型                                   │
│                                                              │
│  AI Client → FastMCP Server → 持久化pyaedt实例 → AEDT       │
│                                                              │
│  工具：search_api / execute_script / get_model_info          │
│        / submit_solve / auto_fix_loop                       │
│                                                              │
│  核心节点：建基板/建贴片/建端口/建空气盒/建求解                │
│  类型系统：Hfss/ObjectId/FaceId/PortId/SetupId               │
│  并发管理：Queue + 状态锁（单线程AEDT）                       │
└─────────────────────────────────────────────────────────────┘
                    ↓ 节点质量达标后进入Stage C
┌─────────────────────────────────────────────────────────────┐
│  Stage C：完整架构扩展                                       │
│                                                              │
│  调度层（大模型：Claude Opus级）                              │
│  ├── 理解意图 → 规划DAG → 分配节点                           │
│  ├── GitNexus发现节点（可选）                                │
│                                                              │
│  工作流层：[建基板]→[贴片]→[馈电]→[边界]→[求解]              │
│                                                              │
│  节点层：                                                    │
│  ├── API白名单（3-5个方法）                                  │
│  ├── GitNexus图谱切片（可选）                                │
│  ├── API语义约束                                            │
│  ├── Few-shot示例                                           │
│  └── 可配模型（7B-14B / Sonnet）                             │
│                                                              │
│  节点进化机制：报错驱动/评估驱动/成本驱动                     │
│                                                              │
│  Semantic validation：几何/仿真/物理正确性检查               │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 关键设计决策汇总

| # | 决策点 | 最终方案 | 来源 |
|---|--------|----------|------|
| 1 | 图谱工具选型 | Stage A: SQLite+FTS5; Stage C可选GitNexus | 务实派+迭代派 |
| 2 | 开发路线 | 三阶段：验证→节点化→扩展 | 务实派 |
| 3 | 验证体系 | Benchmark+自动化判卷+人工评估 | 务实派+迭代派 |
| 4 | 首批节点 | 5个核心节点（建基板/贴片/端口/空气盒/求解） | 工程派 |
| 5 | 类型系统 | Hfss/ObjectId/FaceId/PortId/SetupId | 架构派 |
| 6 | 并发管理 | Queue+状态锁（单线程AEDT） | 工程派 |
| 7 | API抽取 | Top 50核心API深度打标（二八法则） | 迭代派 |
| 8 | 反直觉陷阱库 | Common Traps独立存储，提前喂给LLM | 工程派+迭代派 |
| 9 | 商业授权 | Stage A避开GitNexus，Stage C需评估 | 工程派 |
| 10 | 模型分层 | 调度层大模型/节点层小模型 | 架构派 |

---

## 4. 下一步行动（按优先级排序）

### 立即执行（本周）

| # | 任务 | 输出 | 优先级 |
|---|------|------|--------|
| 1 | clone pyaedt源码到独立目录 | `D:\code\pyaedt-src` | P0 |
| 2 | 设计Benchmark数据集（3个任务） | `tasks/*.md` | P0 |
| 3 | 编写build_api_semantics.py | `api_semantics.json` | P0 |
| 4 | 实现SQLite+FTS5检索原型 | `search_api.py` | P0 |
| 5 | 编写Validation Script自动判卷 | `validation/*.py` | P0 |

### 短期目标（1-2周）

| # | 任务 | 输出 | 阶段 |
|---|------|------|------|
| 6 | 运行Baseline vs Grounded对比实验 | 实验报告 | Stage A |
| 7 | 统计API命中率/运行时成功率数据 | 量化数据 | Stage A |
| 8 | 验证"图谱是否有用"核心假设 | 决策点 | Stage A |

### 中期目标（2-4周）

| # | 任务 | 输出 | 阶段 |
|---|------|------|------|
| 9 | 实现FastMCP Server（5工具） | `aedt_mcp_server.py` | Stage B |
| 10 | 持久化pyaedt实例测试 | 连接验证 | Stage B |
| 11 | Queue机制实现 | 并发管理 | Stage B |
| 12 | 5个核心节点原型 | 节点定义 | Stage B |

### 长期目标（后续）

| # | 任务 | 输出 | 阶段 |
|---|------|------|------|
| 13 | 节点化完整实现（类型系统） | 完整架构 | Stage C |
| 14 | 节点进化机制 | 自动优化 | Stage C |
| 15 | Semantic validation | 物理检查 | Stage C |
| 16 | 可选GitNexus集成 | 图数据库 | Stage C |

---

## 5. 辩论观点摘要

### 务实派（来源：LLM_A.txt）

- 停下画架构图的手，去跑第一个Benchmark
- 图数据库不是第一优先级，SQLite+FTS5完全够用
- 先验证"图谱是否有用"，后续工程才有根
- 收缩目标只做HFSS一个子域
- 案例库比API图谱更重要

### 架构派（来源：设计文档）

- 三层架构（调度层/工作流层/节点层）
- GitNexus三决定性优势：Process追踪/context工具/Cypher查询
- 双存储架构：GitNexus存代码结构，独立存储存API语义
- 节点规范：API白名单+graph_slice+类型系统
- 模型分层：调度层大模型/节点层小模型

### 工程派（来源：LLM_B.txt）

- 节点化是消除语义级错误的杀手锏
- 离线部署优先，7B-14B小模型降低云端依赖
- AEDT单线程，需Queue机制和状态锁
- GitNexus PolyForm许可有商业风险，Graphify降级方案
- 核心节点起始：建基板/建端口/设求解频率

### 迭代派（来源：LLM_B_reviewA.txt）

- 完全认同务实路线
- 自动化判卷：Validation Script实现高频量化迭代
- 二八法则：Top 50核心API深度打标
- 反直觉陷阱库：Common Traps独立存储
- 高频客观量化迭代验证检索策略

---

## 6. 核心假设验证清单

| 假设 | 验证方法 | 成功标准 | 决策点 |
|------|----------|----------|--------|
| 图谱能提升API命中率 | Baseline vs Grounded对比 | 命中率提升 >30% | 是否继续Stage B |
| 语义层能减少silent failure | 错误率统计 | silent failure <10% | 是否投入语义层 |
| MCP+长连接AEDT稳定 | 连接测试 | 连接成功率 >95% | 是否调整架构 |
| retrieval token/延迟可控 | 性能测试 | token <500/次，延迟 <2s | 是否优化检索 |

---

## 7. 版本记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-05-08 | 基于四方辩论综合评判的最终设计方案 |