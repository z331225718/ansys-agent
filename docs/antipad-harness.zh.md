# 反焊盘 Harness 使用说明

本项目把 3D Layout 和 HFSS 3D 的反焊盘分成两个独立的严格写入能力。两者都要求先 preview、再由外部宿主审批、最后 apply；成功只修改当前 AEDT 内存工程，不自动保存。

## 1. HFSS 3D Layout：circle void

Workflow：`layout_live_antipad_circle_create`

原子工具：

- `preview_live_layout_antipad_circle_create`
- `apply_live_layout_antipad_circle_create`

示例请求：

```json
{
  "voids": [
    {
      "name": "AP_GND_U1_1",
      "owner_name": "GND_PLANE",
      "center": [12.5, 8.2],
      "radius": 0.45
    }
  ],
  "max_voids": 16
}
```

`center` 和 `radius` 使用当前 Layout 的 `model_units`。调用方不提供 layer；Harness 从 owner 的原生 `PlacementLayer` 读取并锁定层名，避免 AEDT 在传入错误 layer 时仍静默创建 void。

当前严格边界：

- 一批 1～32 个圆形 void；
- owner 必须是精确名称、精确大小写的非负片 signal-layer rectangle，或不含圆弧的直边 polygon；
- 整个圆必须落在 owner 外轮廓内；
- 名称不得与既有对象重复；
- apply 后同时核验 Type、Name、PlacementLayer、Center、Radius，以及 `GetPolygonVoids(owner)` 中唯一的 owner membership；
- 失败时只删除本批新增的 circle void，并要求 owner、既有 void 清单和目标缺席状态恢复到 preview 快照。

该能力暂不支持椭圆、长圆、矩形、带圆弧 polygon、跨 owner 的 void，或在负片层上推断相反语义。这些形状需要单独的 schema 和真实 AEDT 验收。

## 2. HFSS 3D：圆柱工具体减金属

Workflow：`hfss_live_antipad_subtract`

原子工具：

- `preview_live_hfss_antipad_subtract`
- `apply_live_hfss_antipad_subtract`

示例请求：

```json
{
  "blank_object_name": "L2_GND",
  "center": [12.5, 8.2],
  "radius": 0.45,
  "tool_name": "__AP_L2_U1_1"
}
```

`center` 和 `radius` 使用当前 HFSS 模型单位。`tool_name` 可省略，此时 Harness 根据目标和圆参数生成事务内临时名称。调用方不提供 Z 起点、高度或层厚；Harness 从目标金属的 bounding box 读取 Z 范围，在上下各增加受控 overshoot，创建 Z 轴真圆柱并执行 `subtract(..., keep_originals=False)`。

当前严格边界：

- 每次只处理一个精确名称的既有 solid；
- 活动坐标系必须为 Global；
- 目标必须显式分配非 air/vacuum 材料，并且是 Z 法向薄层实体；
- 圆的 XY bounding box 必须落在目标 bounding box 内；
- 目标体积必须按 `pi * radius^2 * layer_thickness` 减少，借此证明工具体完整穿透该层且没有落入既有孔洞或越过金属边缘；
- 目标 object ID、名称、材料、Solve Inside 和 bounding box 必须保持不变，同时必须观察到新增的圆柱切割面；
- 全设计非目标几何、Boundary 和 Mesh Operation 快照必须保持不变；
- 临时工具体必须消失，工程不会自动保存。

若 subtract 或最终读回失败，Harness 对该次 subtract 调用 AEDT `odesign.Undo()`，确认原金属的对象 ID、体积、面、Boundary 和 Mesh Operation 恢复，再删除恢复出的临时工具体。任何一步不能精确恢复都会报告 rollback incomplete，不会把部分恢复伪装成成功。

## 3. 对话建议

推荐这样描述任务：

```text
先读取当前设计类型和几何清单。若是 3D Layout，在精确 owner GND_PLANE 上，
以 [12.5,8.2]mm 为圆心创建半径 0.45mm、名称 AP_GND_U1_1 的反焊盘；
若是 HFSS 3D，则从精确金属实体 L2_GND 挖同样的圆孔，临时工具名 __AP_L2_U1_1。
必须使用已注册的反焊盘 Workflow，先展示 preview，等待审批，不要自动保存。
```

不要让 Agent 自行猜 owner、金属对象名或坐标单位。目标不明确时应先查询 inventory 并让用户确认；Harness 不会把“附近最像的对象”当成目标。

## 4. 保存与撤销

apply 成功后只代表当前 AEDT 会话中的修改已经通过原生读回。需要持久化时再单独调用项目保存 Harness。用户也可以在保存前从 AEDT GUI 撤销或关闭工程不保存。
