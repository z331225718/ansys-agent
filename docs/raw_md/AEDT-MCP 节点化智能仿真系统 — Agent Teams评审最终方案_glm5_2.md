# AEDT-MCP 节点化智能仿真系统 — 最终设计方案

> 本文档基于多方辩论（知识图谱方案、节点化方案、融合架构、工程挑战）的综合评判，
> 输出最终推荐的设计方案和实施路线。
> 辩论日期：2026-05-08

---

## 1. 辩论观点综述

### 1.1 知识图谱方案论证

**评分：8/10**

#### 核心价值

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构合理性 | 9/10 | 双存储分离清晰，图谱分层科学 |
| 技术选型 | 8/10 | GitNexus选型正确，Process追踪是核心资产 |
| 落地可行性 | 7/10 | 具体实施路径明确，但需验证MVP实验 |
| 维护成本 | 8/10 | 自动化程度高（`gitnexus analyze`一条命令） |
| 长期价值 | 9/10 | Process追踪和Cypher查询非SQLite可替代 |

#### 关键论证

1. **Process执行流追踪的核心价值**
   - 能追踪 `Hfss.__init__` → `Modeler3D.create_rectangle` → `Primitives.assign_material` 调用链
   - 这是AI写代码时最需要的信息："这个方法依赖哪些前置操作？"
   - Graphify无法提供执行流追踪，只能做概念聚类

2. **双存储架构必要**
   - GitNexus图schema是代码结构性节点（Function、Class、Method、31种）
   - API语义信息（参数约束、常见错误）不属于该schema
   - 强行注入会导致GitNexus工具（`context`、`impact`）失效
   - 正确方案：代码结构（GitNexus）与API语义（SQLite）分离，通过fqname关联

3. **Cypher查询的必要性**
   ```cypher
   MATCH (cls:Class {name: 'Hfss'})-[:HAS_METHOD]->(m:Method) 
   WHERE m.name STARTS WITH 'create_' 
   RETURN m.name, m.signature
   ```
   - 支持复杂关系查询（影响分析、API依赖）
   - SQL查询图关系需要复杂JOIN和递归，效率极低

#### 风险与扣分原因

- **-1分**：GitNexus商业授权风险（PolyForm Noncommercial许可证模糊）
- **-1分**：LadybugDB处于过渡期（v0.16.x），API稳定性需关注

---

### 1.2 节点化方案论证

**评分：8/10**

#### 核心价值

节点化方案的核心洞察：

```
原始问题：AI 面对整个 ansys.aedt.core (5000+ 方法) → 命中率低
图谱方案：AI 先查图谱缩小范围到 50 个方法           → 命中率中
节点方案：AI 只需要从 3 个方法里选                   → 命中率高
```

#### 关键论证

1. **解决语义级错误的杀手锏**
   - 将任务收敛到"工程师会心算完成"的颗粒度
   - 类型系统（FaceId/PortId）建立可验证防线
   - 比薄MCP的"跑完几小时仿真后才发现结果不对"高效得多
   - 如果上游输出不是合法的FaceId，下游馈电节点根本无法触发

2. **模型分层策略的成本效益**
   
   | 任务类型 | 模型选择 | 原因 |
   |---------|---------|------|
   | 节点内代码生成（API白名单≤5） | 7B-14B小模型 | 选择空间极小，足够 |
   | 需物理判断的节点 | 中等模型（~70B） | 需领域推理能力 |
   | 调度编排 | 大模型（Opus/4级） | 需理解意图、规划工作流 |
   
   成本估算：
   - 大模型调度：500 tokens × $15/1M = $0.0075
   - 小模型执行：180 tokens × $0.14/1M = $0.00003
   - 完整工作流（5节点）：调度500 + 5×180 = 1400 tokens
   - **成本降低约60%**

3. **节点进化机制的可行性**
   
   触发条件设计完善：
   - 错误驱动：traceback → 优化system_prompt或api_scope
   - 评估驱动：benchmark命中率低于阈值 → 触发调优
   - 成本驱动：发现更小模型也能达标 → 降级模型
   - 需求驱动：用户请求新功能 → 扩展节点能力
   
   进化示例（v7版本）：
   ```
   api_hit_rate: 0.95（从78%提升）
   runtime_error_rate: 0.03
   avg_tokens: 180
   avg_latency_ms: 450
   ```

#### 风险与扣分原因

- **-0.5分**：类型系统的实际实现细节未明确（FaceId等.NET对象验证机制）
- **-0.5分**：pyaedt版本同步和节点版本管理自动化困难
- **-0.5分**：.NET对象不能序列化，状态传递限制了节点隔离
- **-0.5分**：离线部署价值被过度强调（实际基础设施需求复杂）

---

### 1.3 融合架构论证

**评分：7.5/10**

#### 核心价值：组合效应

融合方案不是三层简单叠加，而是**三层递进**：

```
节点化（约束做什么） → 限定每步的 API 选择范围（命中率30%→90%）
  + 代码结构图谱（丰富怎么做） → 提供调用链和类继承上下文（90%→95%，边际5%）
  + API 语义层（补充注意什么） → 提供参数约束和常见错误（silent failure 10%→2%）
  + MCP（连接执行什么） → 通过 pyaedt 实例连接 AEDT
```

#### 各层职责边界

| 层级 | 职责 | 不做什么 | 依赖关系 |
|------|------|----------|----------|
| **节点化** | 约束选择范围（限定API白名单） | 不负责API语义解释 | 需图谱填充api_scope |
| **图谱层** | 提供调用链和继承关系 | 不做执行决策 | 独立数据源 |
| **语义层** | 提供参数约束和常见错误 | 不追踪代码结构 | 与图谱通过fqname关联 |
| **MCP** | 执行代码、管理连接 | 不做代码生成 | 接收节点输出 |

#### 协同机制

1. **节点的api_scope从图谱查询生成**，而非手工枚举 → 避免"漏掉关键API"
2. **节点的system_prompt注入语义层约束** → 防范"参数设错但代码能跑"
3. **调度层通过图谱发现节点** → 解决"用户需求如何映射到节点目录"
4. **节点进化时图谱更新** → 解决"pyaedt升级后节点是否失效"

#### 复杂度评估

| 维度 | 复杂度评分 | 原因 |
|------|------------|------|
| 架构理解 | 中 | 分层清晰，但需理解4层联动机制 |
| 实现难度 | **高** | 需同时实现：节点运行时+图谱索引+MCP+语义存储 |
| 调试难度 | **高** | 跨层问题定位困难 |
| 维护成本 | 中高 | pyaedt版本同步+节点进化管理 |
| 用户理解 | 中 | 节点概念易理解，图谱联动较难 |

#### 风险与扣分原因

- **-1分**：复杂度高，需按阶段推进避免过度工程
- **-1.5分**：Stage A就引入完整融合架构会过早，应先验证节点候选

---

### 1.4 工程实现挑战

**整体实现难度评分：7/10**

#### 高风险项汇总

| 风险类别 | 风险级别 | 关键挑战 | 缓解方案 |
|---------|---------|---------|---------|
| **Baseline对比公平性** | 高 | 需三组对照设计 | Group A/B/C对比 |
| **商业授权** | 高 | GitNexus许可证模糊 | 初期用Graphify/SQLite，Stage C确认授权 |
| **pyaedt实例稳定性** | 高 | 长连接COM调用不可靠 | 健康检查+重建机制 |
| **多节点队列机制** | 高 | AEDT单线程约束 | 任务队列+状态锁 |
| **离线环境部署** | 高 | Node.js/GPU限制 | 纯Python方案，Graphify替代GitNexus |
| Stage A实验有效性 | 中 | 离线评估无法验证运行时行为 | 增加模拟执行层 |
| Benchmark覆盖度 | 中 | 教科书案例不够挑战 | 增加挑战级任务+陷阱库 |

#### 关键质疑：核心假设需验证

**最关键的质疑**：检索增强能否显著提升API命中率？

如果Stage A实验证明：
- Group C（检索增强）比 Group B（prompt注入）只提升 **<10%** → 检索系统ROI被质疑
- Group C比 Group B提升 **≥15%** → 验证成功，继续推进

**这个实验会直接决定整个架构的价值基础。**

---

## 2. 关键争议裁决

### 2.1 争议一：图数据库是否是第一优先级？

| 观点方 | 观点 |
|-------|------|
| LLM_A | 图数据库不是第一优先级，先用SQLite+FTS5 |
| 知识图谱方 | Process追踪是AI写代码的核心需求，SQLite无法替代 |

**裁决**：
- ✅ **同意LLM_A的优先级判断**：Stage A应先验证节点化和语义层核心价值
- ❌ **不同意"SQLite可替代图数据库"**：Process追踪是长期资产，只是时机问题
- 🔧 **裁决方案**：
  ```
  Stage A：手工api_scope（不依赖图数据库）+ SQLite语义层
  Stage B：引入MCP，保持手工api_scope
  Stage C：引入GitNexus，api_scope自动生成
  ```

---

### 2.2 争议二：Baseline vs Grounded实验设计是否公平？

| 观点方 | 观点 |
|-------|------|
| LLM_A | Baseline（裸模型）vs Grounded（检索增强）两组对照 |
| 质疑方 | 需三组对照才能量化检索系统真实增量价值 |

**裁决**：
- ✅ **同意质疑方，扩展为三组对照**：
  ```
  Group A（裸模型）：完全无辅助
  Group B（prompt注入基础docstring）：量化prompt注入价值
  Group C（节点约束+语义提示）：量化检索系统增量价值
  ```
- 🔑 **关键**：如果Group C比Group B只提升5-10%，检索系统ROI会被质疑

---

### 2.3 争议三：离线部署的真实价值？

| 观点方 | 观点 |
|-------|------|
| 节点化方 | 模型分层契合离线部署需求，是核心卖点 |
| 质疑方 | 离线部署需要AEDT+本地大模型+GPU，基础设施复杂 |

**裁决**：
- ✅ **质疑方正确**：
  - pyaedt需要Windows+AEDT Desktop（付费商业软件）
  - 调度层仍需大模型（如果无法云端API，需本地部署70B模型）
  - 小模型（7B）也需要GPU（否则CPU推理延迟秒级）
- 🔧 **降低离线部署强调程度**：离线部署价值存在但有限制，不应作为核心卖点

---

### 2.4 争议四：节点化能否解决语义级错误？

| 观点方 | 观点 |
|-------|------|
| 节点化方 | 类型约束是可验证防线，比薄MCP先进 |
| 质疑方 | 类型系统实现细节未明确，FaceId等.NET对象验证复杂 |

**裁决**：
- ✅ **节点化方核心观点正确**：类型约束确实能提前拦截错误
- 🔧 **需补充类型验证机制**：
  ```python
  # FaceId/PortId本质是pyaedt返回的字符串名称
  # 验证需要调用app.modeler[name]检查对象存在性
  def validate_type(input_id: str, app: Hfss) -> bool:
      try:
          obj = app.modeler[input_id]
          return obj is not None
      except KeyError:
          return False
  
  # 节点执行前增加validate_type(input)步骤
  ```

---

### 2.5 争议五：融合方案复杂度是否过高？

| 观点方 | 观点 |
|-------|------|
| 融合方 | 复杂度可控，价值在组合效应 |
| 质疑方 | Stage A就引入完整融合架构是过度工程 |

**裁决**：
- ✅ **质疑方正确**：融合方案复杂度高，必须分阶段实现
- 🔧 **分阶段策略**：
  - Stage A：只做节点候选评估（手工api_scope）
  - Stage B：节点执行框架+MCP（仍手工api_scope）
  - Stage C：GitNexus引入，api_scope自动生成

---

## 3. 最终设计方案

### 3.1 MVP阶段策略

**收缩目标**（同意LLM_A建议）：

| 支持范围 | 不碰范围 |
|---------|---------|
| ✅ 只做HFSS | ❌ Icepak、Maxwell、Mechanical |
| ✅ 建模、激励、setup、sweep | ❌ Optimetrics、Layout、EDB |
| ✅ S参数导出 | ❌ 后处理复杂场景 |

**核心假设验证优先级**：
1. 🥇 **节点化能否提升API命中率**（最高优先级）
2. 🥈 **语义层能否减少silent failure**（次优先级）
3. 🥉 **图数据库能否提供增量价值**（最后验证）

---

### 3.2 Stage A（1-2周）：节点候选评估 + 语义层验证

#### 目标

验证节点化框架的核心价值，不引入图数据库

#### 任务清单

```
├── 1. git clone pyaedt源码
│     └── git clone https://github.com/ansys/pyaedt.git D:\code\pyaedt-src
│
├── 2. 定义5个核心节点候选（不依赖图数据库）
│     ├── create_substrate（建基板）
│     │     api_scope: [Hfss.modeler.create_box, Hfss.assign_material]
│     ├── create_patch（建贴片）
│     │     api_scope: [Hfss.modeler.create_rectangle, Hfss.assign_material]
│     ├── create_wave_port（建端口）
│     │     api_scope: [Hfss.create_wave_port, Hfss.modeler.select_face]
│     ├── create_airbox（建空气盒）
│     │     api_scope: [Hfss.modeler.create_box, Hfss.assign_radiation_boundary]
│     └── setup_solve（求解）
│           api_scope: [Hfss.create_setup, Hfss.analyze]
│
├── 3. 手工枚举每个节点的api_scope（Top 50高频API）
│
├── 4. build_api_semantics.py（自动抽取+人工标注）
│     ├── 自动抽取：signature、type hints、default values、docstring、Raises、Notes
│     ├── 人工标注：hidden constraints、common failure、semantic traps
│     └── 输出格式：
│           {
│             "fqname": "Hfss.create_setup",
│             "signature": "...",
│             "params": [],
│             "constraints": ["必须在至少一个物体存在后调用"],
│             "common_errors": ["未创建物体 → RuntimeError"],
│             "examples": []
│           }
│
├── 5. 收集examples（HFSS filters/antennas/waveguide）
│     └── 结构化摘要而非原始.py文件
│
├── 6. 设计三组对照实验（扩展LLM_A方案）
│     ├── Group A（裸模型）：用户需求 → LLM直接生成pyaedt
│     ├── Group B（prompt注入）：用户需求 → 注入docstring → LLM生成
│     └── Group C（节点约束）：用户需求 → api_scope + semantic_info → LLM生成
│
├── 7. Benchmark数据集
│     ├── tasks/
│     │     ├── hfss_patch_antenna.md
│     │     │     ├── 自然语言需求
│     │     │     ├── reference_script
│     │     │     ├── expected_outputs
│     │     │     └── validation_script（自动化判卷）
│     │     ├── waveguide_filter.md
│     │     ├── microstrip_line.md
│     │     └── challenge_task.md（挑战级任务）
│     └── common_traps库
│           ├── "警告：WavePort必须与背景完全接触，否则边界条件错误"
│           ├── "警告：airbox尺寸依赖波长，太小会导致辐射边界失效"
│           └── Top 50 API的陷阱
│
└── 8. 人工评估
      ├── Syntax pass：pyflakes/pylint自动化
      ├── Runtime pass：推迟到Stage B（需真实AEDT）
      └── Semantic pass：Validation Script自动化 + 人工复核
```

#### 关键修正

| 原LLM_A建议 | 修正内容 |
|------------|---------|
| 两组对照 | ✅ 三组对照（量化检索系统增量价值） |
| 人工评估为主 | ✅ Validation Script自动化判卷 |
| 无陷阱库 | ✅ 增加Common Traps库 |

---

### 3.3 Stage B（2-4周）：节点执行框架 + MCP

#### 目标

引入真实AEDT执行，验证节点化在运行时的价值

#### 任务清单

```
├── 1. AEDT MCP Server（持久化pyaedt实例）
│     ├── 健康检查机制
│     │     ├── 定期调用app.project_name验证连接
│     │     ├── 检测异常后自动重建实例
│     ├── 异常捕获层
│     │     ├── CLR异常（.NET层面）
│     │     ├── AEDT内部异常（软件层面）
│     │     ├── pythonnet包装异常（interop层面）
│     │     └── 记录完整异常栈，翻译错误码
│     ├── 任务队列机制
│     │     ├── AEDT单线程约束 → 全局串行化
│     │     ├── 节点提交任务到队列，不直接调用pyaedt
│     │     └── 队列管理器保证串行执行
│
├── 2. 节点运行时
│     ├── 输入/输出管理
│     │     ├── inputs: {hfss_app: Hfss实例, params: {...}}
│     │     ├── outputs: {object_id: ObjectId, hfss_app: Hfss}
│     ├── 类型验证
│     │     ├── validate_type(input_id, app) → 检查对象存在性
│     │     ├── 类型失效检测（用户在GUI删除物体）
│     ├── 上下文组装
│     │     ├── system_prompt（节点定义）
│     │     ├── api_scope（手工枚举，3-5个API）
│     │     ├── semantic_info（SQLite查询）
│     │     └── examples（案例库匹配）
│
├── 3. 小模型执行
│     ├── 模型：Qwen2.5-Coder-7B
│     ├── API白名单：≤5个方法
│     ├── 低温度：0.1（代码生成确定性）
│     ├── 输入：上下文组装 + 用户需求
│     └── 输出：pyaedt代码片段
│
├── 4. 单节点反馈闭环
│     ├── Level 1：同节点重试（小模型修复代码）
│     ├── Level 2：节点替换（调度层换等价节点）
│     ├── Level 3：DAG重规划（整体策略调整）
│     └── 记录错误类型分类器：
│           ├── syntax_error
│           ├── api_not_found
│           ├── parameter_wrong
│           ├── runtime_crash
│           └── semantic_failure（用户标注后记录）
│
└── 5. 验证runtime pass
      ├── 真实AEDT执行
      ├── 目标：≥85%通过率
      └── 节点执行延迟：≤500ms（不含AEDT求解）
```

#### 关键补充

| 原设计方案 | 补充内容 |
|-----------|---------|
| 无健康检查 | ✅ pyaedt实例健康检查+重建机制 |
| 无队列机制 | ✅ 任务队列机制（AEDT单线程） |
| 无类型验证 | ✅ 类型验证步骤 |

---

### 3.4 Stage C（后续）：图谱联动 + 节点进化

#### 目标

图数据库引入，节点进化机制启动

#### 任务清单

```
├── 1. GitNexus索引pyaedt源码
│     ├── git clone https://github.com/ansys/pyaedt.git
│     ├── gitnexus analyze --embeddings --skills
│     ├── ⚠️ 确认商业授权边界（联系AkonLabs）
│           ├── 如果企业内部使用需授权 → 评估成本
│           ├── 如果不可接受 → 用Graphify（MIT）降级
│
├── 2. api_scope自动生成
│     ├── 节点种子API → GitNexus context查询
│     │     context("Hfss.modeler.create_box", depth=2)
│     │     → 输出: create_box + assign_material + set_working_coordinate_system
│     ├── 图谱切片注入节点api_scope（3-5个API）
│     ├── 缓存机制：相同API不重复查询
│
├── 3. 调度层DAG编排
│     ├── 意图解析（大模型：Claude Opus）
│     │     ├── 用户需求 → 提取关键参数
│     │     ├── "设计2.4GHz贴片天线" → {freq, antenna_type}
│     ├── 节点发现（GitNexus query）
│     │     ├── query("patch antenna") → 匹配节点目录
│     ├── 工作流拓扑生成
│     │     ├── substrate → patch → feed → boundary → solve
│     ├── 并行节点处理（AEDT单线程）
│     │     ├── 无依赖节点逻辑并行（提交多任务）
│     │     ├── 队列串行执行
│
├── 4. 节点进化机制
│     ├── 错误驱动
│     │     ├── traceback → 错误类型分类 → 自动优化system_prompt
│     │     ├── 记录高频错误模式 → 补充semantic_info
│     ├── 评估驱动
│     │     ├── benchmark命中率低于阈值 → 触发调优
│     │     ├── 调整api_scope或system_prompt
│     ├── 成本驱动
│     │     ├── 发现更小模型也能达标 → 降级模型
│     │     ├── 记录avg_tokens优化
│     ├── 需求驱动
│     │     ├── 用户请求新功能 → 扩展节点能力
│     │     ├── 版本管理（旧工作流兼容）
│
├── 5. pyaedt版本同步
│     ├── pyaedt发版后重索引GitNexus
│     ├── 自动检测api_scope是否需要更新
│     │     ├── 新增API → 推荐新节点候选
│     │     ├── 废弃API → 标记受影响节点
│     ├── 受影响节点触发更新评估
│
└── 6. Semantic Validator（长期护城河）
│     ├── Geometry validation
│     │     ├── airbox大小检查（是否≥λ/4）
│     │     ├── port接触检查（是否与背景接触）
│     │     ├── overlap检查（物体重叠警告）
│     ├── Simulation validation
│     │     ├── 自适应收敛检查
│     │     ├── 网格质量评估
│     │     ├── setup合理性验证
│     └── Physics validation
│           ├── S11异常检测（>0dB警告）
│           ├── 辐射效率异常
│           ├── 谐振不存在警告
```

#### 关键决策

| 决策点 | 方案 |
|-------|------|
| GitNexus引入时机 | Stage C（核心假设验证后） |
| 商业授权确认 | Stage C之前联系AkonLabs，如不可接受用Graphify |
| Semantic Validator | 作为长期护城河，Stage C启动 |

---

## 4. 技术栈选型裁决

| 模块 | 最终选型 | 原因 |
|------|----------|------|
| **API语义存储** | SQLite + FTS5 | ✅ LLM_A建议，质疑方认同，离线部署友好 |
| **代码结构图谱** | GitNexus（Stage C） | ✅ Process追踪核心价值，⚠️ 授权需确认 |
| **备选图谱工具** | Graphify（MIT） | ✅ 如果GitNexus授权不可接受，降级方案 |
| **案例库** | SQLite + FTS5 | ✅ 结构化摘要检索，轻量 |
| **小模型** | Qwen2.5-Coder-7B | ✅ 离线可部署，API白名单≤5足够 |
| **调度层大模型** | Claude Opus/Sonnet | ✅ 意图理解、节点发现需要高能力 |
| **节点运行时** | 纯Python | ✅ 避免Node.js依赖，离线部署友好 |
| **向量库（可选）** | ChromaDB | ✅ Python原生，支持离线，预计算embeddings |

---

## 5. 需避免的陷阱

| 陷阱 | 说明 | 正确做法 |
|------|------|---------|
| ❌ Stage A就引入完整融合架构 | 过度工程 | ✅ 按阶段推进（A→B→C） |
| ❌ 图数据库与语义层混淆 | 破坏GitNexus工具能力 | ✅ 双存储架构，不可合并 |
| ❌ Baseline对比不公平 | 两组对照无法量化增量 | ✅ 三组对照设计 |
| ❌ 过度强调离线部署 | 基础设施复杂（AEDT+GPU） | ✅ 降低强调程度 |
| ❌ 忽视类型验证机制 | FaceId等.NET对象验证不明确 | ✅ 增加validate_type步骤 |
| ❌ GitNexus商业授权未确认 | 企业内部使用模糊 | ✅ Stage C之前联系AkonLabs |

---

## 6. 成功指标

| Stage | 核心验证指标 | 目标 | 验证方法 |
|-------|--------------|------|---------|
| **Stage A** | API命中率（Group C vs Group B） | **提升≥15%** | 三组对照实验 |
| **Stage A** | Silent failure减少率 | **减少≥50%** | Validation Script自动化 |
| **Stage A** | Common Traps覆盖率 | Top 50 API | 陷阱库构建 |
| **Stage B** | Runtime pass率 | **≥85%** | 真实AEDT执行 |
| **Stage B** | 节点执行延迟 | **≤500ms** | 性能监控 |
| **Stage B** | 健康检查有效性 | 实例重建成功率≥90% | 异常捕获测试 |
| **Stage C** | 节点进化有效率 | **≥80%**自动优化成功 | 错误驱动进化测试 |
| **Stage C** | pyaedt版本同步延迟 | ≤1周 | 发版后重索引测试 |

---

## 7. 下一步行动建议

### 7.1 立即执行（本周）

| 任务 | 具体内容 | 产出 |
|------|---------|------|
| **Benchmark数据集设计** | hfss_patch_antenna.md + validation_script | 3个任务文件 |
| **节点候选定义** | 5个核心节点 + api_scope（Top 50 API） | node_configs.json |
| **build_api_semantics.py** | 自动抽取signature/docstring，人工标注constraints | api_semantics.json |
| **Common Traps库** | Top 50 API的陷阱收集 | traps.json |

### 7.2 短期执行（2周内）

| 任务 | 具体内容 | 产出 |
|------|---------|------|
| **三组对照实验** | Group A/B/C对比，自动化判卷 | 实验报告 |
| **结果评估** | 分析Group C比Group B的提升幅度 | 决策报告 |
| **Stage A总结** | 如果提升≥15% → 进入Stage B | Stage A报告 |

---

## 8. 最终结论

### 8.1 核心判断

**三个方案的价值排序**：

```
🥇 节点化是核心（不可妥协） → 解决语义级错误，区别于text-to-cae的根本创新
🥈 语义层是必需（性价比最高） → 防范silent failure的唯一手段，实现成本低
🥉 图数据库是优化（可推迟） → Process追踪有价值，但初期可用手工api_scope替代
```

### 8.2 设计方案成熟度

| 维度 | 评分 | 说明 |
|------|------|------|
| **架构合理性** | ✅ 9/10 | 双存储分离清晰，节点化核心价值明确 |
| **落地可行性** | 🔧 7/10 | 需按阶段推进，避免过度工程 |
| **核心假设验证** | ⚠️ 待验证 | Stage A三组对照实验是关键 |
| **工程风险** | 🔧 7/10 | 可控，需补充健康检查+类型验证 |

### 8.3 最大风险与最大机会

**⚠️ 最大风险**：
- Stage A实验证明"检索增强价值不明显"（Group C比Group B提升<10%）
- → 需重新评估整个架构ROI
- → 检索系统投入可能被质疑

**✨ 最大机会**：
- 节点化框架建立可验证防线（类型约束+API白名单）
- → 区别于text-to-cae的"薄MCP+迭代纠错"
- → 真正解决"代码能跑但物理是错的"问题
- → 行业壁垒建立

---

## 9. 附录：辩论参与方

| 辩论方 | 角色 | 核心观点 |
|-------|------|---------|
| 知识图谱方 | 支持图谱方案 | Process追踪核心价值，双存储必要 |
| 节点化方 | 支持节点方案 | 解决语义级错误，类型约束防线 |
| 融合方 | 支持融合方案 | 组合效应，三层递进协同 |
| 质疑方 | 工程挑战质疑 | 核心假设需验证，工程风险高 |

**最终评判者**：Team Lead（综合裁决）

---

## 10. 版本记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-05-08 | 最终设计方案，基于多方辩论综合评判 |