# ansys-agent v2 架构设计

**Status:** 设计讨论中 | **Date:** 2026-06-17

---

## 一、总览

```
                    ┌──────────────────────────────────────┐
                    │         Orchestrator (外部)            │
                    │  理解任务 → 生成 YAML → 启动图 → 监控   │
                    │  Codex CLI / Claude Code / Pi / ...   │
                    └──────────────┬───────────────────────┘
                                   │ CLI / API 调用
                    ┌──────────────▼───────────────────────┐
                    │          ansys-agent Runtime          │
                    │                                      │
                    │  ┌─────────────────────────────────┐ │
                    │  │        YAML Graph Template       │ │
                    │  │  nodes + edges + handoffs + env  │ │
                    │  └─────────────┬───────────────────┘ │
                    │                │                      │
                    │  ┌─────────────▼───────────────────┐ │
                    │  │         Graph Runner              │ │
                    │  │  ready → execute → handoff → loop │ │
                    │  └─────────────┬───────────────────┘ │
                    │                │                      │
                    │     ┌──────────┼──────────┐          │
                    │     ▼          ▼          ▼          │
                    │  Agent A    Agent B    Agent C        │
                    │  (LLM)      (LLM)      (LLM)         │
                    │     │          │          │          │
                    │     └──────────┼──────────┘          │
                    │                ▼                      │
                    │  ┌─────────────────────────────────┐ │
                    │  │           Scorecard              │ │
                    │  │  程序审计(不是LLM),翻DB查证据     │ │
                    │  └─────────────────────────────────┘ │
                    │                                      │
                    │  ┌─────────────────────────────────┐ │
                    │  │     Infrastructure               │ │
                    │  │  SQLite · Harness · AEDT适配器   │ │
                    │  └─────────────────────────────────┘ │
                    └──────────────────────────────────────┘
```

**核心原则：**

1. **YAML 是系统核心接口** — 所有编排逻辑、agent 定义、约束都在 YAML 里
2. **每个节点是独立的 LLM agent** — 不是确定性函数，是带 System Prompt + 约束的 LLM 调用
3. **Handoff 是唯一的通信方式** — agent 之间不对话，只传结构化 JSON
4. **Scorecard 是程序审计** — 不依赖 LLM，翻 DB 查真实记录
5. **Orchestrator 是外部大脑** — 站在图外面，监控、介入、接管

---

## 二、YAML 模板 — 六层配置

```yaml
id: brd_channel_optimize
version: 1
description: "BRD channel optimization: analyze → build → solve → score → decide"

# ── 层 1: 拓扑 ──
nodes:
  - id: analyze
    role: planner
    kind: agent
    system_prompt: analyze_prompt
    input_schema: task_input
    output_schema: analysis_result
    model: gpt-4.1-mini
    profile: low_cost
    constraints: {temperature: 0.2, response_format: json_object}
    max_runs: 1

  - id: build_model
    role: worker
    kind: agent
    capability: code_writer        # ← 这个标记触发了 Code Agent 的三层约束
    system_prompt: build_prompt
    input_schema: analysis_result
    output_schema: build_evidence
    model: gpt-4.1-mini
    profile: standard
    constraints:
      temperature: 0.1
      max_tokens: 4096
      allowed_imports: [aedt_agent, pyedb, ansys.aedt.core]
      forbidden_patterns: ["os.system", "subprocess", "eval(", "exec("]
    after: [analyze]

  - id: score_channel
    role: evaluator
    kind: agent
    system_prompt: score_prompt
    input_schema: build_evidence
    output_schema: score_result
    model: gpt-4.1-mini
    profile: low_cost
    constraints:
      temperature: 0.3
      read_only: true          # ← 不能写文件，不能调 AEDT
    after: [build_model]

  - id: decide
    role: decision_maker
    kind: agent
    system_prompt: decide_prompt
    input_schema: score_result
    output_schema: next_action
    model: gpt-4.1
    profile: high_reasoning
    constraints:
      temperature: 0.4
      allowed_decisions: [continue, adjust_void, adjust_clearance, widen_trace, rollback, complete]

edges:
  - id: a-to-b
    from: analyze
    to: build_model
    on: succeeded
  - id: b-to-s
    from: build_model
    to: score_channel
    on: succeeded
  - id: s-to-d
    from: score_channel
    to: decide
    on: succeeded
  - id: d-loop
    from: decide
    to: build_model
    on: continue
    max_traversals: 5
  - id: d-complete
    from: decide
    to: scorecard
    on: complete

# ── 层 2: Agent 定义 (System Prompts) ──
prompts:
  analyze_prompt: |
    You are an RF/microwave engineering agent specializing in BRD/MCM
    channel optimization. Analyze the user's request and produce a structured
    plan with: signal_nets, reference_nets, frequency_range, target_metrics,
    port_configuration, and suggested optimization strategy.

  build_prompt: |
    You are an AEDT automation agent. Write PyAEDT Python code to build
    the HFSS 3D Layout model as specified in the analysis.
    - Only use imports from: pyedb, ansys.aedt.core
    - Never call os.system, subprocess, eval, or exec
    - Output: the Python code + a list of expected artifacts (project path,
      port count, setup name, sweep range)
    - If the project already exists, update it; do not create duplicate setups.

  score_prompt: |
    You are a signal integrity evaluator. Read the build artifacts and
    score the channel.
    - Read Touchstone S-parameters and TDR CSV from artifact paths
    - Determine: worst RL, pass band, TDR peak deviation, anomaly windows
    - Compare against target_metrics
    - Output: pass/fail, detailed scores, anomalies found
    - Do NOT write any files. Only read artifacts.

  decide_prompt: |
    You are an optimization strategist. Based on channel scores and
    previous actions, decide the next step.
    - If all metrics pass: output "complete"
    - If RL too high: propose impedance adjustment (void or trace width)
    - If TDR deviation: propose stackup or clearance adjustment
    - If no improvement for 2 rounds: output "rollback" or "complete"
    - Output exactly one decision from the allowed list.

# ── 层 3: Profile ──
profiles:
  low_cost:
    model: gpt-4.1-mini
    temperature: 0.2
    max_tokens: 2048
  standard:
    model: gpt-4.1-mini
    temperature: 0.1
    max_tokens: 4096
  high_reasoning:
    model: gpt-4.1
    temperature: 0.4
    max_tokens: 4096

# ── 层 4: 环境/容器 ──
# (当前阶段: local process harness, 后续支持 Docker)
environment:
  default: host_python
  options:
    host_python:
      type: local_process
      python: .venv/Scripts/python.exe
    docker_aedt:
      type: docker
      image: ansys-aedt:2026.1
      mounts: ["C:/ansys_inc:/ansys_inc:ro"]

# ── 层 5: 安全限位 ──
security:
  code_writer:
    max_code_length: 2000
    timeout_seconds: 300
    sandbox: process_harness
    allowed_paths: ["{artifact_dir}", "{workspace}"]
  read_only:
    allowed_operations: [read_file, parse_csv, parse_touchstone]
    forbidden_operations: [write_file, execute_aedt, shell_command]

# ── 层 6: Handoff 定义 ──
handoffs:
  task_input:
    required_fields: [goal, layout_file, signal_nets]
  analysis_result:
    required_fields: [plan_summary, signal_nets, frequency_range, target_metrics, port_config]
  build_evidence:
    required_fields: [status, code_output, artifact_refs, project_path]
  score_result:
    required_fields: [status, rl_worst_db, tdr_peak_deviation_ohm, pass_band, anomalies]
  next_action:
    required_fields: [decision, reason, suggested_parameters]
```

---

## 三、Agent 类型与约束矩阵

| Agent 类型 | role | 做什么 | 约束 |
|-----------|------|-------|------|
| **analyzer** | planner | 分析需求→结构化计划 | JSON mode, 低温 |
| **coder** | worker | 写 PyAEDT 代码 | 三层约束: 语法校验→import白名单→沙箱 |
| **evaluator** | evaluator | 读结果→评分 | read_only, 只能读 artifact |
| **decider** | decision_maker | 基于分数→决策下一步 | allowed_decisions 白名单 |
| **reviewer** | reviewer | 检查 handoff 完整性 | read_only, 输出 pass/fail+理由 |

### Code Agent 三层约束

```
LLM 生成代码
    │
    ▼
[1] 语法校验: ast.parse(code) → SyntaxError? → 打回重写
    │
    ▼
[2] Import 白名单: 扫描 import 语句 → 不在白名单? → 打回重写
    │
    ▼
[3] 沙箱执行: LocalProcessHarness → 隔离 workspace → timeout → 捕获异常
    │
    ▼
 成功 → handoff artifact_refs
 失败 → handoff error + stderr → 上游 agent 决定 retry/skip/fail
```

---

## 四、GraphNode 升级

```python
@dataclass(frozen=True)
class GraphNode:
    # 现有字段
    node_id: str
    role: str            # planner | worker | evaluator | reviewer | decision_maker | scorecard | approval_gate
    kind: str            # agent | program | human_gate
    capability: str      # coder 节点用: "code_writer" 触发三层约束
    input_schema: str
    output_schema: str
    join: str
    after: list[str]
    max_runs: int
    handler: str
    on_failure: str
    retry_max_attempts: int
    retry_backoff: str
    retry_delay_seconds: float
    fan_out: bool
    expand: bool

    # ── 新增: Agent 层 ──
    system_prompt: str = ""           # prompt key 或 内联 prompt
    model: str = ""                   # 模型名,空=用 profile 的
    profile: str = "standard"         # profile key
    constraints: dict[str, Any] = {}  # 约束配置

class GraphTemplate:
    # 现有字段...
    # ── 新增层 ──
    prompts: dict[str, str]           # prompt 字典
    profiles: dict[str, dict]         # profile 字典
    environment: dict[str, Any]       # 环境配置
    security: dict[str, Any]          # 安全限位
```

---

## 五、Agent Executor

```python
def execute_agent_node(context: GraphNodeExecutionContext) -> GraphNodeExecutionResult:
    """执行一个 LLM agent 节点。"""

    node = context.node
    template = context.template

    # 1. 解析 prompt
    prompt_text = _resolve_prompt(node.system_prompt, template.prompts)

    # 2. 解析约束
    profile = _resolve_profile(node.profile, template.profiles)
    constraints = {**profile, **node.constraints}

    # 3. 构建 LLM 消息
    system = prompt_text
    user = json.dumps({
        "handoff": context.input_payload,
        "constraints": constraints,
        "artifacts": _list_artifacts(context),
    })

    # 4. 调用 LLM
    raw = llm_complete(system, user, model=constraints.get("model"))

    # 5. Code Agent 特殊处理
    if node.capability == "code_writer":
        code = _extract_code(raw)
        code = _validate_syntax(code)       # 约束 1
        code = _validate_imports(code, constraints.get("allowed_imports", []))  # 约束 2
        result = _run_in_sandbox(code, context)  # 约束 3
        return _code_handoff(result, context)

    # 6. 普通 Agent: 解析 JSON + 校验 handoff
    output = _parse_json(raw)
    validate_handoff(template.handoffs[node.output_schema], output)
    return success_result(output)
```

---

## 六、Orchestrator 接口

Orchestrator（外部）通过 CLI/API 操作 ansys-agent：

```bash
# 1. 创建 mission + graph_run（Orchestrator 选好模板）
python -m aedt_agent.agent mission create --goal "..." --template brd_channel_optimize ...

# 2. 推进 graph（Orchestrator 循环调用）
python -m aedt_agent.agent mission advance-graph --graph-run-id <id>

# 3. 查看状态（Orchestrator 轮询）
python -m aedt_agent.agent mission graph-status --graph-run-id <id>

# 4. 处理审批
python -m aedt_agent.agent mission approve --approval-id <id> --decision approve

# 5. 介入 - 取消当前 graph, 用新参数创建接管 run
python -m aedt_agent.agent mission takeover --graph-run-id <id> --reason "..." --new-template ...
```

Orchestrator 伪代码：

```python
def orchestrator_loop(goal: str):
    # Step 1: 理解任务,选模板
    template_id = llm_select_template(goal)
    payload = llm_generate_initial_payload(goal, template_id)

    # Step 2: 创建并启动
    mission = cli("mission create", goal, template_id, payload)
    graph_run = mission["graph_run_id"]

    # Step 3: 监控循环
    while True:
        status = cli("graph-status", graph_run)

        if status["status"] == "succeeded":
            return final_report(status)
        if status["status"] == "failed":
            decision = llm_decide_intervention(status)
            if decision == "retry_with_fix":
                graph_run = cli("mission takeover", graph_run, decision)
            else:
                return failure_report(status, decision)
        if status["status"] == "waiting_approval":
            decision = llm_decide_approval(status)
            cli("mission approve", status["approval_id"], decision)

        cli("advance-graph", graph_run)
```

---

## 七、实现路线

| Phase | 内容 | 优先 |
|-------|------|------|
| **A** | GraphNode + GraphTemplate 扩展（prompts/profiles/constraints） | P0 |
| **B** | Agent Executor（LLM 调用 + handoff 校验） | P0 |
| **C** | Code Agent 三层约束（语法→import→沙箱） | P0 |
| **D** | 升级 YAML 模板（prompts + profiles + security 层） | P1 |
| **E** | Scorecard 扩展（agent 输出审计） | P1 |
| **F** | Orchestrator CLI 接口完善 | P1 |
| **G** | Orchestrator 外部适配（Codex/Pi） | P2 |
