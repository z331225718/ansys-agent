# AEDT-MCP 节点化智能仿真系统 — 设计讨论稿

> 本文档汇总初步讨论与思考，作为后续综合设计与正式设计文档的基础。
> 所有方案均标注成熟度（💡构想 / 🔧可落地 / ✅已验证），供讨论时聚焦优先级。
> 所有已知问题和修正以 ⚠️ 标注。

---

## 1. 背景与动机

### 1.1 从 text-to-cae 说起

[text-to-cae](https://github.com/Cai-aa/text-to-cae) 是一个基于 **Dassault Abaqus/CAE** 的自然语言驱动仿真项目，架构为：

```
AI 客户端 → Abaqus MCP（薄接口） → Abaqus/CAE（执行） → 浏览器 Viewer（可视化）
```

其 MCP Server 暴露 8 个工具（`check_abaqus_connection` / `execute_script` / `get_model_info` / `list_jobs` / `submit_job` / `get_odb_info` / `get_viewport_image` / `ping`），全部调用的是 Abaqus Python API（`from abaqus import mdb`），工具描述本身只占几百 token，**不包含任何 Abaqus API 细节**。

⚠️ **重要区分**：text-to-cae 和 Abaqus MCP 封装的是 Abaqus/CAE，与 Ansys AEDT 无关。pyaedt 的 .NET/gRPC 通信机制与 Abaqus 的文件 IPC 完全不同。我们能从 text-to-cae 借鉴的是**架构模式**（MCP Server + CAE 插件 + 迭代纠错循环），不是代码实现。

### 1.2 核心问题：API 命中率低

这种"薄接口 + 厚执行"的设计存在根本性缺陷：

- **Abaqus Python API 体量庞大**（Scripting Reference Manual 超过 2000 页，数十个模块），但 LLM 训练数据中 Abaqus 脚本占比极低（GitHub 相关仓库仅几千个，SO 标签问题不到 2000 个）。
- MCP 把所有准确性责任推给了 LLM 的 API 知识储备，而 LLM 在这个领域的知识储备是薄的。
- 代码命中率随操作复杂度急剧下降：

  | 操作类型 | 估计命中率 | 原因 |
  |---|---|---|
  | 创建 Part、定义 Material、设 BC | 较高 | 模式固定，训练数据常见 |
  | 复杂接触定义、耦合约束 | 中低 | 参数组合多，枚举值容易写错 |
  | ODB 后处理、字段提取 | 低 | API 层级深，方法名和参数记忆模糊 |
  | 用户子程序（VUMAT/UMAT） | 极低 | 涉及 Fortran，几乎不在 Python 语料里 |

- abaqus-mcp 的唯一补救机制是**迭代纠错循环**（AI 写代码 → Abaqus 报错 → AI 读 traceback → 修正 → 再执行），本质上是用运行时试错替代训练时记忆。
- **最危险的失败模式**不是语法报错（AI 能看到 traceback 可修），而是语义级错误（代码跑了没报错但物理模型是错的，AI 和用户都看不出来）。

### 1.3 项目定位的启示

text-to-cae 的实际定位是**给懂 Abaqus 的人用的提效工具**，不是给不懂的人用的教学工具。如果用户不懂 Abaqus，AI 生成的东西无法验证，"跑通"和"跑对"之间的差距用户看不见。

**对我们的启示**：如果要做一个真正降低门槛的系统，不能走"薄 MCP + 让 AI 凭记忆猜 API"这条路，需要更根本的架构创新。

---

## 2. 系统目标

构建一个**自然语言驱动的 Ansys AEDT 仿真系统**，核心目标：

1. **API 准确性**：生成的 pyaedt 代码命中率远高于裸 LLM 水平
2. **可验证性**：每一步操作的结果可检查，语义级错误可被捕获
3. **可复用性**：工作流模块可跨项目复用
4. **可进化性**：模块可独立优化，系统整体能力持续提升
5. **成本可控**：复杂判断用大模型，简单生成用小模型

---

## 3. 方案一：知识图谱 + MCP（基础方案）

### 3.1 核心思路

用 ansys.aedt.core（原 pyaedt）的开源源码生成 API 知识图谱，让 LLM 在生成代码前**先查证再写**，而非凭记忆猜。

### 3.2 为什么比 abaqus-mcp 更可行

ansys.aedt.core 是开源的（GitHub 1.4k+ stars），这意味着：

- 源码完整可分析，不需要逆向工程 API 结构
- LLM 对它的训练语料覆盖率远高于 Abaqus
- 图谱生成有确定性来源，不需要人工整理

### 3.3 图谱分层策略 💡

| 层级 | 内容 | 何时加载 | 估计 token |
|---|---|---|---|
| L1 索引 | 模块名、类名、一句话职责 | 始终加载 | ~2k |
| L2 签名 | 方法名、参数列表、返回类型 | 按需检索 | ~500/方法 |
| L3 语义 | 参数含义、枚举值、使用约束、注意事项 | 按需检索 | ~300/方法 |
| L4 示例 | 该方法在案例中的实际用法 | 按需检索 | ~800/示例 |

### 3.4 图谱内容：不仅需要静态结构，更需要使用约束 🔧

纯静态分析类继承关系不够，真正有用的是**使用约束**：

```json
{
  "class": "Hfss",
  "method": "create_airbox",
  "signature": "create_airbox(self, airbox_input) -> str",
  "constraints": [
    "airbox_input 可以是 float 或 list[float]，float 时各向等距",
    "必须在至少一个物体存在之后调用",
    "自动创建 air 边界条件"
  ],
  "common_errors": [
    "未先创建物体 → RuntimeError",
    "传入负值 → 静默创建但求解报错"
  ],
  "examples_ref": ["hfss_antenna_tutorial"]
}
```

这种约束信息是 LLM 最缺的，也是从源码里最难自动提取的，需要**半自动**方式：静态分析打底，人工标注补关键约束。

### 3.5 ⚠️ 双存储架构：代码结构与 API 语义分离

**关键修正**：GitNexus 的图 schema 是代码结构性的，节点类型为 Function、Class、Method、Interface 等 31 种，边类型为 CALLS、IMPORTS、EXTENDS、HAS_METHOD 等。我们要注入的"参数约束、枚举值、常见错误"不属于这个 schema。

虽然 LadybugDB 支持自定义节点类型，但 GitNexus 的高层工具（`context`、`impact`、`query`）的设计假设是查询代码结构关系，不会识别自定义语义类型。扩展 schema（加 ApiConstraint、EnumValue、ErrorPattern 节点和 CONSTRAINS、ENUM_OF 边）虽然技术上可行，但会导致 GitNexus 的高层工具无法理解这些节点。

**推荐方案：双存储，不侵入 GitNexus**

```
┌──────────────────────────────────────┐
│  存储一：GitNexus（代码结构）          │
│  存储：LadybugDB                      │
│  内容：类继承、方法签名、调用链、模块依赖│
│  查询工具：query / context / impact / │
│            cypher                     │
│  更新：gitnexus analyze --embeddings  │
└──────────────────────────────────────┘

┌──────────────────────────────────────┐
│  存储二：API 语义层（独立存储）         │
│  存储格式：JSON / SQLite / 向量库 🔧   │
│  内容：参数约束、枚举值、常见错误、     │
│        docstring 解析结果             │
│  查询工具：search_api（自建 MCP 工具） │
│  更新：build_api_semantics.py 重跑    │
└──────────────────────────────────────┘
```

两个存储之间通过**方法全限定名**（如 `Hfss.create_airbox`）做关联。`search_api` 查到语义信息后，可选择性调用 GitNexus 的 `context("Hfss.create_airbox")` 补充调用链上下文。

语义层存储选型待定：
- **JSON 文件**：最简单，适合 API 量 <1000，不支持向量搜索
- **SQLite + FTS5**：支持全文检索，轻量，适合中等规模
- **向量库（如 ChromaDB/Qdrant）**：支持语义搜索，但引入额外依赖

### 3.6 ⚠️ GitNexus 索引前提：需要 git 仓库

GitNexus 的 `gitnexus analyze` 需要目标目录是一个 git 仓库。`D:\code\ansys-api` 是 pip 安装后的包目录（pyaedt 0.26.3 + pyedb 0.74.0），没有 `.git`，无法直接索引。

**正确做法**：

```bash
# 在单独目录克隆 pyaedt 源码
git clone https://github.com/ansys/pyaedt.git D:\code\pyaedt-src
cd D:\code\pyaedt-src

# 在源码目录跑 GitNexus
gitnexus analyze --embeddings --skills
```

### 3.7 ⚠️ 商业授权问题

GitNexus 使用 **PolyForm Noncommercial-1.0.0** 许可证。如果用途涉及商业场景，需要向 AkonLabs 获取单独授权。此外，GitNexus 底层的 LadybugDB 当前为 v0.16.x（pre-1.0），是 KuzuDB 的 rebrand 过渡期，API 稳定性需关注。

**待决策**：
- 如果仅个人/研究使用，开源许可足够
- 如果涉及商业交付，需评估授权成本或考虑 Graphify（MIT 许可）作为替代

### 3.8 "强制 LLM 读取图谱"的实现方式 💡

| 方案 | 机制 | 优点 | 缺点 |
|---|---|---|---|
| A. 系统提示约束 | 工具描述中写明"必须先查图谱" | 简单 | 依赖模型遵循指令，不保证 100% |
| B. 两步执行 | `execute_script` 前置校验，未查图谱则拒绝执行 | 真正强制 | 需 AST 解析，实现复杂 |
| C. 自动注入（推荐） | MCP Server 在执行前自动检索图谱，发现可疑用法先返回修正建议 | AI 不查也得查 | 检索质量依赖图谱完备度 |

### 3.9 案例库的作用 🔧

- **代码结构（GitNexus）**解决"API 的类和方法在哪里、怎么调用"
- **API 语义层（独立存储）**解决"参数约束、枚举值、常见错误"
- **案例库**解决"API 怎么组合完成一个完整任务"
- **MCP**解决"API 怎么执行"

案例建议加工成结构化摘要，而非存原始 `.py` 文件：

```
案例: hfss_antenna_array
功能: 创建贴片天线阵列并求解 S 参数
涉及 API: Hfss, create_rectangle, assign_coaxial, create_setup, create_frequency_scan
关键步骤: 1.建基板 2.贴片 3.馈电 4.边界 5.求解
源文件: examples/hfss_antenna_array.py
```

### 3.10 ⚠️ AEDT 的 IPC 机制与 Abaqus 完全不同

text-to-cae 用文件 IPC（JSON 命令/结果文件）跟 Abaqus 通信，因为 Abaqus 内核不支持网络服务。

pyaedt 的通信方式是 **.NET interop（pythonnet）**，直接在进程内调用 AEDT 的 COM/API 接口。这意味着：

- `execute_script` 不是发给另一个进程，而是调用 `Hfss` 对象的方法
- `get_model_info` 需要一个活的 `pyaedt.Hfss` 实例
- `submit_solve` 调用的是 `analysis.solve()`

**AEDT MCP Server 需要维护一个长连接的 pyaedt 实例，不是文件 IPC。**

正确架构：

```
AI Client → MCP Server (FastMCP) → 持久化 pyaedt 实例 → AEDT Desktop
```

MCP Server 启动时初始化 `Hfss`/`Maxwell3d`/`Icepak` 实例，后续工具调用直接操作该实例。不支持文件 IPC 那种"写命令文件等结果"的模式。

### 3.11 修正后的方案一架构总览

```
AI 客户端
    ↓ MCP 协议（同时连两个 MCP Server）
    ├── GitNexus MCP Server（13 个工具）
    │     ├── query("create_airbox")      ← 搜索代码结构
    │     ├── context("Hfss")             ← 360° 符号视图（调用链/继承）
    │     ├── impact("create_airbox")     ← 影响范围
    │     └── cypher("MATCH ...")         ← 精确图查询
    │
    └── AEDT MCP Server（自建，5 个工具）
          ├── search_api(query)           ← 查 API 语义层（独立存储，非 GitNexus 图）
          ├── execute_script(code)        ← 执行 pyaedt 代码（进程内 .NET interop）
          ├── get_model_info()            ← 查 AEDT 当前状态（pyaedt 实例）
          ├── list_examples(task_type)    ← 案例检索（独立案例库）
          └── submit_solve()              ← 提交求解（pyaedt analysis.solve()）
```

关键变化（相对于初版）：
1. `search_api` 查独立的语义存储（JSON/SQLite），不注入 GitNexus 图
2. AEDT MCP Server 用进程内 pyaedt 实例，不用文件 IPC
3. 先 clone pyaedt git repo 到单独目录，再跑 `gitnexus analyze --embeddings --skills`
4. 需确认 GitNexus 商业授权需求

### 3.12 方案一的局限

- 图谱再全，LLM 依然是在一个较大的 API 空间里做选择，命中率提升有限
- 检索质量决定一切：检索到无关子图比没有图谱更糟
- 图谱维护成本高（pyaedt 几乎每月发版）
- 语义级错误仍然无法防御

---

## 4. 方案二：ComfyUI 式节点化工作流（进阶方案）

### 4.1 核心思路

不追求一个万能 AI，而是**构建一个让普通 AI 也能稳定产出的工程体系**。

把"在几万个 API 里找对的那个"的问题，变成"只有 5 个 API 可选"。

```
原始问题：AI 面对整个 ansys.aedt.core (5000+ 方法) → 命中率低
图谱方案：AI 先查图谱缩小范围到 50 个方法           → 命中率中
节点方案：AI 只需要从 3 个方法里选                   → 命中率高
```

### 4.2 三层架构 💡

```
┌─────────────────────────────────────────────────┐
│  调度层（大模型：Claude Opus / GPT-4 级别）       │
│                                                   │
│  职责：理解意图 → 规划工作流 → 分配节点 → 处理异常  │
│  上下文：用户需求 + 节点目录 + 全局状态             │
│  不需要知道任何 pyaedt API 细节                    │
└──────────┬──────────────────────────────────────┘
           │ 下发任务
           ▼
┌─────────────────────────────────────────────────┐
│  工作流层（DAG 有向无环图）                        │
│                                                   │
│  [建基板] → [贴片] → [馈电] → [边界] → [求解]     │
│     ↓         ↓        ↓                        │
│  (参数)    (参数)    (参数)                       │
└──────────┬──────────────────────────────────────┘
           │ 每个节点独立执行
           ▼
┌─────────────────────────────────────────────────┐
│  节点层（每个节点一个独立上下文 + 可配模型）         │
│                                                   │
│  [建基板节点]   模型: Qwen2.5-Coder-7B            │
│   输入: 长/宽/厚/材料                             │
│   输出: board_id                                  │
│   API范围: Hfss.modeler.create_box               │
│           Hfss.assign_material                    │
│                                                   │
│  [馈电节点]     模型: Claude Sonnet                │
│   输入: patch_id, 馈电类型                        │
│   输出: port_id                                   │
│   API范围: Hfss.create_wave_port                  │
│           + 复杂判断（同轴/微带/Lump选哪个）        │
└─────────────────────────────────────────────────┘
```

### 4.3 节点定义规范 💡

每个节点是一个自包含单元，包含：

```python
NODE_CONFIG = {
    "id": "create_substrate",
    "name": "创建基板",
    "category": "geometry",
    "model": "qwen2.5-coder-7b",       # 默认模型（可覆盖）
    "model_temperature": 0.1,           # 代码生成低温度

    # API 白名单（约束 AI 的选择范围）
    "api_scope": [
        "Hfss.modeler.create_box",
        "Hfss.assign_material",
        "Hfss.modeler.set_working_coordinate_system",
    ],

    # 知识图谱切片（只加载相关子图）
    "graph_slice": "geometry/box_and_material",

    # 类型化输入输出
    "inputs": {
        "hfss_app": {"type": "Hfss", "required": True},
        "length":   {"type": "float", "default": 10e-3, "unit": "meter"},
        "width":    {"type": "float", "default": 10e-3, "unit": "meter"},
        "height":   {"type": "float", "default": 0.5e-3, "unit": "meter"},
        "material": {"type": "str",   "default": "FR4_epoxy"},
    },
    "outputs": {
        "substrate_id": {"type": "ObjectId"},
        "hfss_app":     {"type": "Hfss"},   # 透传
    },

    # Few-shot 示例
    "examples": [...],

    # 系统提示（节点的"人设"和约束）
    "system_prompt": """你是一个 HFSS 几何建模专家。你只能使用以下 API：
- Hfss.modeler.create_box(origin, sizes, name, material)
- Hfss.assign_material(object, material_name)
输入：基板尺寸和材料
输出：创建基板的 pyaedt 代码
约束：坐标原点在基板中心底部
""",
}
```

### 4.4 类型系统：节点间数据流的命脉 💡

ComfyUI 之所以能工作，核心是每个节点的输入输出有类型约束（IMAGE、MASK、CONDITIONING 等），连错了类型不匹配就接不上。

AEDT 节点需要的类型系统：

```python
TYPES = {
    "Hfss":       "Hfss 应用实例（透传整个 app）",
    "Maxwell3d":  "Maxwell3d 应用实例",
    "Icepak":     "Icepak 应用实例",
    "ObjectId":   "3D 模型中的物体 ID",
    "FaceId":     "物体的某个面 ID",
    "EdgeId":     "物体的某条边 ID",
    "BoundaryId": "边界条件 ID",
    "PortId":     "端口 ID",
    "SetupId":    "求解设置 ID",
    "MeshId":     "网格操作 ID",
    "PlotId":     "后处理图表 ID",
    "float":      "标量",
    "str":        "字符串",
    "dict":       "结构化数据",
    "Material":   "材料定义",
}
```

调度层在编排时可做类型检查：`create_wave_port` 需要 `FaceId` 输入，但上游只输出 `ObjectId`，中间必须插一个 `select_face` 节点。

### 4.5 节点粒度判断标准 💡

| 太粗（应拆分） | 合适 | 太细（应合并） |
|---|---|---|
| "设计整个天线" | "创建基板" | "创建基板的每条边" |
| "完成所有后处理" | "创建波导端口" | "创建端口的每个面选择" |
| | "添加辐射边界" | "创建空气盒子的六面" |

**判断标准**：一个节点对应**一个"工程师会心算完成"的操作单元**。

### 4.6 模型分配策略 💡

| 任务类型 | 模型选择 | 原因 |
|---|---|---|
| 节点内代码生成（API 白名单 ≤5） | 7B-14B 小模型 | 选择空间极小，小模型足够 |
| 需要物理判断的节点（馈电选型、材料匹配） | 中等模型（~70B） | 需要领域推理能力 |
| 调度编排 | 大模型（Opus/4 级） | 需要理解意图、规划工作流、处理异常 |
| 参数提取（从自然语言提取结构化参数） | 小模型 | 模式匹配，不需要推理 |
| 错误诊断（traceback → 修正建议） | 中等模型 | 需要理解报错语义 |

### 4.7 节点进化机制 💡

节点可以随使用逐步优化：

```python
NODE_EVOLUTION = {
    "create_substrate": {
        "version": 7,
        "mutations": [
            {"v2": "修复了坐标原点偏移问题", "trigger": "error: KeyError on origin"},
            {"v3": "支持多材质叠层基板",     "trigger": "user_request"},
            {"v4": "自动计算推荐尺寸",       "trigger": "user_request"},
            {"v5": "优化 system_prompt，API 命中率 78%→95%", "trigger": "eval_score"},
            {"v6": "从 Claude Haiku 切换到 Qwen2.5-Coder-7B，成本降 90%", "trigger": "cost_optimization"},
            {"v7": "增加坐标系统验证步骤",    "trigger": "error: wrong CS"},
        ],
        "current_eval": {
            "api_hit_rate": 0.95,
            "runtime_error_rate": 0.03,
            "avg_tokens": 180,
            "avg_latency_ms": 450,
        }
    }
}
```

进化触发条件：

- **报错驱动**：某类错误反复出现 → 自动优化 system_prompt 或 API 白名单
- **评估驱动**：跑 benchmark → 命中率低于阈值 → 触发调优
- **成本驱动**：评估发现更小的模型也能达标 → 降级模型
- **需求驱动**：用户请求新功能 → 扩展节点能力

---

## 5. 图谱工具选型：Graphify vs GitNexus

### 5.1 对比总览

| 维度 | Graphify | GitNexus |
|---|---|---|
| **定位** | 代码→知识图谱的通用转换器 | 代码智能引擎（强调执行流和影响分析） |
| **语言** | Python | Node.js/TypeScript |
| **存储** | `graph.json`（JSON 文件） | LadybugDB（原生图数据库，持久化） |
| **解析引擎** | tree-sitter AST | tree-sitter AST |
| **MCP 工具数** | 4 个 | **13 个** ⚠️（非 16，3 个 group 工具在 ARCHITECTURE.md 中标记为"intentionally not introduced"） |
| **查询语言** | 自有 JSON 路径查询 | **Cypher**（Neo4j 标准图查询语言） |
| **核心特色** | 概念节点+聚类+God Node 发现 | **执行流追踪（Process）**+ 影响分析+ 变更检测 |
| **语义搜索** | 无 | BM25 + 语义向量 + RRF 融合排序 |
| **多仓库** | `merge-graphs` 手动合并 | 原生 Group，跨仓查询执行流 |
| **许可证** | MIT | ⚠️ PolyForm Noncommercial-1.0.0 |
| **星数** | ~3k | ~6k+ |

### 5.2 场景适配对比

| 需求 | Graphify | GitNexus |
|---|---|---|
| 解析 Python 类/方法/继承 | ✅ | ✅ |
| 追踪 API 调用链 | ❌ 只做概念关联 | ✅ Process 追踪，天然适合 |
| 按需检索"某方法的上下游" | ⚠️ `get_neighbors` 粒度粗 | ✅ `context` 工具 360° 符号视图 |
| 精确图查询 | ❌ 无图查询语言 | ✅ Cypher |
| 影响分析 | ❌ | ✅ `impact` 工具 |
| 和 AEDT MCP 集成 | ✅ MCP Server 内置 | ✅ MCP Server 内置，工具更丰富 |
| 大型 Python 库的存储效率 | ⚠️ 单 JSON，大了慢 | ✅ LadybugDB 原生图存储 |
| 商业友好度 | ✅ MIT 许可 | ⚠️ 需商业授权 |

### 5.3 推荐：GitNexus 🔧（需确认授权）

三个决定性优势：

1. **Process（执行流）**：能追踪 `Hfss.__init__` → `Modeler3D.create_rectangle` → `Primitives.assign_material` 这样的调用链，这正是 AI 写代码时最需要的信息
2. **Cypher 查询**：可以写精确的图查询，如 `MATCH (cls:Class {name: 'Hfss'})-[:HAS_METHOD]->(m:Method) WHERE m.name STARTS WITH 'create_' RETURN m.name, m.signature`
3. **`context` 工具**：给 AI 一个 360° 符号视图，写代码前调一次就能拿到完整使用上下文

⚠️ 如果商业授权不可接受，Graphify（MIT）可作为降级方案，牺牲执行流追踪和 Cypher 查询能力。

### 5.4 Graphify 的备选价值

Graphify 的 Python 原生特性在某些场景更顺手（比如与 pyaedt 同一技术栈，AST 后处理更方便），可作为辅助工具使用。

### 5.5 LadybugDB 状态跟踪 ⚠️

LadybugDB 当前为 v0.16.x（pre-1.0），是 KuzuDB 的 rebrand 过渡期。API 可能在后续版本变更，需关注：
- GitNexus 升级时 LadybugDB 是否兼容旧索引
- 是否需要在 KuzuDB 正式版发布后迁移

---

## 6. 融合方案：节点化 + 知识图谱 + MCP

### 6.1 三者定位

```
节点化（约束"做什么"） → 限定每步的 API 选择范围
  + 代码结构图谱（丰富"怎么做"） → 提供调用链和类继承上下文
  + API 语义层（补充"注意什么"） → 提供参数约束和常见错误
  + MCP（连接"执行什么"）       → 通过 pyaedt 实例连接 AEDT
```

### 6.2 融合方式 🔧

1. **节点的 `api_scope`**：从 GitNexus 图谱自动查询生成，而非手工枚举
2. **节点的 `graph_slice`**：从 GitNexus 只拉取相关子图（~200 token），作为节点上下文补充
3. **节点的 API 语义**：从独立语义存储查询参数约束和常见错误，注入节点 system_prompt
4. **节点的 `examples`**：从案例库自动匹配，而非手工编写
5. **调度层的节点发现**：调度层通过 GitNexus 的 `query` 工具找到相关节点
6. **节点进化时的图谱更新**：pyaedt 发版后重索引，自动检测 `api_scope` 是否需要更新

### 6.3 修正后的融合架构全景 💡

```
用户: "设计一个 2.4GHz 贴片天线，基板用 Rogers 4350"
                    │
                    ▼
        ┌──── 调度层（大模型）────────────┐
        │ 理解意图 → 规划 DAG             │
        │ 从节点目录选择节点               │
        │ 解析参数 → 分配给节点            │
        │ 通过 GitNexus 发现/匹配节点      │
        └────────┬───────────────────────┘
                 │
    ┌────────────┼────────────────┐
    ▼            ▼                ▼
[建基板]      [建贴片]         [建馈电]   ...
 小模型        小模型           中模型
 API白名单3   API白名单4      API白名单5
 +GitNexus    +GitNexus       +GitNexus
   图谱切片     图谱切片        图谱切片
 +语义层      +语义层          +语义层
   约束/错误   约束/错误        约束/错误
 +1个示例     +2个示例        +3个示例
    │            │                │
    └────────────┼────────────────┘
                 ▼
        ┌──── 执行层（AEDT MCP）───────┐
        │ 持久化 pyaedt 实例            │
        │ execute_script(code)         │
        │ get_model_info()             │
        │ submit_solve()               │
        └─────────────────────────────┘
                 │
                 ▼
        ┌──── 反馈层 ──────────────────┐
        │ 成功 → 下一个节点             │
        │ 失败 → 调度层重规划           │
        │ 长期 → 节点进化               │
        └─────────────────────────────┘
```

### 6.4 数据流详解 🔧

```
节点执行时的完整数据流：

1. 调度层下发任务 → 节点收到结构化输入参数
2. 节点加载自己的上下文：
   ├── system_prompt（节点定义中的固定部分）
   ├── API 白名单（3~5 个方法）
   ├── GitNexus 图谱切片（通过 context 工具拉取）
   ├── API 语义信息（通过 search_api 工具拉取）
   └── Few-shot 示例（从案例库匹配）
3. 小模型生成 pyaedt 代码
4. 代码通过 MCP 的 execute_script 传给 AEDT MCP Server
5. AEDT MCP Server 在持久化 pyaedt 实例上执行代码
6. 执行结果返回节点：
   ├── 成功 → 输出类型化结果 → 下游节点接收
   └── 失败 → traceback 返回调度层 → 重规划或重试
```

---

## 7. 需要深入讨论的问题

### 7.1 节点设计

- [ ] **节点目录的初始规模**：从哪 5~10 个核心节点开始？按 AEDT 设计器类型分还是按仿真流程分？
- [ ] **节点间的状态传递机制**：`Hfss` app 对象透传 vs 全局注册表 vs 序列化 ID？
  - ⚠️ pyaedt 实例是 .NET interop 对象，不能序列化，必须进程内透传
- [ ] **动态节点 vs 静态节点**：是否允许调度层在运行时"合成"新节点（用大模型临时生成），还是所有节点必须预定义？
- [ ] **节点的版本管理与兼容性**：节点升级后，依赖它的旧工作流如何处理？

### 7.2 调度层

- [ ] **调度协议**：调度层和节点层之间的通信格式？JSON-RPC？MCP 内嵌？自定义协议？
- [ ] **工作流持久化**：用户构建的工作流如何保存、分享、版本管理？
- [ ] **错误恢复策略**：中间节点失败后，是重试当前节点、回滚上游、还是换一条路径？
- [ ] **并行执行**：DAG 中无依赖关系的节点能否并行？如何处理 AEDT 的单线程限制？
  - ⚠️ pyaedt 通过 .NET interop 操作同一个 AEDT 进程，本质上是单线程的。并行节点需要排队到 AEDT 实例上串行执行

### 7.3 知识图谱与语义层

- [ ] **图谱粒度与检索质量的平衡**：L1-L4 分层是否合理？实际 token 消耗如何估算？
- [ ] **API 语义层的存储选型**：JSON / SQLite+FTS5 / 向量库？取决于 API 量和检索需求
- [ ] **API 语义层的半自动标注流程**：哪些必须人工标注？哪些可以从 docstring 自动提取？
- [ ] **图谱与 pyaedt 版本同步**：自动化到什么程度？CI 流水线还是手动？
- [ ] **GitNexus 商业授权**：是否需要？如果需要，授权成本和替代方案（Graphify）？

### 7.4 工程实现

- [ ] **技术栈选型**：
  - 调度层：纯 Python？TypeScript？混合？
  - 节点运行时：进程隔离？线程？Docker？
  - 前端可视化（ComfyUI 风格的节点编辑器）：是否需要？用什么框架？
- [ ] **AEDT 连接方式**：
  - ⚠️ pyaedt 使用 .NET interop（pythonnet），需要 Windows + AEDT Desktop 环境
  - GRPC 模式支持远程连接，但功能可能不如本地 COM 模式完整
  - 多节点并行受限于 AEDT 单进程，需设计执行队列
- [ ] **评估体系**：如何量化节点质量（命中率、运行时错误率、token 消耗、延迟）？
- [ ] **安全性**：`execute_script` 的沙箱隔离？误操作保护？

### 7.5 产品形态

- [ ] **目标用户画像**：懂 AEDT 的工程师提效？还是不懂 AEDT 的新手上手？
- [ ] **交互方式**：纯自然语言对话？可视化拖拽节点？混合？
- [ ] **与现有工具的关系**：是 Cursor/Claude Code 的 MCP 插件？还是独立应用？

---

## 8. 参考资源

### 项目

| 项目 | 链接 | 与本项目的关联 |
|---|---|---|
| text-to-cae | https://github.com/Cai-aa/text-to-cae | Abaqus 版本的自然语言仿真，**架构模式**参考（非代码复用） |
| abaqus-mcp | https://github.com/Cai-aa/abaqus-mcp | 薄接口 MCP 的反面教材，验证了 API 命中率问题 |
| ansys.aedt.core (pyaedt) | https://github.com/ansys/pyaedt | API 源码，图谱生成的数据源（需 git clone 到独立目录） |
| GitNexus | https://github.com/abhigyanpatwari/GitNexus | 推荐的图谱生成和查询工具（⚠️ PolyForm Noncommercial 许可） |
| Graphify | https://github.com/safishamsi/graphify | 备选图谱工具，MIT 许可，Python 原生 |
| ComfyUI | https://github.com/comfyanonymous/ComfyUI | 节点化工作流的 UI 和交互范式参考 |

### 关键概念

- **MCP (Model Context Protocol)**：AI 客户端与外部工具的通信协议，本项目的技术基座
- **pyaedt .NET interop**：ansys.aedt.core 通过 pythonnet 调用 AEDT 的 COM/API 接口，进程内通信，需要持久化实例
- **LadybugDB**：GitNexus 使用的嵌入式图数据库（KuzuDB rebrand 过渡期，当前 v0.16.x pre-1.0）
- **Cypher**：Neo4j 标准图查询语言，GitNexus 支持直接查询
- **Leiden 社区检测**：图聚类算法，用于自动发现代码的功能模块划分
- **PolyForm Noncommercial-1.0.0**：GitNexus 使用的许可证，商业使用需单独授权

### ⚠️ 关键修正记录

| # | 问题 | 修正 |
|---|---|---|
| 1 | text-to-cae 是 Abaqus，与 AEDT 无关 | 只借鉴架构模式（MCP + CAE 插件），不复用代码 |
| 2 | GitNexus 需要 git 仓库 | 先 `git clone` pyaedt 到独立目录，再 `gitnexus analyze` |
| 3 | API 语义与 GitNexus 图 schema 不匹配 | 双存储：GitNexus 存代码结构，语义信息独立存储 |
| 4 | AEDT 用 .NET interop，非文件 IPC | AEDT MCP Server 维护持久化 pyaedt 实例 |
| 5 | GitNexus 实际 13 个 MCP 工具 | 3 个 group 工具未引入，不影响核心功能 |
| 6 | GitNexus 商业授权 | PolyForm Noncommercial 许可，商业使用需联系 AkonLabs |

---

## 9. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v0.1 | 2026-05-07 | 初始讨论稿，汇总背景分析、两套方案及融合架构 |
| v0.2 | 2026-05-07 | 纳入 6 项修正：Abaqus/AEDT 区分、双存储架构、.NET interop、GitNexus 工具数、商业授权、LadybugDB 状态 |