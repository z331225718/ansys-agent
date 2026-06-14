# BRD Solve Evidence Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 BRD local-cut Mission 从 model-review 推进到 solve/extract/score 的 artifact-first 证据闭环，并确保密集 S 参数/TDR 不直接进入 LLM 上下文。

**Architecture:** 先建立确定性谱数据分析与受限窗口查询，再包装 recorded/offline Touchstone/TDR 评分 worker，最后接入 Graph EvidencePackage。真实 AEDT solve 作为可插拔 worker profile，不能成为本阶段测试和演示的硬依赖。

**Tech Stack:** Python 3.11+、dataclasses、csv、json、pathlib、pytest、现有 `aedt_agent.layout.channel_scoring`、`aedt_agent.agent` runtime 和 graph control records。

---

## 背景与基线

上一阶段已经完成 `GraphRunRecord`、`NodeRunRecord`、`ArtifactManifest`、`EvidencePackage`、`JobAttemptRecord`，并让 `mission run-graph` 写入可审计记录。

本阶段要解决的问题：

- 不能把 `0~67GHz / 0.05GHz` 的 S 参数点直接塞给 GLM。
- pass/fail 必须由确定性 evaluator 基于全精度数据计算，而不是由 LLM 判断。
- LLM 只看 bounded summary、异常窗口和受限 window query。
- Touchstone/TDR 原始文件只通过 artifact refs 进入 EvidencePackage。
- 演示优先支持 recorded/offline artifacts，避免真实 AEDT/license 不稳定阻塞 agent 闭环。

## 文件结构

- `src/aedt_agent/agent/evaluation/spectral.py`  
  新增谱数据摘要、极值保留下采样、受限窗口查询和 evidence payload 构建。

- `src/aedt_agent/agent/evaluation/__init__.py`  
  导出稳定 evaluation API。

- `tests/test_agent_spectral_evidence.py`  
  覆盖密集 S 参数摘要、窄带峰保留、window query 限制、raw artifact-only policy。

- `src/aedt_agent/agent/workers/brd_channel_score.py`  
  新增 recorded/offline BRD channel score worker，读取 Touchstone/TDR artifact，输出 bounded evidence summary。

- `src/aedt_agent/agent/workers/__init__.py`  
  导出 score worker capability。

- `tests/test_agent_brd_channel_score_worker.py`  
  覆盖 worker 成功评分、缺 artifact 失败、raw trace 不进入 summary。

- `docs/agent_templates/brd_local_cut_solve_evidence.yaml`  
  新增 model build -> score/evidence -> scorecard/approval 的模板。

- `tests/test_agent_brd_solve_evidence_graph.py`  
  覆盖 graph runner 能执行 build 后的 evidence worker，并产生 EvidencePackage。

---

## Task 1：谱数据 Evidence 摘要与窗口查询

**Files:**
- Create: `tests/test_agent_spectral_evidence.py`
- Create: `src/aedt_agent/agent/evaluation/spectral.py`
- Modify: `src/aedt_agent/agent/evaluation/__init__.py`

- [ ] **Step 1：编写失败测试**

Create `tests/test_agent_spectral_evidence.py`:

```python
from __future__ import annotations

from aedt_agent.agent.evaluation import build_sparameter_evidence, query_sparameter_window


def _dense_samples():
    samples = []
    for index in range(1341):
        frequency = round(index * 0.05, 2)
        s11 = -22.0
        if frequency == 18.0:
            s11 = -7.0
        samples.append({"frequency_ghz": frequency, "s11_db": s11, "s21_db": -1.0})
    return samples


def test_build_sparameter_evidence_keeps_raw_trace_as_artifact_only():
    evidence = build_sparameter_evidence(
        trace_id="run-1:S11",
        samples=_dense_samples(),
        artifact_ref="artifacts/channel.s2p",
        rl_target_db=-20.0,
        bucket_count=64,
    )

    assert evidence["raw_trace_policy"] == "artifact_only"
    assert evidence["artifact_refs"] == ["artifacts/channel.s2p"]
    assert evidence["summary"]["sample_count"] == 1341
    assert evidence["summary"]["frequency_start_ghz"] == 0.0
    assert evidence["summary"]["frequency_stop_ghz"] == 67.0
    assert evidence["summary"]["rl_worst_db"] == -7.0
    assert evidence["summary"]["rl_worst_frequency_ghz"] == 18.0
    assert len(evidence["summary"]["buckets"]) <= 64
    assert "1341" in str(evidence["summary"])
    assert "0.0,0.05,0.1" not in str(evidence["summary"])


def test_extrema_preserving_buckets_keep_narrowband_failure():
    evidence = build_sparameter_evidence(
        trace_id="run-1:S11",
        samples=_dense_samples(),
        artifact_ref="artifacts/channel.s2p",
        rl_target_db=-20.0,
        bucket_count=32,
    )

    bucket = next(item for item in evidence["summary"]["buckets"] if item["frequency_start_ghz"] <= 18.0 <= item["frequency_stop_ghz"])

    assert bucket["max_db"] == -7.0
    assert bucket["max_frequency_ghz"] == 18.0
    assert bucket["threshold_crossings"] >= 1
    assert evidence["summary"]["failure_windows"] == [{"start_ghz": 18.0, "stop_ghz": 18.0, "worst_db": -7.0}]


def test_query_sparameter_window_limits_points_and_preserves_extrema():
    result = query_sparameter_window(
        trace_id="run-1:S11",
        samples=_dense_samples(),
        frequency_start_ghz=17.0,
        frequency_stop_ghz=19.0,
        max_points=8,
        rl_target_db=-20.0,
    )

    assert result["trace_id"] == "run-1:S11"
    assert result["point_count"] <= 8
    assert result["window_summary"]["sample_count"] == 41
    assert result["window_summary"]["rl_worst_db"] == -7.0
    assert any(point["frequency_ghz"] == 18.0 and point["s11_db"] == -7.0 for point in result["points"])
```

- [ ] **Step 2：运行测试确认失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_spectral_evidence.py -q
```

Expected: FAIL because `build_sparameter_evidence` and `query_sparameter_window` do not exist.

- [ ] **Step 3：实现最小谱分析器**

Create `src/aedt_agent/agent/evaluation/spectral.py`:

- `build_sparameter_evidence(trace_id, samples, artifact_ref, rl_target_db, bucket_count=128)`
- `query_sparameter_window(trace_id, samples, frequency_start_ghz, frequency_stop_ghz, max_points=128, rl_target_db=-20.0)`

Rules:

- sort samples by `frequency_ghz`;
- full-resolution worst RL is computed before compression;
- raw samples never appear in returned summary;
- buckets preserve min/max value and frequency, mean, first, last, threshold crossings;
- failure windows are contiguous regions where `s11_db > rl_target_db`;
- window query filters full-resolution samples then returns extrema-preserving points capped by `max_points`.

- [ ] **Step 4：运行测试确认通过**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_spectral_evidence.py -q
```

Expected: PASS。

- [ ] **Step 5：提交谱证据切片**

Run:

```powershell
git add src/aedt_agent/agent/evaluation/spectral.py src/aedt_agent/agent/evaluation/__init__.py tests/test_agent_spectral_evidence.py
git commit -m "feat: summarize dense sparameter evidence"
```

---

## Task 2：BRD recorded/offline channel score worker

**Files:**
- Create: `src/aedt_agent/agent/workers/brd_channel_score.py`
- Modify: `src/aedt_agent/agent/workers/__init__.py`
- Create: `tests/test_agent_brd_channel_score_worker.py`

- [ ] **Step 1：编写失败测试**

Create tests for:

- worker reads `touchstone_path` and `tdr_path`;
- output contains deterministic score, spectral evidence, TDR summary and artifact refs;
- output `evidence_summary.raw_sparameters == "artifact_only"`;
- missing files raise `ValueError`;
- dense traces produce bounded summary, not raw arrays.

- [ ] **Step 2：实现 worker**

Capability:

```python
BRD_CHANNEL_SCORE_CAPABILITY = "brd.channel.score"
```

Worker input:

```json
{
  "touchstone_path": "...",
  "tdr_path": "...",
  "artifact_dir": "...",
  "frequency_start_ghz": 0.0,
  "frequency_stop_ghz": 67.0,
  "rl_target_db": -20.0,
  "tdr_target_ohm": 100.0
}
```

Worker output:

- `status`: `passed` or `failed`;
- `score`: result from deterministic evaluator;
- `sparameter_evidence`: output from Task 1;
- `evidence_summary`: bounded summary only;
- `artifact_refs`: original Touchstone/TDR plus generated JSON evidence artifact.

- [ ] **Step 3：运行测试并提交**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_brd_channel_score_worker.py tests\test_agent_spectral_evidence.py -q
```

Commit:

```powershell
git add src/aedt_agent/agent/workers/brd_channel_score.py src/aedt_agent/agent/workers/__init__.py tests/test_agent_brd_channel_score_worker.py
git commit -m "feat: score brd channel evidence worker"
```

---

## Task 3：Solve Evidence Graph 模板与 CLI mission 创建

**Files:**
- Create: `docs/agent_templates/brd_local_cut_solve_evidence.yaml`
- Modify: `src/aedt_agent/agent/cli.py`
- Create: `tests/test_agent_brd_solve_evidence_graph.py`

- [ ] **Step 1：编写失败测试**

Test:

- CLI can create a mission with `--brd-channel-score --touchstone --tdr`;
- generated queued job capability is `brd.channel.score`;
- `mission plan --template brd_local_cut_solve_evidence` prints worker nodes;
- `mission run-graph` executes score worker and leaves GraphRun/NodeRun/EvidencePackage.

- [ ] **Step 2：实现模板和 CLI**

Template includes:

```text
planner -> input_validator -> channel_score_worker -> channel_scorecard -> approval_gate
```

CLI adds:

```text
mission create --brd-channel-score --touchstone <path> --tdr <path> --artifact-dir <dir> --frequency-stop-ghz 67 --rl-target-db -20
```

- [ ] **Step 3：运行测试并提交**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_brd_solve_evidence_graph.py tests\test_agent_cli_graph_control.py -q
```

Commit:

```powershell
git add docs/agent_templates/brd_local_cut_solve_evidence.yaml src/aedt_agent/agent/cli.py tests/test_agent_brd_solve_evidence_graph.py
git commit -m "feat: run brd solve evidence graph"
```

---

## Task 4：阶段回归与下一阶段入口

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_agent_spectral_evidence.py `
  tests\test_agent_brd_channel_score_worker.py `
  tests\test_agent_brd_solve_evidence_graph.py `
  tests\test_agent_cli_graph_control.py `
  tests\test_agent_graph_runner_dag.py `
  tests\test_agent_scorecard.py `
  tests\test_architecture_dependencies.py -q
```

Then:

```powershell
rg -n "aedt_agent\.v0" src\aedt_agent\agent src\aedt_agent\infrastructure
git diff --check
git status --short
```

Expected:

- focused tests pass;
- agent/infrastructure still do not depend on v0;
- raw S 参数/TDR files appear only as artifact refs;
- no unrelated dirty files are modified.

---

## 完成定义

1. Dense S 参数有 full-resolution deterministic summary。
2. Extrema-preserving buckets 保留窄带尖峰和阈值穿越。
3. `query_sparameter_window` 有 max_points 限制并保留窗口极值。
4. Recorded/offline channel score worker 输出 bounded EvidencePackage-ready payload。
5. 原始 Touchstone/TDR 不进入 LLM summary，只进入 artifact refs。
6. 新 graph 模板可以执行 score worker 并留下 GraphRun/NodeRun/EvidencePackage。
7. 阶段测试通过，架构边界不依赖 `aedt_agent.v0`。

## 后续计划

下一阶段是 `Controlled BRD Action Schema`：只允许一个 void/anti-pad 调整动作族，执行前 approval，执行后 before/after comparison，退化则 rollback。Pi / 外部 agent framework 继续等 native runtime 完整闭环后评估。
