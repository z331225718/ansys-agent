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

当前仍是 model-build only：默认不运行 analyze，不承诺 S 参数/TDR solve 结果。
