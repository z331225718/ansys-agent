# Model-Review 级 BRD Demo 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `brd_local_cut_build.yaml` 模板的完整链路跑通：输入 BRD 参数 → 生成/校验计划 → real_build → model-review scorecard → approval。关键交付：`brd_local_cut_summary.json`、`workflow_run.json`、可审查的 scorecard、端口不确定时进入 approval。

**Architecture:** 升级 `_execute_planner` 生成结构化 brd_local_cut_request、升级 `_execute_validator` 做真实校验、新增 `model_review_scorecard` handler、扩展 `brd_local_cut_build.yaml` 使用 fan-out + 条件边。CLI 新增 `--brd-local-cut-model-review` 子命令。

**Tech Stack:** Python 3.11+、dataclasses、YAML、pytest、现有 graph/worker 基础设施。

---

## 文件结构

- `src/aedt_agent/agent/graph_executors.py`：升级 `_execute_planner`、`_execute_validator`。
- `src/aedt_agent/agent/scorecard.py`：新增 `model_review_checks()`。
- `src/aedt_agent/agent/cli.py`：新增 `--brd-local-cut-model-review` 子命令。
- `docs/agent_templates/brd_local_cut_build.yaml`：升级为 fan-out + model-review 结构。
- `tests/test_agent_graph_runner_dag.py`：新增端到端 model-review 测试。
- `tests/test_agent_cli_brd_mission.py`：新增 CLI model-review 测试。

---

## Task 1：升级 Planner — 从输入生成结构化 BRD 计划

**现状：** `_execute_planner` 只做 input_payload 拷贝，不生成任何结构化内容。

**目标：** Planner 接收包含 `layout_file`、`signal_nets`、`reference_nets`、`local_cut_region` 等字段的 initial_payload，生成完整的 `brd_local_cut_request`：

```python
# 输出示例
{
    "layout_file": "/path/to/brd",
    "signal_nets": ["CLK0", "CLK1"],
    "reference_nets": ["GND"],
    "local_cut_region": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
    "artifact_dir": "/path/to/artifacts",
    "adapter_mode": "real",
    "target_metrics": [{"type": "rl", "target_db": -20, "freq_ghz": 2.4}],
    "port_hint": {"style": "uniform_line", "count": 2},
    "plan_summary": "BRD local-cut build for CLK0/CLK1 channel optimization"
}
```

- [ ] **Step 1：扩展 `_execute_planner`**

当 input_schema 为 `brd_local_cut_request` 时，补全缺失字段、设置默认值、输出 `plan_summary`。

- [ ] **Step 2：写测试**

```python
def test_planner_generates_brd_local_cut_request_from_input():
    """planner 从 minimal input 生成完整 brd_local_cut_request"""
    
def test_planner_preserves_user_overrides():
    """planner 保留用户显式指定的值"""

def test_planner_adds_artifact_dir_when_missing():
    """planner 自动推导 artifact_dir"""
```

- [ ] **Step 3：确认红灯 → 实现 → 绿灯**

---

## Task 2：升级 Validator — 真实工程校验

**现状：** `_execute_validator` 只做 handoff schema 校验（字段存在性）。

**目标：** 对 `brd_local_cut_request` 做工程语义校验：

- `layout_file` 存在且可读
- `signal_nets` 非空
- `local_cut_region` 是有效矩形
- `target_metrics` 有合理值（RL < 0, freq > 0）
- `port_hint` 有明确端口数量和方向
- 不确定时输出 `approval_required` outcome

- [ ] **Step 1：实现 `_validate_brd_request()` 函数**

返回 `(validated_payload, warnings, needs_approval)`。

- [ ] **Step 2：升级 `_execute_validator`**

当 input_schema 为 `validated_brd_local_cut_request` 时调用 `_validate_brd_request()`。

- [ ] **Step 3：写测试**

```python
def test_validator_rejects_missing_layout_file():
def test_validator_rejects_empty_signal_nets():
def test_validator_warns_on_ambiguous_ports_and_requests_approval():
def test_validator_accepts_valid_request():
```

- [ ] **Step 4：确认红灯 → 实现 → 绿灯**

---

## Task 3：Model-Review Scorecard

**现状：** `score_mission` 做基础检查（job 存在、有 artifact、有 evidence_summary）。

**目标：** 对 BRD build 结果做 model-review 语义检查：

- 项目文件存在（.aedt）
- cutout 成功（端口数量匹配）
- stackup 正确应用
- 端口类型/方向合理
- build summary 包含关键几何参数

- [ ] **Step 1：在 `scorecard.py` 中新增 `model_review_checks()`**

```python
def model_review_checks(runtime, mission_id: str) -> list[dict]:
    """检查 BRD local-cut build 的工程语义"""
    # - project_path 存在
    # - port count 匹配 hint
    # - cutout bbox 匹配请求
    # - build summary 完整
```

- [ ] **Step 2：注册 `model_review_scorecard` handler**

- [ ] **Step 3：写测试**

```python
def test_model_review_scorecard_passes_with_complete_build():
def test_model_review_scorecard_fails_when_project_missing():
def test_model_review_scorecard_flags_port_count_mismatch():
```

- [ ] **Step 4：确认红灯 → 实现 → 绿灯**

---

## Task 4：升级 BRD Template + 端到端 CLI

**目标：** 模板使用升级后的 planner/validator/scorecard，CLI 一键运行。

### 模板结构

```yaml
id: brd_local_cut_build
nodes:
  - planner        → generates brd_local_cut_request
  - input_validator → validates + flags uncertain ports → can emit approval_required
  - real_build_worker → builds AEDT project
  - model_review_scorecard → model-review checks
  - approval_gate  → human approval for port selection
edges:
  planner → validator (succeeded)
  validator → build (succeeded)
  validator → approval_gate (approval_required)  # port uncertainty
  build → scorecard (succeeded)
  build → approval_gate (approval_required)      # post-build review
  scorecard → approval_gate (passed)
```

- [ ] **Step 1：更新 `brd_local_cut_build.yaml`**

- [ ] **Step 2：CLI 新增 `--brd-local-cut-model-review` 模式**

```bash
python -m aedt_agent.agent mission create \
  --goal "Review BRD CLK0/CLK1 channel model" \
  --brd-local-cut-model-review \
  --layout-file /path/to/board.brd \
  --signal-net CLK0 --signal-net CLK1 \
  --bbox 0,0,10,10
```

- [ ] **Step 3：写端到端测试**

```python
def test_model_review_cli_creates_mission_and_runs_full_dag():
def test_model_review_template_runs_with_fake_adapter():
```

- [ ] **Step 4：确认红灯 → 实现 → 绿灯**

---

## Task 5：全量回归

- [ ] 所有已有 graph 测试绿色
- [ ] CLI 测试绿色
- [ ] 全仓失败集合不扩大

---

## 完成定义

- [ ] Planner 从 BRD 参数生成完整 `brd_local_cut_request`。
- [ ] Validator 做工程语义校验，端口不确定时输出 `approval_required`。
- [ ] Model-review scorecard 检查 project 存在、端口匹配、build summary 完整。
- [ ] `brd_local_cut_build.yaml` 模板完整跑通 planner→validator→build→scorecard→approval。
- [ ] CLI `--brd-local-cut-model-review` 一键启动全链路。
- [ ] Fake adapter 覆盖 CI，真实 AEDT smoke 可选。
- [ ] 所有已有测试绿色。
