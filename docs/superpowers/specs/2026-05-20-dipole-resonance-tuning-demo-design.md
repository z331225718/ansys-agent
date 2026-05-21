# Dipole Resonance Tuning Demo Design

## Goal

把 Stage C 偶极子演示从“一次性参数化并运行”升级为“仿真结果驱动的闭环调参”：用户给出目标频率后，agent 先生成初始几何，运行仿真，读取 S11 曲线，判断谐振点，再调整单臂长度，最多迭代 3 轮。

## Scope

本次只做 demo 级能力，覆盖 `dipole_antenna_s11_farfield`。调参变量只开放 `dipole_arm_length_mm`，评价指标只用 S11 最低点对应的频率。微带线 demo、已有 workflow template、真实 AEDT 启动路径保持兼容。

## Architecture

新增 `aedt_agent.demo.tuning` 作为闭环调参核心。它接收目标频率、当前参数和 S 参数曲线，输出每一轮的谐振点、误差、下一轮长度和 agent 解释。`DemoService` 暴露 `/api/agent-run`，由后端根据用户自然语言判断是单次 workflow 还是 `dipole_tuning` 多轮 workflow。

浏览器主路径不提供单独“调试按钮”。用户只输入需求并点击 `Run Real AEDT`；后端 agent decision 会选择执行模式。普通微带线或普通偶极子请求运行一次真实 AEDT workflow；表达“工作在目标频率、谐振点落在目标频率、调试、调整、优化”等意图的偶极子请求启动真实 AEDT 多轮 tuning job。

测试路径仍允许 fake adapter 快速验证状态机和 UI，但展示路径默认走真实 AEDT。每一轮真实 tuning 都调用 `scripts/run_stage_c_real_workflow_smoke.py`，读取 Touchstone S11 曲线，定位最低点作为谐振频率，再让 LLM advisor 或工程规则给出下一轮 `dipole_arm_length_mm`。

## Tuning Rule

偶极子谐振频率与有效长度近似成反比。若当前谐振频率低于目标频率，说明天线偏长，下一轮缩短单臂；若当前谐振频率高于目标频率，说明天线偏短，下一轮加长单臂。

更新公式：

```text
next_length = current_length * current_resonance_frequency / target_frequency
```

为避免 demo 中过度跳变，每轮调整比例限制在 0.80 到 1.20 之间。若误差进入默认 2% 内，提前停止。

## Test Plan

- 单元测试：从 S11 曲线找最低点作为谐振点。
- 单元测试：目标 2.5GHz、当前谐振 2.3GHz 时缩短长度。
- Service 测试：`start_agent_run` 能把偶极子调试请求判为 `dipole_tuning`，普通请求判为 `single_workflow`。
- Web 测试：页面不暴露调试按钮，统一通过 `/api/agent-run` 启动。
