# Controlled BRD Action Schema Design

## 状态

- 日期：2026-06-14
- 状态：Approved by standing execution direction
- 前置能力：Agent Graph Control Plane、BRD Solve Evidence Pipeline

## 目标

为 ansys-agent 增加第一个受控工程动作闭环：

```text
channel evidence
    -> propose adjust_layout_void
    -> deterministic validation
    -> approval bound to action digest
    -> checkpoint
    -> apply through adapter
    -> score before/after
    -> accept, wait for review, or rollback
```

该闭环必须证明 Agent 能根据证据提出下一步、等待工程审批、执行受限修改、根据确定性指标判断结果，并在退化时恢复到修改前状态。

## 非目标

- 不允许 LLM 生成或执行任意 PyAEDT/PyEDB Python。
- 不在第一版自动执行 raw AEDT void 命令。
- 不做多动作族自动搜索。
- 不序列化 AEDT COM/live session。
- 不用图片或 VLM 决定动作是否成功。
- 不因单次指标改善就自动扩大动作范围。

## 设计选择

采用统一 Action Schema 和双 adapter 边界：

1. `recorded` adapter 用 before/after Touchstone/TDR artifacts 模拟一次已执行修改，完成审批、比较和回滚语义。
2. `real_aedt` adapter 只定义接口和 fail-closed 行为，本阶段不执行 raw AEDT 命令。
3. Graph、Mission、Approval 和 Evaluator 只认识 Action Schema，不依赖 adapter 内部实现。

这样先验证 Agent 控制闭环，再接入真实 AEDT 修改，不用真实工具的不稳定性掩盖协议错误。

## Action Schema

首个动作类型固定为：

```text
adjust_layout_void
```

动作记录包含：

```json
{
  "action_id": "uuid",
  "mission_id": "uuid",
  "action_type": "adjust_layout_void",
  "version": 1,
  "status": "proposed",
  "target": {
    "layer": "ART03",
    "region_ref": "via-transition-1",
    "shape": "circle"
  },
  "parameters": {
    "variable": "r_cut_ART03",
    "old_value_mil": 13.95,
    "new_value_mil": 15.0,
    "delta_mil": 1.05
  },
  "constraints": {
    "min_value_mil": 10.0,
    "max_value_mil": 20.0,
    "max_abs_delta_mil": 2.0
  },
  "reason": {
    "evidence_package_id": "evidence-1",
    "failure_window_ghz": {"start": 17.8, "stop": 18.2},
    "summary": "18GHz 附近 RL 失败，建议小幅扩大 ART03 void。"
  },
  "adapter_mode": "recorded",
  "adapter_input": {
    "before_touchstone": "...",
    "before_tdr": "...",
    "after_touchstone": "...",
    "after_tdr": "..."
  },
  "digest": "sha256"
}
```

`digest` 由排除运行时字段后的规范 JSON 计算。审批必须保存：

- `action_id`
- `action_digest`
- 用户选项
- comment

审批完成后，如果 Action 内容或 digest 改变，执行必须拒绝。

## 状态模型

### ActionStatus

```text
proposed
waiting_approval
approved
applying
applied
accepted
rolled_back
rejected
failed
```

合法迁移：

```text
proposed -> waiting_approval
waiting_approval -> approved | rejected
approved -> applying
applying -> applied | failed
applied -> accepted | rolled_back | waiting_approval
```

`waiting_approval` 在 `applied` 之后表示 mixed result 需要工程师复核。第一版不允许从终态重新打开同一 Action。

## 验证规则

Action Validator 是确定性程序，至少验证：

- `action_type == adjust_layout_void`
- `version == 1`
- layer、region_ref、shape、variable 非空
- shape 只能是 `circle` 或 `rectangle`
- old/new/delta 为有限数值
- `new - old == delta`，允许 `1e-6` 浮点误差
- new value 在 min/max 内
- `abs(delta) <= max_abs_delta`
- delta 不能为 0
- adapter mode 只能是 `recorded` 或 `real_aedt`
- recorded adapter 的 before/after Touchstone/TDR 路径完整

验证失败时不得创建 approval。

## Proposal Policy

LLM 可以解释原因，但第一版候选参数由确定性策略生成：

- 输入必须包含失败的 channel evidence。
- 动作只针对显式提供的 layer/variable/region。
- 默认步长不超过 `max_abs_delta_mil`。
- 若 TDR 主要表现为高阻峰，候选动作可扩大 void。
- 若没有足够证据或目标参数缺失，返回 `needs_user_input`。
- 同一 Mission 不得重复相同 digest。

策略输出完整 ActionRecord，不输出代码。

## Approval 绑定

Approval option 使用：

```json
{
  "id": "approve-action",
  "label": "批准受控 void 调整",
  "action_id": "...",
  "action_digest": "..."
}
```

执行 worker 必须读取已解析 ApprovalRequest，并验证：

- decision 为 approved；
- selected option 是 `approve-action`；
- option 的 action_id 和 digest 与当前 Action 完全一致。

普通 Mission approval 不自动等价为 Action approval。

## Adapter

### RecordedActionAdapter

职责：

- 验证 before/after artifacts 存在；
- 创建修改前 checkpoint manifest；
- 把 after artifacts 复制或登记到 action artifact 目录；
- 输出 ActionExecutionResult；
- 不声称修改了真实 AEDT project。

### RealAedtActionAdapter

第一版接口存在，但调用时返回 fail-closed 错误：

```text
real_aedt action adapter is not enabled
```

后续只有在真实项目 checkpoint、项目复制、AEDT 进程隔离和 smoke test 稳定后才启用。

## 比较和决策

before/after 均使用现有 deterministic channel evaluator。

```text
improved -> accepted
regressed -> rolled_back
unchanged -> rolled_back
mixed -> waiting_approval
```

LLM 只能解释判定，不可修改决策。

### Rollback

recorded adapter 的 rollback 表示：

- accepted artifact refs 恢复为 before refs；
- ActionRecord 标记 `rolled_back`；
- 记录 rollback reason 和 comparison；
- 产生 checkpoint/event；
- after artifacts 保留用于审计，不删除。

真实 AEDT rollback 后续通过复制的 `.aedt/.aedb` checkpoint 恢复，不依赖 live handle。

## 持久化

SQLite 新增：

- `action_records`
- `action_executions`

ActionRecord 持久化 proposal、digest、状态、审批引用、比较结果和最终 decision。

ActionExecutionRecord 持久化：

- adapter
- before/after artifact refs
- started/completed timestamps
- result
- error

所有 create/status change 都写 Mission Event。

## Worker 能力

```text
brd.action.propose_void
brd.action.apply
brd.action.compare
brd.action.rollback
```

第一版也允许一个组合 worker `brd.action.execute_recorded` 用于端到端测试，但内部必须调用相同 validator、approval binder、adapter 和 policy，不得绕过状态记录。

## Graph

模板：

```text
channel_score_worker
    -> void_action_proposer
    -> action_validator
    -> action_approval_gate
    -> action_apply_worker
    -> channel_compare_worker
    -> accept_or_rollback_policy
    -> final_scorecard
```

当前顺序 Graph Runner 仍只执行 queued worker，因此本阶段 CLI 可以预创建按顺序的 jobs；完整 edge-driven ready-set 在后续 graph runner 增强中实现。

## CLI

新增查询和控制面：

```text
mission actions --mission-id
mission action-status --action-id
mission approve-action --approval-id --action-id --action-digest
```

首版 end-to-end fixture 命令：

```text
mission create --brd-recorded-void-action ...
```

必须显式提供 before/after artifacts、layer、region、variable、old/new value。

## 测试

- Action JSON-ready 和 digest 稳定。
- 修改内容后 digest 改变。
- 越界 delta、空 layer、未知 shape 被拒绝。
- Approval digest 不匹配时 apply 拒绝。
- recorded adapter 不修改原始 artifacts。
- improved -> accepted。
- regressed/unchanged -> rolled_back。
- mixed -> waiting_approval。
- rollback 保留 after artifacts 和完整审计。
- real_aedt adapter fail closed。
- Action execution 跨 SQLite 重启可读取。
- raw S 参数/TDR 不进入 Action reason 或 LLM summary。

## 完成定义

1. Action Schema、状态机和 digest 已实现并持久化。
2. Approval 与 action_id/digest 强绑定。
3. recorded adapter 能完成 apply/compare/accept-or-rollback。
4. regressed/unchanged 自动 rollback，mixed 等待人工。
5. 所有动作和执行均可审计、可跨重启读取。
6. real AEDT adapter 默认关闭。
7. CLI 能查询 Action 状态和记录。
