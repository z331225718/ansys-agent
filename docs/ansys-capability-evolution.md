# Ansys 助手能力分层与自进化架构

## 目标

让助手在保留现有工程安全边界的前提下处理三类任务：

1. 已知任务直接调用强类型 Harness capability。
2. 可复用工程方法由 Skill 指导，循环、分支、审批和恢复由严格 Workflow 执行。
3. 未知任务先查询当前安装版本的 PyAEDT/PyEDB 源码图，再通过受控探索通道验证；成功轨迹可以生成新的 Harness 候选实现。

这不是让 Agent 任意执行 Python。Codebase Memory 只提供知识，Runtime Harness 仍是唯一可以驱动
AEDT/PyEDB 的执行边界。

## 能力分层

| 层 | 职责 | 是否执行 AEDT | 主要产物 |
|---|---|---:|---|
| Harness | 强类型原子能力、schema、风险和后置条件 | 是 | MCP tool、backend command、tests |
| Skill | 可复用判断方法和工具组合指导 | 否 | `SKILL.md`、references |
| Workflow | 有状态循环、分支、审批、恢复和退出条件 | 通过 Harness | YAML graph、handoff、evidence |
| API Memory | 查询当前版本源码、签名、调用链、测试和示例 | 否 | 只读知识查询结果 |
| Exploration | 验证 Harness 尚未覆盖的一次性操作 | 受控 | candidate、preview、readback、trace |
| Promotion | 把可重复的成功轨迹转成 Harness/Skill/Workflow 候选 | 否 | 候选 patch、tests、promotion report |

确定性路由优先级：

```text
严格 Workflow
  > 已注册 Harness capability
  > 只读 API Memory + 受控 Exploration
  > unsupported
```

不能因为 Exploration 存在就绕过已有 Harness。相同意图已有 capability 时，路由器必须选择已注册能力。

## 双 MCP 架构

```text
Claude Code
  |-- ansys-assistant       受控 Runtime MCP
  |     |-- live/artifact Harness
  |     |-- preview/apply/readback/rollback
  |     `-- Desktop native approval
  |
  `-- ansys-api-memory      只读 Knowledge MCP
        |-- search_ansys_api
        |-- inspect_ansys_symbol
        |-- trace_ansys_call
        |-- search_ansys_source
        `-- find_ansys_example
              `-- codebase-memory-mcp graph store
```

`ansys-api-memory` 是 codebase-memory-mcp 的受限 facade。运行时不向 Agent 暴露
`index_repository`、`delete_project`、`manage_adr` 或任意写接口。索引的创建和升级只能由本地管理 CLI 完成。

## 版本化源码图

不把 PyAEDT/PyEDB 源码提交到本仓库。默认索引本项目 `.venv` 中实际安装的源码：

```text
.venv/Lib/site-packages/ansys/aedt/core
.venv/Lib/site-packages/pyedb
```

索引和 manifest 放在用户本地缓存：

```text
%LOCALAPPDATA%/AnsysAgent/knowledge/
  manifest.json
  cbm/<codebase-memory-mcp-version>/
```

当前 manifest 记录：

- distribution name/version；
- source root、文件数和源码 SHA-256 inventory digest；
- codebase-memory project id；
- manifest 创建时间、backend 版本和 cache directory。

Python 解释器与 AEDT 会话版本不写入当前知识 manifest；前者由已安装 distribution 和源码 digest
间接绑定，后者在 Runtime preview 的 live target 中记录。索引是否可用由 `status` 根据当前源码和
codebase-memory project 是否存在动态计算，而不是把可漂移的 ready/stale 状态固化进 manifest。

启动 Desktop 会话时比较 manifest 与当前环境。版本或源码 digest 变化时状态变为 `stale`，不得把旧图结果标成
当前版本证据。索引失败不影响已有 Harness，但 Exploration 必须保持关闭并明确报告原因。

## 只读知识契约

每条可用于 Exploration 的源码证据必须包含：

```json
{
  "package": "pyaedt",
  "package_version": "1.3.0",
  "project": "indexed-project-id",
  "symbol": "qualified.symbol",
  "source_path": "absolute-or-package-relative-path",
  "snippet_digest": "sha256",
  "query_id": "query-..."
}
```

`inspect_ansys_symbol` 会直接返回上述 `operation_evidence`。Runtime validation 会在执行前用本地
API Memory 重放 inspect，并逐字段比较证据；正确格式、正确版本但虚构的 query id 或 digest 仍会拒绝。

仅凭模型记忆、搜索摘要或未绑定版本的网页内容不能成为执行证据。当前 facade 限制 query 长度、
结果数量、call trace 深度，并把 inspect snippet 截断到 20,000 字符，避免整库源码进入模型上下文。

## 受控 Exploration

Exploration 使用声明式 `ansys-operation-plan/v1`，不执行 Agent 提供的原始 Python、shell、COM 字符串或 `eval/exec`。

```json
{
  "schema_version": "ansys-operation-plan/v1",
  "intent": "read or change one bounded AEDT property",
  "target": {
    "product": "hfss3dlayout",
    "project_name": "Board",
    "design_name": "Layout1"
  },
  "risk": "read_only | reversible_edit",
  "evidence": [],
  "steps": [],
  "readback": [],
  "rollback": []
}
```

`propose_ansys_operation` 的 MCP 输入是强类型闭合 schema；Agent 也可先调用
`get_ansys_operation_plan_schema` 获取完整字段和示例。允许的步骤是受限对象导航、JSON 参数方法调用、
属性读取和属性赋值。禁止：

- import、文件系统、网络、进程和环境变量访问；
- 私有/dunder 属性；
- 关闭 AEDT/project、保存、删除 project、启动/停止求解；
- 未声明副作用的方法调用；
- 无源码证据的调用路径；
- destructive 或无法回读的修改；
- 修改 Desktop 按钮来源之外的 port/project/design。

写操作必须有非空 readback 和 rollback。Preview 只执行只读 preflight，解析对象和方法是否真实存在，冻结 plan、
目标、前态和 digest，不执行 mutation。Apply 必须经过已有 Desktop 原生审批，失败时执行 rollback 并记录结果。

## Capability Trace

每次 Exploration 都生成本地 append-only trace；只有到达终态后才 seal 为不可变快照：

```text
proposed -> validated -> previewed -> applied -> verified | rolled_back | rollback_failed | failed  (read-only)
proposed -> validated -> previewed -> approved -> applied -> verified | rolled_back | rollback_failed | failed  (edit)
proposed | validated | previewed -> rejected | failed
previewed | approved -> expired
```

Trace 包含：

- 用户意图和结构化 plan；
- 知识 query ids 与源码 digest；
- PyAEDT/PyEDB package 版本，以及 preview 返回的 AEDT 版本；
- 实际连接选择器（PID 或 port）、product/project/design；
- validation findings、preview/session id 和 snapshot digest；
- before、readback、rollback 结果和稳定错误码。

Token、API key 和 approval session key 永远不写入 trace。Trace 使用本地 append-only JSONL 事件日志、
逐事件哈希链、终态 seal、原子 manifest 和 server-held HMAC 认证；默认签名密钥位于工作区之外的
`%LOCALAPPDATA%/AnsysAgent/secrets`。普通摘要负责内容寻址，HMAC 证明终态 trace 来自配置的 Runtime
信任根；篡改或只重算 SHA-256 后，读取和晋升仍会拒绝。公开 promoter CLI 只接受默认 store 的 trace id，
不能把任意目录或用户提供的 JSON 指定成信任根。

## Promotion

`ansys-capability-promoter` Skill 读取 verified trace，判断目标落点：

| 观察结果 | 晋升目标 |
|---|---|
| 稳定、确定性、原子操作 | Harness capability |
| 复合只读步骤或主要价值是判断方法 | Skill |
| 包含循环、分支、恢复、预算或多审批点 | Workflow |

当前 auto classifier 总会为 sealed verified trace 选择 Harness、Skill 或 Workflow 之一。严格的
单个 `ansys-operation-plan/v1` 通常不携带循环/分支信号，因此 Workflow 候选也可以由 reviewer
显式指定 `target_kind=workflow`。当前实现不会聚合多条 trace，也不会因为“仅一次成功”自动停止生成
候选；重复 fixture/版本验证属于后续人工 review gate。

Promotion 只生成候选目录和 unified diff，不热更新当前 MCP，不自动 commit，不自动启用：

```text
.aedt-agent/capability-candidates/<candidate-id>/
  candidate.json
  manifest.json
  promotion-report.md
  candidate.patch
  generated/
    capability.py + test_capability.py
    or SKILL.md + test_skill_contract.py
    or workflow.yaml + test_workflow_contract.py
```

当前生成器只落盘 `state=candidate`，不实现候选审批或发布状态迁移。后续人工治理可以采用：

```text
observed -> candidate -> validated -> approved -> promoted -> deprecated
```

若后续治理流程把候选推进到 `approved`，必须满足：

1. 至少一条 verified trace，生产级能力建议多 fixture/版本重复验证。
2. 移除端口、工程名、对象 id 和本机路径硬编码。
3. 请求/响应 schema、风险、审批、side effects、postconditions 完整。
4. capability miss、歧义目标、stale preview、执行失败、rollback 失败等负面测试存在。
5. mock contract、MCP schema、现有 interactive/live 和 YAML Graph 回归通过。
6. live acceptance 使用一次性工程或工作副本通过。
7. 用户明确批准候选 patch 后才允许应用。

## 安全不变量

1. API Memory 永远不能调用 AEDT。
2. Exploration 永远不能执行原始 Python、shell 或 COM 脚本。
3. 已有 Harness 永远优先于 Exploration。
4. 未配置知识图、源码证据、审批或 readback 时，未知写操作固定拒绝。
5. Preview 不产生 mutation；Apply 不能保存工程，保存仍是独立审批动作。
6. Release 不关闭 AEDT 或工程。
7. Promotion 不能修改正在运行的 server，也不能自行批准候选。
8. 新增能力不得改变现有 YAML Graph、BRD Worker 或 artifact 行为。

## 端到端示例

```text
用户：把当前 3D Layout 某类对象的属性改成参数表达式

1. Router 发现没有已注册 capability。
2. Agent 用 ansys-api-memory 查询对象类、属性 setter、示例和调用链。
3. Agent 提交声明式 operation plan，并附 query_id/source digest。
4. Validator 拒绝未证实、私有、危险或无 rollback 的步骤。
5. Preview 在绑定的 live session 做只读 preflight，冻结前态。
6. Desktop 显示原生审批框。
7. Apply 执行、readback；失败则 rollback。
8. Trace 状态成为 verified。
9. 对 sealed verified trace 显式调用 promoter，生成 typed Harness 候选 patch 和测试；生产晋升前再做多 fixture/版本复验。
10. 用户审查、验收并批准后，下一版本正式注册 capability。
```

## 验收标准

- 当前安装的 PyAEDT/PyEDB 均可建立独立版本化图，并能查询真实源码片段和调用链。
- Desktop Claude 会话同时加载 Runtime MCP 与只读 Knowledge MCP。
- 已知 capability 不会路由到 Exploration。
- 未知只读计划可 preflight、执行并产生 trace。
- 未知写计划没有 evidence/readback/rollback/approval 任一项时均拒绝。
- 一次性审批 token、过期、重放和 target mismatch 均拒绝。
- verified trace 能生成不自动启用的 Harness/Skill/Workflow 候选。
- Claude Code + DeepSeek v4 flash 五项能力演化 v11 基准达到 `100` 编排分、`100%` 状态准确率和
  `100%` tool-call success；这是确定性单轮证据，不替代多次稳定性与真实项目验收。
- 原 interactive/live/Desktop、YAML Graph 和 BRD Worker 兼容回归保持通过。
