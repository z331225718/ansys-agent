# Stage C.5 Local Cut Optimization Cell Design

## Goal

把 Stage C.5 从“整条 channel 建模和优化”修正为“局部 cut cell 优化”。真实高频过孔优化不应默认整板或整条 channel 反复仿真，而是由工程师指定一个局部 bbox，只切出目标 BGA/过孔和一段均匀线，在这个局部模型中建端口、仿真、调整 anti-pad/void。

## Core Decision

第一版 `local_cut_region` 只支持用户给定 bbox 坐标。LLM 不自动猜 bbox。

原因：

- cut box 是强工程经验输入，位置、余量、是否包含均匀线会直接影响端口和 TDR。
- LLM 可以解释用户输入、检查字段完整性、把 bbox 写入 workflow，但不能把猜测结果直接用于生产模型。
- 后续可以增加 LLM 建议 bbox，但必须经过用户确认。

## Workflow

1. 从 BRD/MCM 打开 EDB。
2. 根据用户指定 signal nets、reference nets 和 `local_cut_region` 执行局部 cutout。
3. 局部模型只保留目标 BGA/过孔、一段可作为端口参考的均匀线、必要参考平面和回流结构。
4. 导入 stackup。
5. 在 BGA 端建立 component/ball/bump port。
6. 在均匀线端建立 edge/circuit port。
7. 应用录制脚本中的 HFSS extents、DesignOptions、setup、sweep 设置。
8. build-only 保存工程，先由工程师检查。
9. 模型确认后再进入局部 solve、S 参数/TDR 评分、void/anti-pad 调整。

## Input Contract

第一版输入：

```json
{
  "layout_file": "D:/boards/case.brd",
  "stackup_xml": "D:/boards/stackup.xml",
  "target_component": "U1",
  "signal_nets": ["SRDS_0_RX0_N", "SRDS_0_RX0_P"],
  "reference_nets": ["GND"],
  "local_cut_region": {
    "type": "bbox",
    "unit": "mil",
    "x_min": 5400.0,
    "y_min": 1100.0,
    "x_max": 6200.0,
    "y_max": 1500.0
  },
  "uniform_line_port_hint": {
    "side": "right",
    "layer": "ART03",
    "port_type": "edge"
  },
  "solve_enabled": false
}
```

Rules:

- `local_cut_region.type` must be `bbox`.
- `unit` must be explicit.
- The runner must reject missing or inverted bbox values.
- The runner must record the bbox in every summary and action record.
- LLM may not synthesize bbox values in production mode.
- If the uniform-line port cannot be found near the bbox boundary, the runner must stop and report candidate traces/edges instead of silently creating a wrong port.

## Port Strategy

The local cell has two endpoint classes:

| Endpoint | Strategy | Notes |
| --- | --- | --- |
| BGA/component side | component ball/bump port | Reuse current U1 flip-chip/solderball flow. |
| Uniform line side | edge/circuit port near bbox boundary | Find target net trace edges close to the specified bbox side and layer. |

Uniform-line port creation is the main new difficulty. It must be an explicit node, not hidden inside generic port creation.

The first version should:

- Filter layout primitives by target net and optional layer.
- Prefer edges crossing or closest to the requested bbox side.
- Emit edge candidates with primitive name, edge id, distance to bbox side, net, layer, and point.
- Create ports only when the best candidate is unambiguous.
- Otherwise stop and ask for a user hint.

## Local Cutout Semantics

Existing PyEDB cutout by nets and expansion is not enough for optimization. Stage C.5 local cutout needs a bbox constraint.

Accepted behavior for first implementation:

- Use PyEDB where it can honor a polygon/bbox extent.
- If a direct bbox API is unavailable, convert bbox to an extent polygon and use the closest supported PyEDB cutout method.
- Preserve the bbox and generated polygon in `stage_c5_local_cut_summary.json`.
- Do not fall back to whole-channel cutout when bbox cutout fails.

## Optimization Scope

The optimization loop only acts inside the local cut cell:

- Adjustable objects: local anti-pad/void/plane cutout around target BGA/过孔.
- Fixed objects: stackup, BGA ball geometry, backdrill process constraints, bbox, selected nets.
- Each iteration writes before/after project references and an action record.
- The first implementation remains build-only unless `solve_enabled=true` is explicitly set after model inspection.

## LLM Role

LLM responsibilities:

- Parse user intent into known fields.
- Validate that bbox is present and explicit.
- Explain why bbox is required.
- Select from available nodes and action schemas.
- Summarize candidates and risks.

LLM must not:

- Invent bbox coordinates in production mode.
- Move the bbox during optimization without user confirmation.
- Create arbitrary AEDT scripts outside controlled nodes.

## Deliverables

Stage C.5 local cut implementation should produce:

- `stage_c5_local_cut_params.json`
- `stage_c5_local_cut_summary.json`
- `port_candidates.json` including uniform-line edge candidates
- build-only AEDT project
- action plan including bbox, BGA port action, uniform-line port action, and recorded solve settings

## Acceptance

Build-only acceptance:

- User-supplied bbox is present in summary.
- Cutout is local to the bbox and does not silently expand to the whole channel.
- BGA/component port is created or a clear failure is reported.
- Uniform-line edge port is created only when candidate selection is unambiguous.
- Recorded HFSS extents, DesignOptions, setup, sweep and curve approximation settings are carried into the project.
- Solve remains skipped by default.

Solve acceptance comes later:

- Local cell can solve within a practical runtime.
- S 参数/TDR are exported.
- Before/after void changes can be scored against local-cell metrics.

## Relationship To Existing Stage C.5 Work

Existing recorded-workflow analysis and build-only runner remain useful, but they must become supporting tools for local cut cells:

- recorded analysis provides solve settings and raw fallback facts.
- build-only runner provides environment setup and HFSS 3D Layout project creation.
- local cut cell adds the missing production constraint: user-defined bbox and uniform-line endpoint porting.

