# M1 生产端交接：T4 真实 AEDT 启动

- 分支：`feature/reviewed-brd-dsh-loop`
- 当前提交：`c2c59935`
- 离线状态：T0–T3 完成，1465 passed / 31 skipped
- 真实 AEDT：**未启动**
- DSH：单一 `Ansys 工程优化模式`，只做观察与工程操作；生产 Mission lifecycle 被 guard 拒绝

## 1. 生产端必须先提供

在 AEDT 机器上准备并确认：

1. 已人工审查、可求解的四端口差分 `.aedt` 源工程。
2. 源工程只读，working/run_root/report_dir 位于受控目录。
3. AEDT 2026.1、本机 license、仓库 venv 可用。
4. 实际 setup / sweep 名称（不要沿用示例猜测）。
5. Touchstone：`.s4p`、`expected_port_count=4`、`sparameter_mode=differential`。
6. SDD11 / SDD21 / TDRZ(Diff1) 与该工程含义一致。
7. **Diff1 物理观察端证据**：人工审查记录、端口位置、原理图或 AEDT 截图。缺失则停止。
8. 几何约束：anti-pad ≤ 22 mil，NFP ∈ [7.875, 10] mil；首轮不做 trace 路由优化。
9. candidate inventory 由实际 AEDB 的 `candidate_inventory_builder` 生成，不能手写 layer/shape/via id。
10. 用户明确允许首轮真实求解；首轮建议 `max_rounds=1`。
11. 本地配置文件：
   - `config/execution_profiles/local_real_aedt.json`
   - `config/optimization_loops/reviewed_brd_remote.json`

当前 checkout 只有 `.example.json`，这两个文件必须在生产端生成/填写，不能提交。

## 2. 启动前检查

~~~powershell
cd D:\ansys-agent
$env:PYTHONUTF8 = "1"
$env:RUN_REAL_LIVE_AEDT = "0"
$env:RUN_REAL_AEDT = "0"
$env:RUN_REAL_PYEDB = "0"
$env:ANSYS_AGENT_RUN_REAL_AEDT = "0"

..venvScriptspython.exe -m aedt_agent.agent `
  mission validate-loop-config `
  --config config\optimization_loops\reviewed_brd_remote.json
~~~

要求全部 passed，尤其：

- template_loadable
- source_project_path_present
- working_project_path_present
- working_project_is_separate
- working_project_under_run_root
- report_dir_under_run_root
- touchstone_is_s4p
- differential_traces
- tdr_diff1
- differential_reference_impedance
- geometry_constraints

记录：

- source `.aedt` SHA256；
- source sidecar 是否存在；
- working 是否不存在或确为本 run 可复用副本；
- repo commit；
- config 与 profile 的绝对路径。

## 3. 启动顺序

先设置真实运行开关（只在生产端、人工确认后）：

~~~powershell
$env:RUN_REAL_LIVE_AEDT = "1"
$env:RUN_REAL_AEDT = "1"
$env:RUN_REAL_PYEDB = "1"
$env:ANSYS_AGENT_RUN_REAL_AEDT = "1"
$env:PYTHONUTF8 = "1"
$db = "D:\aedt-agent-runs\reviewed-loop\missions.db"
~~~

1. 先启动 dashboard：

~~~powershell
..venvScriptspython.exe -m aedt_agent.agent `
  --db $db `
  mission web --host 0.0.0.0 --port 8766 `
  --profile config\execution_profiles\local_real_aedt.json
~~~

2. 再启动唯一 runner（后台 job，不要重复启动）：

~~~powershell
..venvScriptspython.exe -m aedt_agent.agent `
  --db $db `
  mission run-loop `
  --config config\optimization_loops\reviewed_brd_remote.json `
  --profile config\execution_profiles\local_real_aedt.json `
  --worker-id production-operator `
  --max-workers 1
~~~

3. 轮询间隔 30 秒；不要高频轮询 AEDT。

4. 首轮只看 baseline，不自动进入多轮优化。

## 4. 首轮验收

必须逐项检查：

- graph 到达期望停止点（`waiting_approval` 或 `succeeded`），不是仅“进程退出”；
- source SHA256 未变化；
- working 工程与 source 分离；
- 报告目录包含：
  - `optimization_history.csv`
  - `optimization_progress.html`
  - `optimization_progress.json`
  - `optimization_artifacts.json`
- `optimization_artifacts.json` 中每个 artifact 存在、位于 run_root、SHA256 可重算；
- SDD11/SDD21 最差值及频率；
- TDR 峰值偏差、峰值时间、Diff1 观察端口；
- orientation 是否已证实；
- objective cost；
- 动作、审批或失败原因；
- source/working 与报告路径。

原始 S 参数和 TDR 曲线只作为 artifact，不粘贴到聊天/LLM payload。

## 5. 停止条件

出现任一情况必须停止，不得继续自动优化：

- Diff1 物理观察端证据缺失；
- working 与 source 相同，或 working/report 越界；
- source SHA256 在报告前变化；
- report worker 返回 `source_project_integrity_unavailable` 或 `source_project_modified`；
- report manifest 发现 artifact 缺失、越界或 hash 不符；
- graph waiting_approval 但审批信息不完整；
- 真实 AEDT solve 失败、license 错误或结果不可解释。

## 6. DSH 边界

DSH 当前只有一个 `Ansys 工程优化模式`。它允许工程查询与几何 preview/apply，但 `production-guard.mjs` 拒绝：

- `preview_ansys_workflow_start`
- `apply_ansys_workflow_start`
- `preview_ansys_workflow_advance`
- `apply_ansys_workflow_advance`

生产 Mission 的启动、advance、恢复和 human_gate 审批只能由本地 CLI/dashboard 执行。DSH 只读观察 dashboard/Mission DB；恢复依据 `graph_run_id`，不是聊天上下文。

## 7. 回传内容

生产端首轮完成后回传：

- graph_run_id / mission_id；
- graph 最终状态和停止节点；
- 上述 bounded 指标；
- 报告与 manifest 路径；
- source SHA256 与 working 隔离结论；
- 审批/失败原因；
- 任何未满足的 T4 输入项。

未完成真实 AEDT 前，只能声明“T0–T3 离线验收通过”，不能声明真实闭环通过。
