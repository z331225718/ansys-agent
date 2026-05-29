# Stage C BRD Via Optimization Agent Design

## Goal

把 Stage C BRD/MCM workflow 从“可生成并验收模型”推进到“能辅助工程师优化高速通道过孔 TDR 和回波损耗”的生产级 agent。第一阶段目标不是全自动替代工程师，而是让 agent 能稳定搭建仿真模型、读取 S 参数/TDR、定位问题层、提出并执行受控的挖空调整，形成可复盘的优化记录。

## User Workflow

真实工程流程如下：

1. 从 BRD/MCM 导入版图。
2. 选择目标 net 或差分 pair，执行 cutout。
3. 导入 stackup。
4. 基于 cutout 后的模型建立 component bump/ball。
5. 建立 pin/component port。
6. 建立差分传输线 port。
7. 修改机械孔孔径：BRD 导入的是完成孔径，仿真需要的是外圈孔径。
8. 修改 backdrill 尺寸：如果有 backdrill，该尺寸由工艺能力决定，优化过程中默认不变化。
9. 设置 DC-to-26.56GHz 或更宽频段仿真。
10. 运行仿真，读取 S 参数和 TDR。
11. 调整某一具体层的挖空。
12. 再仿真，根据 TDR 结果判断上层或下层挖空需要继续调整。
13. 迭代到 TDR 较平滑，同时 0-26.56GHz 内 RL 尽可能小于 -20dB。

## Scope

本阶段只覆盖 BRD/MCM 高速通道过孔优化，不覆盖天线、腔体、普通微带线 demo。

包含：

- BRD/MCM 导入、cutout、stackup、端口、setup 的复用。
- 机械孔孔径修正。
- backdrill 参数识别和锁定。
- 指定层 anti-pad / void / plane cutout 挖空尺寸调整。
- S 参数和 TDR 后处理。
- 每轮优化记录、报告和人工可审查建议。

不包含：

- 自动改变 backdrill 工艺能力约束。
- 自动移动器件、改走线、重布线。
- 自动新增复杂拓扑，例如 AC coupling 电容两端跨器件优化。
- 未经确认直接批量修改多个不相关区域。
- 对所有 board 通用的端口识别承诺。端口规则仍先按目标板和脚本逐步固化。

## Optimization Target

默认优化目标：

- 频段：0-26.56GHz。
- 回波损耗：RL/Sdd11 尽可能小于 -20dB。
- TDR：阻抗曲线尽可能平滑，避免明显台阶和尖峰。
- 约束：backdrill 尺寸如果存在则固定；机械孔要从完成孔径修正为外圈孔径；每轮只改少量明确层的挖空，保留可回退记录。

评价不只看单点最优值。agent 应同时输出：

- `rl_pass_band`: 满足 -20dB 的频段范围。
- `rl_worst_db`: 0-26.56GHz 内最差 RL。
- `tdr_peak_deviation_ohm`: TDR 相对目标阻抗的最大偏差。
- `tdr_discontinuity_location`: TDR 异常对应的时间/距离窗口。
- `changed_layers`: 本轮改动的层。
- `improvement_summary`: 相比上一轮 RL 和 TDR 是否改善。

## Architecture

优化 agent 分为五层：

1. **Model Build Layer**  
   复用现有 BRD/MCM experimental workflow：导入、cutout、stackup、component bump/ball、port、setup。输出 AEDT project、EDB、端口计划和生产验收 artifact。

2. **Board Normalization Layer**  
   处理仿真前必须修正的 board 语义差异：完成孔径到外圈孔径、backdrill 固定尺寸、目标差分 pair、reference net、可调层列表、禁止修改层列表。

3. **Simulation and Measurement Layer**  
   显式运行 analyze，导出 Touchstone 和 TDR。读取 Sdd11/Sdd21、TDR 曲线，并将结果转换成结构化指标。

4. **Diagnosis Layer**  
   根据 RL/TDR 指标判断问题来源：上层挖空不足、下层挖空不足、目标层 transition 不连续、端口或 ball 设置异常。此层先允许工程规则主导，LLM 负责解释和选择候选动作。

5. **Optimization Action Layer**  
   执行一轮受控模型修改：只调整一个或少数指定层的挖空尺寸，生成 diff-like action record。每轮都保留输入、输出、脚本日志、仿真结果和判断理由。

## Node Model

后续应新增或固化以下 experimental nodes：

| Node | Purpose |
| --- | --- |
| `normalize_layout_via_geometry` | 将完成孔径修正为仿真需要的外圈孔径，记录孔径来源和修改对象 |
| `lock_layout_backdrill_rules` | 识别并锁定 backdrill 尺寸，作为工艺约束写入 context |
| `create_layout_differential_ports` | 建立差分传输线 port，复用现有 component/pin port 规则 |
| `solve_layout_channel` | 显式运行仿真，默认目标 0-26.56GHz，可配置到更高频 |
| `extract_layout_sparameters_tdr` | 导出并解析 S 参数和 TDR |
| `score_layout_channel` | 计算 RL/TDR 指标和 pass/fail/warning |
| `propose_layout_void_adjustment` | 根据指标和可调层提出挖空调整 |
| `apply_layout_void_adjustment` | 执行某一层或上下层挖空尺寸修改 |
| `package_layout_optimization_run` | 汇总每轮优化记录和中文报告 |

这些节点全部属于 `layout-brd` experimental track，不进入默认 HFSS core catalog。

## Data Contract

优化输入：

```json
{
  "layout_file": "D:/boards/case.brd",
  "stackup_xml": "D:/boards/stackup.xml",
  "signal_nets": ["SRDS_3_RX1_P", "SRDS_3_RX1_N"],
  "reference_nets": ["GND"],
  "target_frequency_stop": "26.56GHz",
  "target_impedance_ohm": 100,
  "rl_target_db": -20,
  "mechanical_hole_rule": {
    "source": "finished_hole",
    "simulation_uses": "outer_drill"
  },
  "backdrill_rule": {
    "mode": "locked_by_process"
  },
  "tunable_layers": ["L3", "L4", "L5"],
  "protected_layers": ["TOP", "BOTTOM"],
  "max_iterations": 3,
  "human_confirm_each_iteration": true
}
```

每轮输出：

```json
{
  "iteration": 1,
  "status": "needs_adjustment",
  "rl_worst_db": -15.8,
  "rl_target_db": -20,
  "tdr_peak_deviation_ohm": 8.4,
  "diagnosis": "TDR step suggests lower-layer void is too small near the via transition.",
  "proposed_action": {
    "type": "adjust_layer_void",
    "layers": ["L5"],
    "direction": "increase",
    "delta": "4mil",
    "reason": "Reduce capacitive discontinuity at lower via transition."
  },
  "artifacts": {
    "aedt_project": "...",
    "touchstone": "...",
    "tdr_csv": "...",
    "report_html": "..."
  }
}
```

## Control Policy

优化阶段必须默认保守：

- 每轮最多修改一个局部策略组，例如同一 via transition 的上层或下层挖空。
- backdrill 尺寸默认只读，不作为优化变量。
- 修改 mechanical hole 时必须记录原始完成孔径和仿真外圈孔径。
- 如果端口、stackup 或 ball 设置异常，agent 应停止优化并要求先修模型。
- 如果 RL 变好但 TDR 明显变差，不能判定为成功。
- 如果 TDR 变平滑但 RL 在 0-26.56GHz 内仍显著高于 -20dB，应继续给出风险而不是强行通过。
- 若 `human_confirm_each_iteration=true`，每轮修改前必须输出 action plan，等待工程师确认。

## LLM Role

LLM 不直接自由改 AEDT 模型。LLM 的职责是：

- 根据用户自然语言补齐优化目标和约束。
- 在已知 net、stackup、port、TDR/S 参数指标基础上解释问题。
- 在有限 action schema 中选择下一步动作。
- 生成工程师能审查的理由和风险说明。

实际模型修改必须通过节点和脚本执行，并留下 action record。这样可以避免 LLM 生成不可追踪的 AEDT 操作。

## Staged Delivery

### Stage C.4: Optimization Spec and Offline Scoring

先实现 S 参数/TDR 解析、指标计算、报告。输入可以是已有 Touchstone/TDR，不要求重新跑 AEDT。

验收：

- 能读取 `.sNp` 和 TDR CSV。
- 能计算 0-26.56GHz 内 worst RL。
- 能标记 RL 是否达到 -20dB。
- 能输出中文诊断报告。

Stage C.4 command line entry points:

```bash
.venv/bin/python scripts/score_stage_c_channel.py \
  --touchstone D:/runs/case.s2p \
  --tdr D:/runs/case_tdr.csv \
  --output-json D:/runs/channel_score.json \
  --output-html D:/runs/channel_score.html

.venv/bin/python scripts/compare_stage_c_channel.py \
  --before D:/runs/before_score.json \
  --after D:/runs/after_score.json \
  --output D:/runs/channel_compare.json
```

### Stage C.5: Single-Iteration Real AEDT Optimization

接入用户提供的“导入到仿真脚本”和“某层挖空脚本”。只做单轮：建模、仿真、评分、提出一个挖空调整、执行一次、再仿真、对比。

录制脚本处理原则：

- 录制脚本是事实来源，用来提取真实 AEDT 操作顺序、对象名、变量和报告配置。
- 产品化节点优先使用 PyAEDT/PyEDB 包装 API，例如 `Hfss3dLayout.create_ports_on_component_by_nets()`、`Hfss3dLayout.create_edge_port()`、`Hfss3dLayout.create_setup()`、`Hfss3dLayout.create_linear_step_sweep()`、`Hfss3dLayout.analyze()`。
- 没有稳定包装的操作才保留为 raw AEDT fallback，并且必须隔离在 action schema 后面，例如 `CreateCircleVoid`、`CreateRectangleVoid`、`SetDiffPairs`。

Recorded workflow bridge command:

```bash
.venv/bin/python scripts/analyze_stage_c_recorded_workflow.py \
  --source /path/to/recoard_workflow.py \
  --output-json D:/runs/recorded_workflow_analysis.json \
  --output-html D:/runs/recorded_workflow_analysis.html
```

验收：

- 每轮都有 before/after AEDT project。
- 每轮都有 before/after S 参数和 TDR。
- 报告能说明改了哪一层、为什么改、指标是否改善。

### Stage C.6: Controlled Multi-Iteration Loop

在单轮稳定后做最多 3 轮闭环。默认每轮人工确认，后续可增加自动模式。

验收：

- 最多 3 轮。
- 达到目标或无改善时提前停止。
- 每轮可回退。
- 最终输出优化历史和工程结论。

## Required User Inputs Later

后续实现前需要你提供：

- 从 BRD 导入到可仿真的完整脚本。
- 修改某一层挖空的脚本。
- 一组真实 before/after Touchstone 和 TDR 样例。
- 机械孔完成孔径与外圈孔径的具体换算规则或示例。
- backdrill 数据在 AEDT/EDB 中的读取位置或录制脚本。
- 成功案例和失败案例各至少一组，用于验证 scoring 和 diagnosis。

## Risks

- 高频板端口和 component/pin 规则强依赖具体封装，必须逐板积累规则。
- TDR 异常到物理层位置的映射可能需要 stackup、传播速度和端口参考面信息，不能只靠 LLM 猜测。
- 单纯优化 RL 可能牺牲 TDR 平滑性，因此 scoring 必须同时看频域和时域。
- 大板全仿真耗时高，优化 loop 需要显式预算、超时和恢复机制。
- 如果导入后的孔、pad、anti-pad 对象命名不稳定，需要先做对象识别和 action target 归一化。

## Open Decisions

1. 第一版优化变量是只允许调整 anti-pad/void 尺寸，还是也允许增减 return via？
2. TDR 平滑的量化阈值用最大偏差、局部斜率，还是两者结合？
3. 26.56GHz 是否作为默认 stop frequency，67GHz 是否只在用户明确要求 VNA 宽频评估时启用？
4. 每轮人工确认是否作为生产默认强制项？
5. 报告里是否需要自动生成“可交给 layout 工程师修改”的变更表？
