# Stage B 当前记忆

更新日期：2026-05-14

## 项目状态

- 仓库：`/home/zzmjay/code/ansys-agent`
- GitHub：`https://github.com/z331225718/ansys-agent`
- 当前分支：`stage-a-grounding-benchmark`
- 最新已推送提交：
  - `32e285a Strengthen stage b wave port validation`
  - `9449d1d Improve stage b port node planning`
  - `7630a66 Advance stage b real node validation`

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

## 已验证结果

单元测试：

```bash
.venv/bin/python -m pytest -q
```

最新结果：

- `118 passed, 2 skipped`

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
- validation checks：
  - `session_available`
  - `wave_port_present`
  - `wave_port_uses_selected_face`

## 重要经验

- C 组不能只给 task 的 `allowed_nodes`，因为真实 AEDT 会话是空模型。涉及端口/边界/face 的任务必须允许 prerequisite nodes 先创建最小几何。
- LLM 常把节点输入写成 PyAEDT 代码字符串，或者使用 `position/type/dimensions` 这类字段。Stage B 应该用 schema 示例和有限 normalization 吸收这类低价值错误。
- Trap 任务不能只验证对象是否存在。至少要验证节点间数据流是否符合预期，例如端口 assignment 来自选中的 face。
- 当前 Trap 判卷仍不是完整电磁语义判断；它是比“端口存在”更强的结构性检查。后续若要正式报告，需要继续增强几何/物理 validation。

## 下一步

建议执行 5-task C-only smoke，先不跑 B，节省 token 和 AEDT 时间：

```bash
.venv/bin/python scripts/run_stage_b_benchmark.py \
  --groups C \
  --task L1_create_substrate \
  --task L1_create_setup \
  --task L1_create_wave_port \
  --task L2_microstrip_line \
  --task Trap_waveport_wrong_face \
  --max-attempts 3 \
  --run-dir benchmarks/runs/stage_b_c_5task_smoke
```

观察重点：

- first-pass rate 是否稳定高于 Stage A Group B baseline 的 80%。
- 失败是否集中在 schema/normalization，而不是 AEDT API 调用。
- `L2_microstrip_line` 是否需要更强的 airbox/radiation validation。
- 如果 5-task C-only 稳定，再跑 B/C 对照并生成中文 Stage B HTML 报告。
