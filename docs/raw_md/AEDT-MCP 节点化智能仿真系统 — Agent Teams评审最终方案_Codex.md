# AEDT-MCP 节点化智能仿真系统 — Agent Teams 评审最终方案

> 评审日期：2026-05-08  
> 输入材料：`AEDT-MCP 节点化智能仿真系统 — 设计讨论稿.md`、`LLM_A.txt`、`LLM_B.txt`、`LLM_B_reviewA.txt`  
> 评审方式：三方 agent 辩论，最终由 Codex 裁决

---

## 1. 最终裁决

最终方案采用：

**轻量语义库 + 结构化案例库 + 静态节点 + 受控 AEDT MCP 执行队列**

并把长期目标定为：

**节点化、类型安全、状态可查、语义可验证、可复用的 AEDT 智能仿真系统。**

核心判断如下：

1. **节点化是最终系统主架构**  
   不能把系统做成一个“会写 pyaedt 的聊天机器人”。AEDT 仿真的难点不是单个 API，而是流程、状态、物理语义和验证闭环。节点是系统边界、类型边界、验证边界和版本边界。

2. **MVP 必须采纳 LLM_A 的收缩路线**  
   先做 HFSS-only、Top 50 API、SQLite/FTS5、结构化案例库、Benchmark 和自动判卷。不要一开始上 GitNexus、完整 DAG Runtime、可视化编辑器、多求解器。

3. **风险控制必须成为一等公民**  
   AEDT 长连接、单线程执行、裸代码执行、安全审计、GitNexus 授权、Benchmark 真实性，都不是后续小问题，而是决定系统能否交付的核心约束。

一句话：

> 架构上坚持节点化，实施上坚持 MVP 验证，工程上坚持受控执行和语义判卷。

---

## 2. 三方辩论结论

### 2.1 Architect 观点

Architect 强调：Benchmark 只是证明节点价值的手段，不能把最终系统降级成检索增强代码生成器。

它的核心主张：

- 大模型负责规划 DAG
- 节点负责受控执行
- 类型系统负责连接合法性
- 语义层负责参数约束、单位、陷阱和常见错误
- MCP 长连接负责真实 AEDT 状态
- 节点持续沉淀为可复用工程能力

Architect 的关键提醒：

> API Grounding 只能降低 API 幻觉，不能解决工作流级错误。例如端口选错面、airbox 尺寸不合理、边界顺序错误。

### 2.2 MVP 观点

MVP 派强调：现在不要继续扩张架构，要先证明核心假设。

它的核心主张：

- 只做 HFSS
- 只覆盖 Top 50 高频 API
- 用 SQLite + FTS5，而不是图数据库
- 先建设结构化案例库和自动判卷
- 用 20-30 个 Benchmark 任务量化收益
- 第 7-8 周再从 Benchmark 中沉淀“伪节点”

MVP 派的关键提醒：

> 如果 4-8 周内不能证明 Grounded 方案相对裸 LLM 有 2-3 倍质量提升，后面做节点化、图数据库、Agent Runtime 都是在放大一个未验证假设。

### 2.3 Risk 观点

Risk 派强调：当前方案方向正确，但过于乐观。

它认定的最高风险：

- AEDT 长连接和单线程执行会成为第一故障源
- 代码能跑但物理错误是最致命失败模式
- GitNexus 授权和 LadybugDB 稳定性会卡商业落地
- 离线部署比想象中更难
- 裸 `execute_script` 是高危攻击面
- 语义层可能变成“伪知识库”
- Benchmark 不真实会让所有指标失真
- 节点化如果一次做全，会滑向维护地狱

Risk 派的关键裁决：

> 短期否决裸 `execute_script`、动态节点生成、DAG 并行执行、商业版强依赖 GitNexus。

---

## 3. 系统总体架构

最终系统分三层，但分阶段实现。

```text
用户自然语言
    |
    v
调度层
  - 理解意图
  - 选择工作流 pattern
  - 规划节点序列
  - 分配参数
    |
    v
节点层
  - 静态节点 catalog
  - 类型化输入输出
  - API 白名单
  - 参数 schema
  - 语义约束
  - 执行前检查
  - 执行后 validation
    |
    v
AEDT MCP 执行层
  - 持久化 pyaedt/HFSS 实例
  - 单实例任务队列
  - 全局锁
  - 超时 watchdog
  - checkpoint
  - 审计日志
    |
    v
AEDT Desktop
```

知识层采用可插拔设计：

```text
KnowledgeProvider
  - SQLiteProvider: MVP 默认，SQLite + FTS5
  - JsonProvider: 离线最小方案
  - GitNexusProvider: 研究增强项，授权明确后接入
  - GraphifyProvider: MIT 备选图谱方案
```

---

## 4. 阶段路线

### Stage A：HFSS Grounding Benchmark

周期：1-4 周

目标：验证 Grounding、案例库、陷阱库和自动判卷是否显著提升正确率。

范围：

- 只做 HFSS
- 只覆盖建模、材料、端口、边界、setup、sweep、S 参数导出
- 不碰 Maxwell、Icepak、EDB、Optimetrics、复杂后处理
- 不引入 GitNexus
- 不做可视化节点编辑器

任务：

1. 建立 Top 50 HFSS API 清单
2. 建立 `api_semantics.sqlite`
3. 人工精标 constraints、common errors、semantic traps
4. 建 10-15 个结构化案例
5. 建 20-30 个 Benchmark 任务
6. 每个任务配 reference script 和 validation script
7. 对比裸 LLM、docstring 注入、节点约束 grounding 三组结果

推荐三组对照：

```text
Group A: 裸 LLM
Group B: 基础 docstring / 签名注入
Group C: API 白名单 + 语义约束 + 案例 + 陷阱库
```

成功门槛：

- Group C 相对 Group B 的 semantic pass 至少提升 15%
- Grounded 一次运行成功率 >= 70%
- 两轮内成功率 >= 85%
- 自动判卷通过率 >= 70%
- 已知 silent failure 捕获率 >= 80%

### Stage B：受控 MCP + 静态节点原型

周期：5-8 周

目标：接入真实 AEDT，验证受控执行闭环。

只实现 5 个 MCP 工具：

```text
search_api(query)
list_examples(task_type)
execute_node(node_id, inputs)
get_model_info()
run_validation(task_id)
```

注意：正式接口不提供裸 `execute_script`。如果开发期保留，必须只用于内部调试，并经过 AST 审计和沙箱限制。

首批静态节点控制在 8 个以内：

```text
create_substrate
create_conductor
create_port
create_airbox
assign_boundary
create_setup
create_sweep
export_sparameters
```

每个节点必须包含：

```text
id
version
input_schema
output_schema
api_whitelist
semantic_constraints
common_traps
precheck
postcheck
validation_rules
examples_ref
```

AEDT MCP Server 必须包含：

- 单实例执行队列
- 全局锁
- 超时 watchdog
- session/project/design/transaction 标识
- checkpoint
- AEDT 健康检查
- 崩溃后重启和恢复策略
- 审计日志

### Stage C：图谱增强 + 节点进化

周期：验证 Stage A/B 通过后启动

目标：引入代码结构图谱和节点演化能力。

GitNexus 的位置：

- 只作为可插拔增强项
- 不进入商业版核心依赖，除非授权明确
- 价值主要是 Process 追踪、context 查询、影响分析、Cypher 查询
- 若授权不可接受，用 Graphify 或继续 SQLite/FTS5 路线

节点进化原则：

- 先人工审核，后半自动
- 节点不达标不得进入 catalog
- 节点版本锁定，旧 workflow 不自动升级
- pyaedt/AEDT 升级后必须跑回归 Benchmark

长期引入 Semantic Validator：

- Geometry validation：尺寸、重叠、airbox、端口接触
- Simulation validation：setup、mesh、收敛、sweep
- Physics validation：S 参数异常、谐振异常、效率异常

---

## 5. 核心数据设计

### 5.1 API 语义库

MVP 使用 SQLite + FTS5。

建议字段：

```text
fqname
module
class_name
method_name
signature
params_json
return_type
docstring
constraints_json
common_errors_json
common_traps_json
examples_ref_json
source
confidence
pyaedt_version
aedt_version
last_verified_at
```

原则：

- signature/docstring 可自动抽取
- hidden constraints 和 traps 必须人工精标
- 每条约束必须有来源
- 不允许把未验证推断当作事实

### 5.2 结构化案例库

案例不是原始 `.py` 文件堆积，而是 workflow pattern。

建议字段：

```text
case_id
domain
task_type
natural_language_task
workflow_steps
api_used
parameters
reference_script_path
validation_script_path
expected_state_json
known_traps
```

首批案例：

- patch antenna
- microstrip line
- rectangular waveguide
- waveguide filter
- coax-fed antenna
- cavity resonator
- simple S-parameter export

### 5.3 Benchmark 任务

每个任务必须包含：

```text
natural_language_requirement
reference_script
expected_aedt_state
validation_script
expected_outputs
manual_review_notes
known_failure_modes
```

任务分级：

- Level 1：单操作 API 命中
- Level 2：小工作流
- Level 3：完整仿真闭环
- Trap：反直觉陷阱任务

---

## 6. 安全与执行策略

### 6.1 禁止裸执行

正式系统禁止把任意 LLM 生成 Python 直接交给本机执行。

必须转为：

```text
用户需求
  -> 选择节点
  -> 组装受控参数
  -> 节点生成或模板生成代码
  -> AST 审计
  -> 白名单校验
  -> AEDT 队列执行
  -> validation
```

### 6.2 AST 审计红线

禁止：

- `os`
- `subprocess`
- `socket`
- `shutil`
- 任意文件删除
- 任意网络访问
- 任意进程操作
- 访问非授权 project/design

### 6.3 执行审计

每次执行记录：

```text
user_request
selected_node
retrieved_context
generated_code_or_template
ast_audit_result
aedt_state_before
aedt_state_after
validation_result
traceback
repair_attempts
```

---

## 7. 成功指标

Stage A 指标：

| 指标 | 目标 |
|---|---:|
| API 命中率 | Grounded >= 95% |
| 一次运行成功率 | >= 70% |
| 两轮内成功率 | >= 85% |
| 自动判卷通过率 | >= 70% |
| Silent failure 捕获率 | >= 80% |
| 平均修复次数 | <= 0.7 次/任务 |
| 检索上下文大小 | <= 8k tokens |
| Top 50 API 覆盖率 | >= 85% |

Stage B 指标：

| 指标 | 目标 |
|---|---:|
| AEDT session 健康检查成功率 | >= 95% |
| 崩溃后恢复成功率 | >= 90% |
| 节点 postcheck 覆盖率 | 100% |
| 节点 validation 覆盖率 | 100% |
| 串行队列任务丢失率 | 0 |
| 审计日志完整率 | 100% |

Stage C 指标：

| 指标 | 目标 |
|---|---:|
| GitNexus/图谱增量收益 | semantic pass 提升 >= 10% |
| 节点回归通过率 | >= 90% |
| pyaedt 版本升级回归周期 | <= 1 周 |

---

## 8. 明确否决项

短期明确不做：

1. 不做完整 ComfyUI 可视化编辑器
2. 不做多求解器支持
3. 不做动态节点生成
4. 不做 DAG 并行执行
5. 不把 GitNexus 作为商业版核心依赖
6. 不开放裸 `execute_script` 给正式用户
7. 不承诺自动抽取完整 API 语义
8. 不用 demo 级 Benchmark 冒充工程验证

---

## 9. 下一步行动清单

本周 P0：

1. 定义 Top 50 HFSS API 清单
2. 设计 `api_semantics.sqlite` schema
3. 写 3 个 Benchmark 任务：patch antenna、microstrip line、waveguide
4. 为每个任务写 validation checklist
5. 建立 common traps 初版
6. 确定首批 8 个静态节点草案

两周内 P0：

1. 完成 10-15 个结构化案例
2. 完成 20-30 个 Benchmark 任务
3. 跑 Group A/B/C 对照实验
4. 输出 Grounding 收益报告
5. 决定是否进入 Stage B

四到八周 P0：

1. 实现最小 AEDT MCP Server
2. 实现单实例队列、锁、watchdog
3. 实现 `execute_node`
4. 实现节点 precheck/postcheck
5. 实现真实 AEDT validation
6. 沉淀第一批静态节点 catalog

---

## 10. 最终结论

这个项目的正确方向不是“让 LLM 背熟 pyaedt”，而是把 AEDT 仿真过程拆成可约束、可检查、可复用、可演进的工程节点。

但当前最优动作不是继续扩写宏大架构，而是先把 HFSS 小闭环打穿：

```text
Top 50 API
  + 结构化案例
  + common traps
  + 自动判卷
  + 受控 MCP 执行
  + 静态节点
```

若 Stage A/B 指标通过，再引入 GitNexus、完整 DAG 调度、节点进化和可视化编辑器。若指标不通过，应优先修正数据资产和 validation，而不是继续堆架构。

最终裁决：

> 保留方案 B 的节点化方向，采纳 LLM_A 的 MVP 路线，吸收 Risk 派的安全与稳定性红线。  
> 先做一个小而硬的 HFSS 智能仿真闭环，再长成完整的 AEDT 节点化智能仿真系统。

