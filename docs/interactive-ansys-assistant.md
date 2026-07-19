# 通用交互式 Ansys 助手

`ansys-assistant` 是现有工程 Agent 的加法式交互入口。它既面向临时查询和受控模型修改，
也通过 guarded graph tools 复用原有 YAML Workflow、worker 和 mission 状态机，而不改变其执行语义。

当前稳定纵向切片支持 HFSS 3D Layout：

- 打开 `.aedt` 加同名 `.aedb` sidecar，或直接打开 `.aedb`。
- 枚举 Path/trace，并按线宽、net、layer、primitive id 筛选。
- 预览把一组 Path 的宽度绑定到 design parameter。
- 仅在自动创建的工作副本中应用修改。
- 回读表达式和 primitive 参数化状态，失败时回滚。

新增 live control plane 还能受控启动 AEDT，或发现并显式连接正在运行的 AEDT，会话内复用
PyAEDT broker，读取工程信息、HFSS geometry/setup/port/boundary/report inventory、受控创建
有序 design/project variable 原子批量事务、typed geometry batch、为显式 solid batch 分配已有工程材料、创建 setup、radiation boundary、typed Wave/Lumped Port
和 report、创建 Perfect E/Perfect H/Finite Conductivity/sheet Impedance/Lumped RLC 表面边界、受控 Length Based Mesh
与有界 Infinite Sphere 远场设置、驱动 analysis，
并能在单一事务中原子创建新几何和 Boundary/Port，或原子创建 Setup 和 Sweep，同时查询 live 3D Layout Path。
在 Desktop-bound strict 会话和推荐的生产链路中，live edit、setup/boundary/report、solve/cancel/export
与 project save 都采用 preview/apply 两阶段操作，并使用外部 Host 签发的短期批准令牌。通用 MCP
仍保留 `create_live_hfss_design` 和 `start_live_hfss_analysis` 兼容入口；它们不属于 Desktop 生产链路，
strict Desktop 会拒绝直接写入路径。

## 安装

CLI 使用项目基础依赖；MCP server 还需要 `mcp` extra：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[mcp]"
```

也可以不安装 console script，直接使用模块入口：

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m aedt_agent.interactive capabilities
```

## CLI

列出 Agent 可见的机器可读能力和 schema：

```powershell
ansys-assistant capabilities
```

查看兼容的统一 live/artifact v2 catalog：

```powershell
ansys-assistant capabilities-v2
```

发现运行中的 AEDT，不建立连接：

```powershell
ansys-assistant live-sessions
```

启动一个非图形 AEDT gRPC 会话；CLI 释放 wrapper，但 AEDT 继续运行：

```powershell
ansys-assistant live-launch --aedt-version 2026.1 --non-graphical
```

显式连接一个 PID，读取工程信息后释放 wrapper；不会关闭 AEDT 或工程：

```powershell
ansys-assistant live-info --pid 12345
```

查看所有 `0.1mm` 走线：

```powershell
ansys-assistant inspect-layout `
  --project C:\cases\board.aedt `
  --target-width 0.1mm `
  --tolerance 0.1um
```

只生成参数化预览，不应用：

```powershell
ansys-assistant parameterize-width `
  --project C:\cases\board.aedt `
  --target-width 0.1mm `
  --tolerance 0.1um `
  --variable-name trace_w
```

确认预览逻辑后应用到自动创建的工作副本：

```powershell
ansys-assistant parameterize-width `
  --project C:\cases\board.aedt `
  --target-width 0.1mm `
  --tolerance 0.1um `
  --net DDR_DQ0 `
  --layer L1 `
  --variable-name trace_w `
  --variable-value 0.1mm `
  --workspace C:\cases\assistant-runs `
  --apply
```

命令始终输出 JSON。成功结果中的 `working_project_path` 是应当在 AEDT 中打开和检查的
副本；源工程不会被覆盖。

## MCP

仓库提供 [`config/harness/interactive_ansys_assistant_mcp.json`](../config/harness/interactive_ansys_assistant_mcp.json)
示例。Server 暴露：

```text
list_ansys_capabilities
list_ansys_capabilities_v2
list_ansys_workflows
inspect_ansys_workflow
preview_ansys_workflow_start
apply_ansys_workflow_start
get_ansys_workflow_status
preview_ansys_workflow_advance
apply_ansys_workflow_advance
open_layout_session
list_layout_paths
preview_parameterize_path_width
apply_parameterize_path_width
close_layout_session
list_live_aedt_sessions
launch_live_aedt_session
attach_live_aedt_session
get_live_aedt_project_info
preview_live_project_save
apply_live_project_save
create_live_hfss_design
get_live_hfss_design_inventory
get_live_aedt_setup_inventory
get_live_hfss_geometry_inventory
get_live_hfss_material_inventory
preview_live_hfss_material_assign
apply_live_hfss_material_assign
get_live_hfss_mesh_inventory
preview_live_hfss_length_mesh_create
apply_live_hfss_length_mesh_create
get_live_hfss_far_field_inventory
preview_live_hfss_infinite_sphere_create
apply_live_hfss_infinite_sphere_create
get_live_hfss_surface_boundary_inventory
preview_live_hfss_surface_boundary_create
apply_live_hfss_surface_boundary_create
get_live_hfss_port_inventory
preview_live_hfss_geometry_create
apply_live_hfss_geometry_create
preview_live_hfss_geometry_boundary_create
apply_live_hfss_geometry_boundary_create
preview_live_hfss_setup_create
apply_live_hfss_setup_create
preview_live_hfss_setup_sweep_create
apply_live_hfss_setup_sweep_create
preview_live_hfss_setup_update
apply_live_hfss_setup_update
preview_live_frequency_sweep_create
apply_live_frequency_sweep_create
preview_live_hfss_boundary_create
apply_live_hfss_boundary_create
preview_live_hfss_report_create
apply_live_hfss_report_create
start_live_hfss_analysis
preview_live_hfss_analysis_start
apply_live_hfss_analysis_start
get_live_hfss_analysis_status
preview_live_hfss_analysis_cancel
apply_live_hfss_analysis_cancel
preview_live_hfss_results_export
apply_live_hfss_results_export
list_live_layout_paths
get_live_layout_routing_inventory
get_live_layout_object_inventory
get_live_layout_object_property_inventory
preview_live_layout_object_property_update
apply_live_layout_object_property_update
get_live_aedt_variable_inventory
preview_live_aedt_variable_upsert
apply_live_aedt_variable_upsert
preview_live_aedt_variable_batch_upsert
apply_live_aedt_variable_batch_upsert
preview_live_parameterize_path_width
apply_live_parameterize_path_width
wait_for_live_approval
release_live_aedt_session
get_ansys_operation_plan_schema
propose_ansys_operation
validate_ansys_operation
preview_exploratory_operation
apply_exploratory_operation
capture_capability_trace
promote_ansys_capability
```

独立只读 `ansys-api-memory` MCP 暴露 `get_ansys_api_memory_status`、`search_ansys_api`、
`inspect_ansys_symbol`、`trace_ansys_call`、`search_ansys_source` 和 `find_ansys_example`。
`inspect_ansys_symbol` 返回可直接复制到 operation plan 的 `operation_evidence`；Runtime 会重放
inspect 并核验全部证据字段，而不是只相信 Agent 提交的版本号。

MCP server 会把 PyEDB 会话放在独立 worker process 中运行，避免 PyEDB/gRPC 输出污染
stdio 协议。关闭 MCP server 时 worker 会随父进程退出。

推荐 Agent 调用顺序：

```text
open_layout_session(writable=true)
  -> list_layout_paths
  -> preview_parameterize_path_width
  -> 向用户展示命中对象和工作副本
  -> apply_parameterize_path_width
  -> 检查 verified_count 和 evidence
  -> close_layout_session
```

Live 修改的推荐顺序：

```text
list_live_aedt_sessions
  -> attach_live_aedt_session(pid|port)
  -> list_live_layout_paths
  -> preview_live_parameterize_path_width
  -> Host/UI 审阅 approval_request 并在 MCP 之外签发 token
  -> apply_live_parameterize_path_width
  -> 按需 preview_live_project_save + 独立批准 + apply_live_project_save
  -> release_live_aedt_session
```

多个 HFSS/3D Layout design/project variable 需要按依赖顺序一起创建或更新时，使用严格 Workflow
`aedt_live_variable_batch_upsert`，不要让 Agent 临时循环调用单变量 setter：

```text
get_live_aedt_variable_inventory
  -> preview_live_aedt_variable_batch_upsert（1～32 个有序 name/expression）
  -> 核对 create/update/noop、scope、before/after expression
  -> Host approval
  -> apply_live_aedt_variable_batch_upsert
  -> 逐项回读并核对 project_saved=false
```

preview 冻结当前设计类型、solution type 和完整变量表达式清单；无关变量变化也会 stale。已有变量通过
AEDT `ChangeProperty` 的 value-only 路径更新，以保留 Sweep、Description、ReadOnly 和 Hidden；新变量使用
PyAEDT 受测默认值。数值字面量允许 AEDT 规范化，例如 `3.0mm` 回读为 `3mm`。任一项失败时恢复已有值并
逆序删除新变量，随后比较完整清单。该事务已在隔离 AEDT 2026 R1 上同时实测 HFSS/3D Layout，并通过
“第一项创建成功、第二个 project variable 非法引用 design variable”的真实部分失败回滚；2024 R2 仍需目标机复验。

HFSS 建模写操作遵循同样边界。需要成对创建新 Setup 和 Sweep 时，使用原子接口，不要把两个独立写操作
临时串联：

```text
get_live_aedt_setup_inventory
  -> preview_live_hfss_setup_sweep_create
  -> Host approval
  -> apply_live_hfss_setup_sweep_create
  -> 核对 setup_inventory、atomic_setup_sweep_transaction 和 project_saved=false
```

为已有 solid 分配材料时，目标材料必须已经存在于当前工程 material catalog：

```text
get_live_hfss_material_inventory
  -> get_live_hfss_geometry_inventory
  -> preview_live_hfss_material_assign（冻结对象 ID、材料、Solve Inside 和材料定义）
  -> Host approval
  -> apply_live_hfss_material_assign
  -> 核对 verified_count、material_name、solve_inside 和 project_saved=false
```

为明确 solid batch 创建局部 Length Based Mesh 时：

```text
get_live_hfss_geometry_inventory
  -> get_live_hfss_mesh_inventory
  -> preview_live_hfss_length_mesh_create
  -> Host approval
  -> apply_live_hfss_length_mesh_create
  -> 核对 Type、Assignment、Region、Max Length、Max Elems 和 project_saved=false
```

创建 HFSS Infinite Sphere 远场设置时，设计必须已经有 Radiation、PML 或 free-standing hybrid
边界，且 solution type 不能是 EigenMode/CharacteristicMode：

```text
get_live_hfss_far_field_inventory
  -> 核对 creation_ready 和 radiated_field_sources
  -> preview_live_hfss_infinite_sphere_create
  -> Host approval
  -> apply_live_hfss_infinite_sphere_create
  -> 核对 definition、两条角度轴、polarization、sample_count 和 project_saved=false
```

Harness 支持 `Theta-Phi`、`El Over Az`、`Az Over El` 以及 `deg`/`rad` 数值输入，限制角度网格总样本数，
并在 apply 前冻结 Boundary 和全部 Field Setup。当前只允许 `Global` coordinate system，不接受自定义
radiation surface 或未验证的表达式输入。

创建常用 HFSS 表面边界时使用 typed 接口，不要把通用 radiation/port Harness 当作自由 Boundary API：

```text
get_live_hfss_geometry_inventory
  -> get_live_hfss_surface_boundary_inventory
  -> Finite Conductivity 时核对 get_live_hfss_material_inventory
  -> preview_live_hfss_surface_boundary_create
  -> Host approval
  -> apply_live_hfss_surface_boundary_create
  -> 核对 kind、assignment、typed options 和 project_saved=false
```

支持 Perfect E、Perfect H、Finite Conductivity、sheet Impedance 和 sheet Lumped RLC。对象名与 face ID 必须二选一；
Impedance 只接受 sheet object；Infinite Ground 只接受可确认的 planar sheet/face；Finite Conductivity
只能引用当前工程已经存在的材料，厚度和粗糙度必须显式带单位。preview 会冻结 solution type、目标几何、
全部现有 Boundary 和材料定义，任一状态变化都会触发 stale 拒绝。

Lumped RLC 只接受一个 planar sheet。`rlc_type` 为 `Parallel` 或 `Serial`；integration line 方向限定为六个
全局轴正负方向，preview 会把方向解析成带当前模型单位的 Start/End 三维点。R/L/C 分别按 Ω、H、F 接收正有限
数值，至少启用一项；apply 后同时回读启用位、单位化数值和 integration line。

已有 HFSS 几何上的 Wave/Lumped Port 使用独立严格 Workflow `hfss_live_port_create`：

```text
get_live_hfss_geometry_inventory
  -> get_live_hfss_port_inventory
  -> Wave Port 使用一个 planar face ID；Lumped Port 使用一个 planar sheet 名称
  -> preview_live_hfss_boundary_create
  -> 核对 preview 解析的实际 integration line
  -> Host approval
  -> apply_live_hfss_boundary_create
  -> 回读 type、assignment、mode、CharImp、renormalize、deembed/impedance 和 integration line
```

当前 typed Port Harness 只支持 DrivenModal。Wave Port 接受 `modes=1..16`、布尔 renormalize、毫米制
deembed、六个全局轴方向和 `Zpi/Zpv/Zvi/Zwave`；Lumped Port 接受正有限欧姆值、布尔 deembed 和六个轴方向。
Wave Port 的普通 `impedance` 参数在 DrivenModal 中没有稳定属性回读，因此严格路径不接受；terminal reference
也留给独立的后续 Harness。preview 会冻结 solution type、model unit、完整 geometry 和 Boundary 属性，apply 前
任一变化都返回 stale。创建与回读失败会删除本次端口并核对旧 Boundary 快照，默认不保存工程。

`hfss_live_geometry_boundary_create` 也支持在同一原子批次中新建 rectangle sheet 和 Lumped Port。selector 必须先
证明该对象只有一个明确平面 face；apply 随后把 sheet 对象名传给 PyAEDT，而不是把 face ID 当作 lumped-port
geometry assignment。已有 sheet 则优先走上述独立 typed Port Workflow，以获得更完整的属性回读。

生产求解应使用批准链路，而不是兼容入口 `start_live_hfss_analysis`：

```text
preview_live_hfss_analysis_start(cores/tasks/gpus)
  -> Host approval
  -> apply_live_hfss_analysis_start
  -> get_live_hfss_analysis_status
  -> 可选 preview/apply_live_hfss_analysis_cancel
  -> preview/apply_live_hfss_results_export
```

3D Layout 的完整受控链路可直接使用组合 Workflow `layout_live_solve_export`，也可以分别使用
`layout_live_solve_start`、`layout_live_solve_monitor` 和 `layout_live_results_export`。monitor 通过有界 Graph
loop 每次只轮询一次；results export 支持
`product="layout"`，并在 scorecard 中重新核验 artifact 与 manifest。Workflow 的 start 和每个
advance step 都需要 Graph 审批；组合 Workflow 中，启动求解和写出结果还各自需要一次不能互换的 operation 审批。

批准后的求解固定非阻塞，资源预算有上限。结果导出只允许写入
`AEDT_AGENT_EXPORT_ROOT`（默认 `.aedt-agent/exports`）下的 server-managed 目录，支持
Touchstone 和 report CSV，并生成包含 artifact SHA-256 的 evidence manifest。

批准令牌绑定 `action + live_session_id + preview_id + snapshot_digest`，默认有效期 5 分钟且只能使用一次。
MCP 不暴露签发工具。部署时可用 `HmacApprovalAuthority` 在可信 Host 进程签发，并通过
`AEDT_AGENT_APPROVAL_SECRET` 让 MCP server 验证；密钥至少 32 字节。

从 AEDT Automation Tab 启动时使用独立 loopback approval Host。preview 会触发原生确认框；
Claude 只能通过 `wait_for_live_approval` 获取用户批准后的 token，Host 不提供 approve API。

可信 Host 在展示并确认 preview 后签发，不要把这段逻辑注册成 MCP tool：

```python
from aedt_agent.live import HmacApprovalAuthority

authority = HmacApprovalAuthority(host_secret)
token = authority.issue(**preview["approval_request"])
```

不要跳过 preview，也不要把 `apply` 的成功返回当作充分证据。只有 `status=verified`、
`verified_count=target_count`、`variable_is_parameter=true` 且 `source_unchanged=true` 时，
才应向用户报告完成。

## 当前边界

- 写操作暂不支持覆盖源工程。
- 只读查询也打开临时快照副本，关闭会话后自动清理，避免 EDB lock/tmp 文件触碰源目录。
- `.aedt` 输入必须存在同名 `.aedb` sidecar。
- Live HFSS/3D Layout 当前支持 routing/object/variable/setup inventory、有序变量原子批量事务、HFSS typed surface boundary、solid 材料批量分配、Length Based Mesh 和 Infinite Sphere 远场设置、受控变量和对象属性更新、setup/sweep、
  radiation/wave/lumped port、report 创建、批准式 analysis start/cancel/status、Layout 有界求解监控、
  HFSS/Layout 受限结果导出和受控 project save。
- `create_live_hfss_design` 与 `start_live_hfss_analysis` 仅为通用 MCP 兼容入口；Desktop strict 模式禁用直接写入，生产求解使用批准链路。
- `launch_live_aedt_session` 返回 `owned_by_assistant=true`；release 仍不关闭 AEDT。
- Live apply/save 在未配置 Host approval verifier 时固定返回 `approval_required`。
- 自由 Python、shell 和 COM fallback 始终关闭。只有 API Memory ready 且 Exploration policy
  enabled 时，Router 才允许声明式 `code_fallback`；已有 Workflow/Harness 始终优先。
