# Dipole Resonance Tuning Demo Design

## Goal

把 Stage C 偶极子演示从“一次性参数化并运行”升级为“仿真结果驱动的闭环调参”：用户给出目标频率后，agent 先生成初始几何，运行仿真，读取 S11 曲线，判断谐振点，再调整单臂长度，最多迭代 3 轮。

## Scope

本次只做 demo 级能力，覆盖 `dipole_antenna_s11_farfield`。调参变量只开放 `dipole_arm_length_mm`，评价指标只用 S11 最低点对应的频率。微带线 demo、已有 workflow template、真实 AEDT 启动路径保持兼容。

## Architecture

新增 `aedt_agent.demo.tuning` 作为闭环调参核心。它接收目标频率、当前参数和 S 参数曲线，输出每一轮的谐振点、误差、下一轮长度和 agent 解释。`DemoService` 暴露 `/api/tune-dipole`，Web 页面增加 “Tune Resonance” 入口和回合展示。

真实 AEDT 链路后续可以复用同一回合协议逐轮运行。为了当前演示稳定，fake adapter 路径用可解释的合成 S11 曲线模拟“几何长度影响谐振频率”，验证 UI 和闭环逻辑；真实 AEDT 单次运行仍保持原路径。

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
- Service 测试：`tune_dipole` 返回最多 3 轮，最后误差进入阈值，并包含每轮参数和解释。
- Web 测试：页面和 dispatch API 暴露 Tune Resonance。
