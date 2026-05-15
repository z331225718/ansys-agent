# Stage B 当前记忆

更新日期：2026-05-15

## 项目状态

- 仓库：`/home/zzmjay/code/ansys-agent`
- GitHub：`https://github.com/z331225718/ansys-agent`
- 当前分支：`stage-a-grounding-benchmark`
- 最新已推送提交：
  - `e88937c Add stage b html report`
  - `3d000c7 Normalize stage b port integration lines`
  - `2dc995c Isolate stage b node attempts`

## Stage B 目标

Stage B 不是继续比较裸 LLM 写 Python，而是验证节点化路径是否比 Stage A Group B 的 grounded free-code harness 更可控、可审计、可修复。

- Group B：Stage A 最终 baseline，Claude Code harness + GitNexus/PyAEDT 官方源码/示例，生成自由 Python。
- Group C：Stage B candidate，Claude Code harness 只生成 JSON node plan，本地 runner 调用 `execute_node`，不允许自由 Python fallback。
- 判据必须来自真实 AEDT 2026.1 non-graphical 执行和 validation，不把 fake adapter 结果当 benchmark 证据。

## 当前已完成能力

- `PyaedtAdapter` 可以自动识别 `~/ansys_inc/v261`，并补齐 `ANSYSEM_ROOT261/AWP_ROOT261`。
- `run_stage_b_benchmark.py` 支持真实 C 组：harness 生成 JSON node plan，本地 kernel 执行节点。
- C 组执行后会读取 `get_model_info()`，再调用 validation script 判定，不再是“节点无异常就算 pass”。
- 支持节点输出引用：
  - 示例：`{"$ref": "select_face.output.selected_face_id"}`
- `select_face/create_port` 的 allowed nodes 会自动展开 prerequisite：
  - `create_conductor_or_geometry_group -> select_face -> create_port`
- 几何节点兼容常见 LLM 字段别名：
  - `type -> kind`
  - `position -> origin`
  - `sizes/dimensions -> size`
  - `matname -> material`
- Trap wave port validation 已增强：
  - 不只检查 `wave_port_present`
  - 还检查 `create_port.assignment` 是否能追溯到 `select_face.output.selected_face_id`
  - 通过 check 名：`wave_port_uses_selected_face`
- 2026-05-15 新增：
  - 每次 C 组 attempt 使用独立 AEDT project/session，避免失败尝试污染修复尝试。
  - 先生成/解析 node plan，再启动 AEDT，避免 generation/parse 失败时浪费 AEDT session。
  - 节点输出增加便利字段：`object_name/object_names/port_name/boundary_name/setup_name/sweep_name`。
  - `create_airbox.padding` 支持数值列表并归一化为最大 padding。
  - `assign_boundary` 和 `create_port` 可以接受上游节点完整 output 作为 `assignment/reference`，执行时提取合适对象。
  - `create_port.integration_line` 支持 `{"start": [...], "end": [...]}` 并归一化为两点列表。
  - 增加 Stage B 中文 HTML 报告生成器：`src/aedt_agent/benchmark/report_html_stage_b.py`。
  - `scripts/run_stage_b_benchmark.py` 现在会在 run dir 下同时写出 `stage_b_report.html`。
  - `create_sweep_or_export` 兼容 PyAEDT 2026.1 的 `unit` 参数签名，并把 `"1GHz"` 这类字符串拆成数值和单位。
  - `select_face` 输出补充 `object_name`；`create_port` 在 lumped port 收到 face id 时会回溯所属 sheet/object，避免 AEDT 报 `a geometry selection is required for assignment`。
  - 节点 audit 的 snapshot 失败会记录 `snapshot_error`，不再让失败后的 AEDT 状态读取直接中断 benchmark。
  - `create_conductor_or_geometry_group` 对 LLM 常输出的 cylinder 输入做 box 近似，减少与节点 schema 无关的低价值失败。
  - C 组提示词明确要求 lumped port 优先使用 port sheet 对象名，sweep 使用频率字符串。

## 已验证结果

单元测试：

```bash
.venv/bin/python -m pytest -q
```

最新结果：

- `121 passed, 2 skipped`

真实 AEDT / harness smoke：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups C \
  --task L1_create_wave_port \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_c_l1_wave_port_real_harness_v3
```

结果：

- `L1_create_wave_port`：PASS，1/3
- validation checks：
  - `session_available`
  - `wave_port_present`

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups C \
  --task Trap_waveport_wrong_face \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_c_trap_waveport_real_harness_v2
```

结果：

- `Trap_waveport_wrong_face`：PASS，1/3

当前可展示 HTML 报告：

- `benchmarks/reports/stage_b_5task_compare.html`
- `benchmarks/reports/stage_b_10task_compare.html`
- validation checks：
  - `session_available`
  - `wave_port_present`
  - `wave_port_uses_selected_face`

5-task C-only smoke v2：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups C \
  --task L1_create_substrate \
  --task L1_create_setup \
  --task L1_create_wave_port \
  --task L2_microstrip_line \
  --task Trap_waveport_wrong_face \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_c_5task_smoke_v2
```

结果：

- `task_count`: 5
- `first_pass_rate`: 1.0
- `pass_rate_3try`: 1.0
- `avg_attempts_to_success`: 1.0
- `avg_attempts_all`: 1.0
- `avg_node_count`: 2.4
- `free_code_execution_count`: 0
- `failure_categories`: `{}`

5-task B/C compare：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups B C \
  --task L1_create_substrate \
  --task L1_create_setup \
  --task L1_create_wave_port \
  --task L2_microstrip_line \
  --task Trap_waveport_wrong_face \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_bc_5task_compare
```

结果：

- Group B:
  - `task_count`: 5
  - `first_pass_rate`: 0.8
  - `pass_rate_3try`: 0.8
  - 失败任务：`L1_create_wave_port`
  - 失败原因：第 1/3 轮均为 `AssignWavePort` runtime error，第 2 轮 harness 900s timeout。
- Group C:
  - `task_count`: 5
  - `first_pass_rate`: 0.8
  - `pass_rate_3try`: 1.0
  - `avg_attempts_to_success`: 1.2
  - `free_code_execution_count`: 0
  - 首轮失败任务：`Trap_waveport_wrong_face`，原因是 `integration_line` 使用了 `{"start": ..., "end": ...}` 格式。

修复 `integration_line` 归一化后，单独重跑：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups C \
  --task Trap_waveport_wrong_face \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_c_trap_waveport_after_line_normalization
```

结果：

- `Trap_waveport_wrong_face`：PASS，1/3

10-task C-only after node fixes：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups C \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_c_10task_after_node_fixes
```

结果：

- `task_count`: 10
- `first_pass_rate`: 0.8
- `pass_rate_3try`: 1.0
- `avg_attempts_to_success`: 1.2
- `avg_attempts_all`: 1.2
- `avg_node_count`: 4.3
- `node_coverage_rate`: 1.0
- `free_code_execution_count`: 0
- 失败类别：`{}`

10-task B-only after node fixes：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups B \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_b_10task_after_node_fixes
```

结果：

- `task_count`: 10
- `first_pass_rate`: 0.7
- `pass_rate_3try`: 0.9
- `avg_attempts_to_success`: 1.3333333333333333
- `avg_attempts_all`: 1.5
- `tool_usage_rate`: 1.0
- `avg_gitnexus_queries`: 6.9
- `retrieval_before_code_rate`: 1.0
- 失败任务：`L2_dipole_antenna`
- 失败类别：`{"generation_error": 1}`

10-task 对照汇报版报告：

- JSON：`benchmarks/reports/stage_b_10task_compare.json`
- HTML：`benchmarks/reports/stage_b_10task_compare.html`
- 报告结论：B 组三次内成功率 90%，C 组三次内成功率 100%；B 组首轮 70%，C 组首轮 80%；C 组自由代码执行次数 0，平均成功轮次 1.2。

## 重要经验

- C 组不能只给 task 的 `allowed_nodes`，因为真实 AEDT 会话是空模型。涉及端口/边界/face 的任务必须允许 prerequisite nodes 先创建最小几何。
- LLM 常把节点输入写成 PyAEDT 代码字符串，或者使用 `position/type/dimensions` 这类字段。Stage B 应该用 schema 示例和有限 normalization 吸收这类低价值错误。
- Trap 任务不能只验证对象是否存在。至少要验证节点间数据流是否符合预期，例如端口 assignment 来自选中的 face。
- 当前 Trap 判卷仍不是完整电磁语义判断；它是比“端口存在”更强的结构性检查。后续若要正式报告，需要继续增强几何/物理 validation。
- 多次 attempt 必须隔离 AEDT session。之前同一 task 的修复尝试复用同一 session，会导致第二轮在第一轮残留对象上成功，benchmark 证据不干净。
- B/C 小集显示 C 组的主要收益不是所有任务 first-pass 立刻更高，而是三轮内成功率更稳定、失败更可控、无自由代码执行；B 组在 wave port 上会出现自由代码难以修复的 runtime error 和长时间 harness timeout。

## 下一步

中文 Stage B 10-task 汇报版 HTML 已生成。下一步建议：

1. 复查 `benchmarks/reports/stage_b_10task_compare.html` 的表述和截图效果。
2. 若要继续 Stage B，实现更细的节点 schema/validation；若进入下一阶段，再考虑节点分解、更多物理语义判卷和更大任务集。

报告已经展示：

- B/C 10-task 对照指标。
- B 组 `L2_dipole_antenna` 三轮失败原因。
- C 组节点化如何通过 schema、节点输出、真实 AEDT 执行和 validation 控制风险。
- 当前 validation 仍有限，Trap 的电磁语义仍只是结构性检查，不应夸大。
