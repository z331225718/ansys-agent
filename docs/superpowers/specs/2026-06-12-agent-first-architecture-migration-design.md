# Agent-First Architecture Migration Design

## Status

- Date: 2026-06-12
- Status: Approved for implementation planning
- Scope: Package architecture migration and runtime boundaries
- First production scenario: BRD local-cut via optimization

> 文档语言约定：从本规格的后续实施计划开始，项目新增的设计、计划和交付报告默认使用中文。代码标识符、协议字段和第三方专有名词保留英文。

## 中文决策摘要

本次迁移采用“旧应用归档、领域能力共享”的方案：

- 原有 `demo`、`benchmark`、`chat`、`evolution` 和旧 CLI 迁入 `aedt_agent.v0`。
- `workflow`、`nodes`、`layout`、`validation`、`mcp`、`knowledge`、`reporting` 第一阶段保持原路径，供新旧系统共享。
- 新的 `aedt_agent.agent` 成为默认产品入口，负责 Mission、规划、Worker 调度、评估、审批、恢复和最终交付。
- 第一个正式 Agent 场景是 BRD local-cut 高速过孔优化，偶极子调谐仅保留为快速回归测试。
- Pi 暂不进入核心运行时；待 Mission、Worker、Event、Approval API 在真实 AEDT 场景稳定后，再作为可插拔会话与交互层进行 PoC。
- 主运行链不依赖 VLM。服务器可只部署无视觉能力的强文本模型，通过结构化仿真证据完成规划、诊断和受控决策。

## Goal

Restructure `ansys-agent` so that the default product is a persistent,
goal-driven Agent runtime while preserving the existing Stage A/B/C application
as a runnable `v0` system.

The migration must retain the tested AEDT domain capabilities already present in
the repository. It must not turn a package move into a broad rewrite of
Workflow, Node, PyAEDT, PyEDB, layout, validation, or reporting behavior.

## Product Definition

The new default product is responsible for the complete mission lifecycle:

```text
User request
    -> mission creation
    -> planning
    -> worker dispatch
    -> AEDT execution
    -> evidence collection
    -> deterministic evaluation
    -> retry, approval, replan, rollback, or completion
    -> final engineering delivery
```

An execution is not considered an Agent mission merely because an LLM generated
a Workflow. A mission must retain its goal and state across multiple jobs, use
intermediate evidence to choose the next action, survive a process restart at a
completed job boundary, and terminate with an explicit engineering outcome.

## Migration Strategy

Use the approved **legacy application archive with shared domain capabilities**
strategy.

### Move into `v0`

The existing application-level products move under `aedt_agent.v0`:

- `demo`
- `benchmark`
- `chat`
- `evolution`
- the current benchmark-oriented CLI

These modules represent the previous product experience, planning entry points,
benchmark harnesses, and demo-specific orchestration. They remain runnable and
tested as the `v0` product.

### Keep shared initially

The following packages remain at their existing import paths during the first
migration:

- `workflow`
- `nodes`
- `layout`
- `validation`
- `mcp`
- `knowledge`
- `reporting`

They contain tested domain behavior used by both the legacy application and the
new Agent runtime. Moving them immediately would create high import churn
without improving Agent behavior.

New Agent code may depend on these shared packages through explicit adapters.
The shared packages must not depend on `aedt_agent.agent`.

### Introduce new top-level boundaries

```text
src/aedt_agent/
├── agent/
│   ├── mission/
│   ├── orchestrator/
│   ├── planning/
│   ├── workers/
│   ├── evaluation/
│   ├── policies/
│   └── approvals/
├── domain/
├── infrastructure/
├── v0/
│   ├── benchmark/
│   ├── chat/
│   ├── demo/
│   ├── evolution/
│   └── cli.py
├── workflow/
├── nodes/
├── layout/
├── validation/
├── mcp/
├── knowledge/
└── reporting/
```

`domain/` and `infrastructure/` are intentional target boundaries, not empty
copies of the existing code. Code moves into them only when the Agent vertical
slice establishes a stable interface and a concrete ownership reason.

## Package Responsibilities

### `aedt_agent.agent`

Owns product-level Agent behavior.

It may decide:

- what job should run next;
- whether evidence satisfies the mission;
- whether an error is retryable;
- whether a new plan is needed;
- whether an engineering action requires approval;
- whether to accept or roll back a modification;
- whether the mission is completed, failed, cancelled, or blocked.

It must not contain direct PyAEDT or PyEDB calls.

### `aedt_agent.v0`

Owns the preserved Stage A/B/C application.

Its behavior remains stable during the architecture migration. Existing demo,
benchmark, planner, and evolution tests are moved with their modules or retained
as compatibility tests. New Agent features must not be implemented inside
`v0.demo.service`.

### Shared domain packages

The existing Workflow, Node, layout, validation, knowledge, and reporting
packages remain deterministic capabilities. They execute or evaluate a
well-defined request and return structured results.

They do not own Mission state or decide the next Mission action.

### `aedt_agent.domain`

This is the future home for stable, product-independent electromagnetic domain
contracts and services. Initial additions must be driven by the BRD vertical
slice. It must not become a second copy of the current shared packages.

Candidate future contents include:

- channel objective and metric contracts;
- layout action schemas;
- artifact contracts;
- domain error taxonomy;
- evaluator interfaces.

### `aedt_agent.infrastructure`

Owns technical mechanisms with no engineering decision authority:

- SQLite persistence;
- process execution and cancellation;
- worker leases;
- filesystem artifact storage;
- AEDT process/session adapters;
- event transport.

## Dependency Rules

The intended dependency direction is:

```text
entry points
    -> agent
        -> domain contracts
        -> worker interfaces
            -> shared workflow/layout/mcp capabilities
            -> infrastructure

v0
    -> shared workflow/layout/mcp capabilities
```

The following dependencies are forbidden:

- shared domain packages importing `aedt_agent.agent`;
- infrastructure deciding whether an engineering objective passed;
- workers directly changing Mission state;
- evaluators dispatching workers;
- `v0` becoming a required dependency of the new Agent runtime;
- Pi-specific types appearing in Mission, Job, Worker, or Event contracts.

## Compatibility Plan

### Import compatibility

Moving four existing packages immediately breaks a large number of imports in
tests, scripts, and internal modules. The migration therefore uses compatibility
modules:

```python
# aedt_agent/demo/__init__.py
from aedt_agent.v0.demo import *
```

Submodules that are used directly receive explicit forwarding modules during
the compatibility period. New code must import from `aedt_agent.v0`; old import
paths are deprecated but continue to work.

Compatibility modules contain no product logic.

### CLI compatibility

Two console entry points are required:

```text
aedt-agent       -> new Agent CLI
aedt-agent-v0    -> preserved legacy CLI
```

The new CLI initially exposes runtime-oriented commands:

```text
aedt-agent mission create
aedt-agent mission run
aedt-agent mission status
aedt-agent mission resume
aedt-agent mission approve
aedt-agent mission cancel
```

The legacy CLI retains the current benchmark commands. Existing standalone
scripts remain functional during the migration and may continue importing
compatibility paths.

### Versioning

The package version remains a single project version. `v0` is an application
namespace and compatibility promise, not a separately published Python
distribution.

## Mission Runtime Boundary

The architecture migration prepares, but does not fully implement, these Agent
contracts:

### Mission

Owns:

- user goal;
- measurable acceptance criteria;
- immutable engineering constraints;
- execution and iteration budgets;
- current state;
- current plan version;
- final outcome.

### Job

Represents one leaseable execution boundary with:

- capability;
- structured input;
- timeout;
- retry policy;
- idempotency key;
- input artifact references;
- output artifact references;
- structured error result.

### Worker

Consumes a Job and returns a Job result plus evidence. A Worker does not know
whether the overall Mission is complete.

The first adapters should wrap existing capabilities rather than rewrite them:

- local-cut model build;
- AEDT solve subprocess;
- Touchstone/TDR extraction;
- channel scoring;
- approval wait/resume.

### Evaluator

Reads evidence and acceptance criteria and returns a deterministic assessment:

```text
passed
needs_adjustment
invalid_model
needs_user_input
inconclusive
```

The LLM may explain or select among allowed actions, but it is not the authority
for numeric pass/fail decisions.

## 无视觉模型优先的证据架构

生产环境默认假设部署的是无视觉能力但推理能力较强的文本模型，例如服务器上的 GLM。系统不得因为模型不能读取图片而中断 Mission，也不得把曲线截图识别作为核心评估方法。

### 核心原则

```text
AEDT 原始结果
    -> 确定性解析与特征提取
    -> 结构化 Evidence Package
    -> 规则 Evaluator 给出客观判定
    -> 文本模型解释、诊断并选择受控动作
    -> 可选 VLM / 人工复核作为补充证据
```

图片是审计和辅助诊断产物，不是主链路输入。能够从 AEDT 导出数值、对象属性或采样数据时，不允许退化为“让模型看截图读数”。

### Evidence Package

每次建模、求解和评估 Job 都输出结构化证据包。证据包分为摘要和可追溯 artifact：

```json
{
  "model_facts": {
    "objects": [],
    "materials": [],
    "ports": [],
    "boundaries": [],
    "setups": [],
    "stackup": {},
    "cutout_region": {},
    "geometry_checks": []
  },
  "channel_metrics": {
    "rl_worst_db": -17.4,
    "rl_worst_frequency_ghz": 18.2,
    "rl_pass_bands": [],
    "tdr_peak_deviation_ohm": 8.1,
    "tdr_anomaly_window_ps": {}
  },
  "field_features": {
    "available": false,
    "peak_regions": [],
    "energy_by_region": [],
    "surface_current_hotspots": []
  },
  "comparison": {},
  "artifact_refs": []
}
```

文本模型只接收压缩后的证据摘要、工程约束、历史动作和允许的 Action Schema。完整 Touchstone、TDR CSV、场采样数据和工程文件通过 `artifact_refs` 保留，不直接塞入模型上下文。

### 结构化提取优先级

1. **曲线与频域结果**：解析 Touchstone、CSV 或 AEDT report 数据，计算 worst RL、pass band、谐振点、插损和频点覆盖。
2. **时域结果**：解析 TDR 数值，提取峰值偏差、局部斜率、异常时间窗口和前后变化。
3. **模型事实**：提取对象、材料、层叠、端口、边界、setup、mesh 和 cutout 范围，验证模型是否具备可优化条件。
4. **场分布**：优先导出数值采样网格或区域统计，计算峰值位置、热点区域、区域能量和表面电流集中度。
5. **图片**：仅在缺少可用数值导出、需要观察复杂空间关系或结构化诊断结果为 `inconclusive` 时使用。

### 纯文本 GLM 的职责

无视觉模型可以基于 Evidence Package：

- 判断当前问题属于模型错误、端口错误、求解问题还是设计指标未达标；
- 结合 stackup、TDR 异常窗口、目标层和历史动作解释可能原因；
- 从预注册 Action Schema 中选择下一步动作；
- 比较多个候选动作的风险、成本和约束冲突；
- 生成面向工程师的中文诊断、审批说明和最终报告。

文本模型不能：

- 自己读取图片并声称获得了其中的数值；
- 覆盖确定性 Evaluator 的 pass/fail；
- 在没有结构化证据时猜测场热点位置；
- 生成任意 PyAEDT/PyEDB 修改代码；
- 在证据不足时强行选择优化动作。

### 可选 VLM Sidecar

VLM 被实现为可选的 `VisualEvidenceWorker`，而不是 Planner 或 Evaluator 的强依赖。

触发条件限定为：

- 结构化提取结果为 `inconclusive`；
- AEDT 只提供了图片形式的关键结果；
- 需要判断难以数值化的空间模式、场型或几何关系；
- 工程师主动要求视觉复核。

VLM 输出必须包含：

- 对应的图片 artifact；
- 观察结论；
- 置信度；
- 无法确认的内容；
- 建议的结构化验证步骤。

VLM 结论属于辅助 Evidence，不能单独触发模型修改、判定达标或跳过人工审批。没有 VLM 部署时，Mission 继续使用结构化证据；若该任务确实依赖视觉判断，则进入 `waiting_approval`，由工程师查看已导出的图片。

### 模型能力协商

模型配置显式声明能力，而不是由业务代码假设模型具备 Vision：

```json
{
  "model_id": "glm-server",
  "capabilities": {
    "text": true,
    "vision": false,
    "structured_output": true,
    "tool_calling": true
  }
}
```

Planner 根据能力选择上下文构建器。`vision=false` 时不生成图片消息，也不影响 Mission 的正常规划和执行。后续接入 VLM、Pi 或其他模型提供方时，均通过相同的能力声明与 Evidence API 工作。

### Orchestrator

Advances the Mission state machine. It is the only component that creates the
next Job, requests approval, triggers a replan, accepts a modification, rolls
back, or closes the Mission.

## First Agent Vertical Slice

The first production Agent scenario is BRD local-cut via optimization.

```text
User supplies board, nets, stackup, bbox, and target metrics
    -> validate mission input
    -> build local-cut model
    -> resolve or request port selection
    -> wait for model approval
    -> solve
    -> extract S-parameters and TDR
    -> validate model evidence
    -> score channel
    -> propose one allowed void/anti-pad adjustment
    -> wait for modification approval
    -> checkpoint and apply
    -> solve and compare
    -> accept, rollback, continue, or stop
    -> deliver engineering report
```

The existing dipole tuning demo remains a fast regression fixture. It is not the
product acceptance scenario for the Agent architecture.

## Error and Recovery Policy

Error handling is policy-driven, not delegated wholesale to the LLM.

| Error class | Default action |
| --- | --- |
| Missing or invalid user input | Wait for user input |
| Ambiguous port candidate | Wait for explicit selection |
| Workflow/schema error | Repair or replan within a bounded attempt count |
| License unavailable | Retry with backoff |
| Worker process crash | Retry from the last completed Job checkpoint |
| Solver timeout | Terminate the process and apply the configured retry policy |
| Invalid model evidence | Stop optimization and create a model-repair plan |
| Metric not achieved | Propose a constrained optimization action |
| Metric regression | Roll back to the prior accepted checkpoint |
| Budget exhausted | End with an unsuccessful engineering report |

Recovery occurs at completed Job boundaries. The runtime does not attempt to
serialize live AEDT COM handles.

## Pi Integration Strategy

### Why Pi is not introduced during the migration

Pi is not rejected. It is deferred until the ansys-agent runtime contracts are
proven.

Introducing it now would create four risks:

1. Mission, Job, Worker, Event, approval, and recovery semantics are not stable
   enough to map safely to an external runtime.
2. The main engineering difficulty is long-running, stateful, license-limited
   AEDT execution rather than ordinary LLM tool calling.
3. Pi and the current project use different primary runtimes, introducing a
   TypeScript/Python boundary before its value is measured.
4. Pi project trust and tool registration do not replace process cancellation,
   filesystem policy, AEDT operation policy, or engineering approval.

### Future integration boundary

Pi may later act as an optional Agent frontend and model-session runtime:

```text
Pi frontend/runtime
    -> Mission API
    -> Event stream
    -> Approval API
    -> Worker capability API
        -> Python ansys-agent runtime
            -> AEDT workers and domain evaluators
```

The Python runtime remains independently runnable. Pi does not own Mission
persistence, AEDT lifecycle, numeric evaluation, rollback, or engineering
approval policy.

### Pi evaluation gate

A Pi proof of concept begins only after:

- the BRD Mission can resume after a process restart;
- Worker and Event contracts have passed a real AEDT scenario;
- approval, retry, cancellation, and rollback semantics are stable;
- the native Python CLI can complete the scenario independently;
- Pi can integrate through public APIs without importing Python internals.

Pi is adopted only if the proof of concept demonstrates at least one material
benefit:

- substantially less model/session orchestration code;
- better streaming and approval interaction;
- reliable multi-provider model support;
- a clearer extension ecosystem;
- measurable reduction in maintenance burden.

The comparison must also measure added deployment complexity, cross-runtime
debugging cost, failure recovery behavior, and security boundaries.

## Migration Phases

### Phase 1: Namespace and compatibility migration

- Create `aedt_agent.v0`, `aedt_agent.agent`, `aedt_agent.domain`, and
  `aedt_agent.infrastructure`.
- Move legacy application packages into `v0`.
- Add compatibility forwarding modules.
- Split the CLI entry points.
- Keep all existing tests passing.

Acceptance:

- old scripts and imports continue to work;
- `aedt-agent-v0` preserves current CLI behavior;
- `aedt-agent` resolves to the new CLI;
- no Agent behavior is added to `v0`;
- shared execution packages have no dependency on `agent`.

### Phase 2: Agent runtime foundation

- Define Mission, Job, Event, Checkpoint, Approval, and Worker contracts.
- Implement SQLite persistence.
- Implement state transition validation.
- Implement worker registration, leases, idempotency, cancellation, and
  structured error classification.

Acceptance:

- a Mission survives service restart;
- duplicate Job execution is prevented;
- a crashed Worker lease can be recovered;
- every state change has an auditable Event.

### Phase 3: BRD model-build Mission

- Wrap the current local-cut build pipeline as a Worker.
- Persist bbox, port candidates, action plan, model project, and approval.
- Resume automatically after approval.

Acceptance:

- the Agent reaches an auditable model-review state;
- ambiguous ports produce a user decision request;
- approval resumes the same Mission without rerunning completed Jobs.

### Phase 4: Solve, evaluation, and one controlled modification

- Add solve, extraction, scoring, proposal, modification, comparison, and
  rollback Jobs.
- Allow one pre-registered void/anti-pad adjustment family.

Acceptance:

- before/after artifacts are tied to checkpoints;
- deterministic metrics drive pass/fail;
- rejected actions do not change the model;
- regressions restore the previous accepted checkpoint.

### Phase 5: Limited iterative Agent and Pi proof of concept

- Add bounded multi-iteration policy.
- Stop on success, repeated action, no improvement, unrecoverable error, or
  budget exhaustion.
- Evaluate Pi only after the native Mission passes acceptance.

Acceptance:

- the complete BRD scenario produces a final engineering delivery;
- all iterations are auditable and recoverable;
- Pi evaluation has measured adoption criteria and a documented decision.

## Testing Strategy

### Migration tests

- import compatibility for every moved package;
- old and new console entry points;
- existing Stage A/B/C test suite;
- dependency rule checks preventing shared packages from importing `agent`.

### Runtime tests

- Mission state transition table;
- SQLite transactional updates;
- event ordering;
- idempotent Job creation and execution;
- lease expiration and reclaim;
- worker cancellation;
- restart recovery;
- approval wait, approve, reject, and resume.

### Scenario tests

- fake/replay BRD Mission for fast CI;
- text-only GLM context built entirely from structured evidence;
- Mission completion without any configured VLM;
- optional VLM failure does not fail the main Mission;
- inconclusive visual-only evidence enters engineering approval;
- ambiguous port candidate;
- license retry;
- solver timeout;
- invalid model evidence;
- metric regression and rollback;
- budget exhaustion;
- one controlled successful improvement;
- real AEDT acceptance run on the supported local environment.

## Non-Goals

This migration does not include:

- rewriting WorkflowExecutor;
- moving all shared packages into `domain` immediately;
- converting every function into a Worker;
- distributed Workers;
- Redis, Celery, Kafka, or Kubernetes;
- multi-agent conversation;
- arbitrary Python execution;
- mandatory VLM dependency;
- VLM-based numeric acceptance or automatic model modification;
- automatic bbox invention;
- Pi source-code fork or deep modification;
- multi-tenant authorization.

## Success Criteria

The architecture migration is successful when:

1. The legacy product remains runnable under `aedt_agent.v0`.
2. The default `aedt-agent` entry point represents the new Agent product.
3. Shared AEDT capabilities remain tested and reusable without depending on the
   new Agent runtime.
4. A BRD Mission can progress across multiple Worker Jobs, approvals, evidence
   evaluations, and process restarts.
5. The system can distinguish completion, engineering failure, user-input
   requirements, retryable infrastructure failure, and budget exhaustion.
6. Pi can be evaluated through stable public boundaries instead of dictating
   the Python runtime architecture.
7. The BRD Mission can complete its normal build, solve, evaluation, approval,
   and delivery path with a text-only model and no configured VLM.
