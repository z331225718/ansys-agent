# BRD/MCM Experimental Workflow

## 定位

BRD/MCM workflow 是 Stage C 的 experimental track，不属于默认 HFSS core catalog。它用于高频板级场景：导入 Cadence BRD/MCM，解析 net，执行 PyEDB cutout，导入 stackup，定位端口候选，创建 3D Layout port 和 setup。

## 当前节点边界

| 节点 | 当前职责 | 稳定性 |
| --- | --- | --- |
| import_layout_file | 打开 BRD/MCM 或 EDB，读取可用 net inventory | experimental |
| select_layout_nets | 将用户输入或 wildcard 解析为实际 signal/reference nets | experimental |
| create_layout_cutout | 调用 PyEDB cutout，保留 signal/reference nets 和 cutout artifact | experimental |
| configure_layout_stackup | 在 HFSS 3D Layout 中导入 stackup XML | experimental |
| locate_layout_port_candidates | 根据组件、pin、net、位置生成端口候选报告 | experimental |
| create_layout_ports | 根据候选端点创建 component cylinder port 或 ToggleViaPin Gap port | experimental |
| create_layout_setup | 创建 DC-to-67GHz 宽频 setup/sweep，默认不 analyze | experimental |

## 当前验收

- model-build only：默认不运行 analyze。
- 输出 artifact 包括 cutout summary、port candidate report、port action plan、AEDT project path。
- 端口规则来自本机 AEDT 录制脚本和人工检查，不声明跨板通用稳定。

## 已知限制

- 不同 BRD 的 component naming、pin naming、stackup layer naming 可能需要板级规则。
- 端口候选选择仍是启发式，复杂拓扑如串联电容、AC coupling、connector breakout 需要后续规则。
- TDR/S 参数后处理需要 solve，当前工作流默认跳过。

## Artifact 约定

BRD/MCM experimental workflow 同时保留两个 artifact：

- `import_cutout_summary.json`：板级 model-build 的原始执行摘要，包含 PyEDB cutout、stackup、端口、setup、AEDT project path。
- `workflow_run.json`：转换后的统一 workflow artifact，包含标准 `workflow_id`、`status`、`steps`、`outputs`，供 demo UI、报告和后续 repair/evolution 使用。

当前仍是 model-build only / model-build-only：默认不 analyze，默认不运行 analyze，不承诺 S 参数/TDR solve 结果。

## Production acceptance artifacts

生产验收运行会把一次 BRD/MCM model-build 打包成可复盘目录。核心 artifact：

- `preflight.json`：运行前环境预检结果，包括 PyAEDT、AEDT root、Cadence/CDSROOT、layout 文件和 stackup 文件。
- `params.json`：本次用户输入和运行参数快照。
- `workflow_run.json`：统一 workflow 节点状态。
- `import_cutout_summary.json`：PyEDB/HFSS 3D Layout 原始执行摘要。
- `acceptance_report.json`：生产验收结构化摘要。
- `acceptance_report.html`：中文生产验收报告，面向工程 review 和失败复盘。
- `stdout.log` / `stderr.log`：真实运行日志。

生产验收入口：

```bash
.venv/bin/python scripts/run_stage_c_brd_acceptance.py \
  --adapter real \
  --params D:/boards/stage_c_brd_params.json \
  --run-dir D:/aedt-agent-runs/brd_case_001 \
  --config config/demo_config.example.json \
  --local-config config/demo_config.local.json
```

对已有 run directory 离线补报告：

```bash
.venv/bin/python scripts/package_stage_c_brd_run.py --run-dir D:/aedt-agent-runs/brd_case_001
```

## Live progress contract

BRD/MCM demo runs write `workflow_run.json` while the model-build job is still running. The page should treat this file as the single source of truth for node status.

Canonical step ids:

- `import_layout_file`
- `select_layout_nets`
- `create_layout_cutout`
- `configure_layout_stackup`
- `locate_layout_port_candidates`
- `create_layout_ports`
- `create_layout_setup`
- `validate_layout_model`

The BRD/MCM path remains `experimental` and `model-build-only` by default. Heavy board analyze is intentionally skipped unless a future explicit run mode enables it.
