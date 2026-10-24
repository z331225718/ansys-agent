# M1 实施计划：Reviewed BRD 差分 Via 优化闭环与 DSH 观察入口

- 日期：2026-10-24
- 执行对象：可独立阅读本文件的低成本编码 Agent
- 基线分支：codex/pi-operator-poc
- 初始基线提交：bf2f4f1e6296f91ada78665e41510475dbb95429
- 计划文档提交：9c3989eec6fed7028d0a413f1400617d691df76d
- 当前实施基线：本计划最新提交（执行时以 `git rev-parse HEAD` 实测记录，不机械假定固定 SHA）
- 目标分支：feature/reviewed-brd-dsh-loop
- 计划状态：可执行；真实 AEDT 运行必须先通过第 9 节人工启动门

## 1. 最终目标

在一个**已由人类工程师审查、可求解的四端口差分 AEDT local-cut 工程**上，完成可恢复、可回滚、可审计的优化闭环：

~~~text
人类审查的源工程
  -> 仅复制一次到 working project
  -> 真实求解与 Touchstone/TDR 导出
  -> 确定性 SDD11/SDD21/TDR/objective 评分
  -> LLM 仅根据有界证据提出候选动作
  -> 几何约束校验与人工审批
  -> working project 修改、回读、失败回滚
  -> 下一轮或终止
  -> 历史 CSV、HTML 报告、前后对比与证据清单
~~~

M1 的产品结论是：**一个真实工程能否稳定、可审计地完成优化闭环**。不是继续增加通用 AEDT 工具数量，也不是继续搭 Agent 框架。

## 2. 不得违反的边界

1. **源工程只读。** 不得以 working_project_path 缺省、同路径、相对路径绕行或配置别名方式修改源工程。
2. 一个 run 只维护一个 working AEDT bundle；不得每轮新建独立工程。
3. 生产优化必须走 brd_reviewed_model_optimize_loop 的 worker/graph。LLM 不得替代 solve/export/score/geometry validate/model edit/report worker。
4. DSH reviewed 模式不得注册提供 run_python_code / run_python_script 的官方 PyAEDT-MCP，不得开放 shell、任意文件写入或任意 PTC 代码执行。
5. DSH explore 才允许官方 PyAEDT-MCP 和 PTC；探索结果不能直接作为生产修改证据。
6. 原始 S 参数/TDR 曲线保持 artifact-only。报告只写有界指标、哈希和 artifact 路径。
7. 不把“单元测试通过”“配置校验通过”“mock 运行通过”写成“真实 AEDT 闭环通过”。
8. 指标不改善不必然是代码失败；必须如实标记 completed_no_improvement 或实际失败状态，不得伪造改善。
9. 不得自动批准人工门，不得伪造 approval token，不得关闭校验或负向测试来让流程通过。
10. 不启动 SSH；使用 simulation_runner=local_cli、max_workers=1、max_concurrent_aedt=1。
11. 不顺手重构 live/backend.py，不恢复 Pi Operator，不修改无关 benchmark/legacy v0。
12. 不提交 .aedt/.aedb、真实工程路径、密钥、原始曲线或大型结果目录到 Git。

## 3. 已知基线与执行环境事实

### 3.1 代码基线

计划编写时工作树干净，HEAD 为上述基线提交。最近验证记录：

- 排除 tests/test_offline_release_scripts.py 时：1424 passed, 31 skipped。
- 未排除的基线曾有一个 Windows PowerShell 5 离线 preflight 失败：test_powershell5_runs_full_offline_preflight_with_target_python。
- 执行者**不得直接宣称全套测试已经通过**，必须先重新建立本机基线。

### 3.2 已有可复用能力

- src/aedt_agent/agent/loop_runner.py：
  - validate_loop_config_for_run() 校验 source/working 分离、run_root/report_dir、s4p、差分 SDD/TDR、90ohm、几何约束；
  - 配置中存在 graph_run_id 时可恢复已有 graph，不新建 mission。
- docs/agent_templates/brd_reviewed_model_optimize_loop.yaml：完整 reviewed graph 与人工门。
- src/aedt_agent/agent/optimization_handlers.py：准备 working project、候选动作、报告等。
- src/aedt_agent/harness/：变量、Via、反焊盘、HFSS 几何与材料领域 Harness。
- src/aedt_agent/aedt_access/：直连 gRPC 默认访问层，Legacy broker 仅为兼容路径。
- DSH 工具层支持 ctx.tools.restrict() 与 ctx.tools.guard()；但**不要仅依赖 persona 或 allow/deny 文本实现生产安全**，reviewed 模式还要从 MCP 注册与 preset 组合上移除危险入口。
- graph_run_id 可恢复 graph；DSH 本身不是 Mission 状态权威，SQLite/artifacts 才是。

### 3.3 已知风险

- prepare_working_project() 当前在 working_project_path 为空时会回退到 source_project，且相同路径时不会触发复制保护。**T1** 必须在执行层 fail closed；T0 不改业务代码。
- DSH preset 目录不是模式选择器的数据源；GUI 需要 Cordis dsh-agent-preset 声明。
- DSH patch 启动时读取；修改 preset/MCP 后需重启或重载对应 profile。
- config/*.local.json 不能假定覆盖所有文档中的本机配置名；本计划显式保护真实 local config。

## 4. 执行者工作方式

1. 先完整阅读本文、CLAUDE.md、docs/remote-reviewed-model-loop.md、docs/agent_playbooks/brd-local-cut-optimization.md。
2. 严格按任务顺序执行；每个任务先写/补失败测试，再做最小实现。
3. 每个任务完成后运行指定测试并记录命令、结果、commit。
4. 只修改任务列出的范围；需要扩大范围时停下报告，不自行改架构。
5. 遇到失败先读日志，不重复盲目重试。相同失败两次仍无新证据，停止并报告。
6. 不调用真实 AEDT 做普通单元测试。真实运行只从第 9 节开始，且需要人类明确启动。
7. 每轮结束输出：完成项、测试证据、未完成项、风险、下一步；不要只写“已完成”。

## 5. T0：冻结基线与分支

### 修改范围

- 新分支与 .gitignore。
- 新建 docs/evidence/README.md；不修改业务代码。

### 步骤

1. 确认当前只包含已审阅的计划文档提交，先提交计划修订；不得把未审阅改动带入实施。
2. 确认 git status --short 干净，并用 `git rev-parse HEAD` 记录实际实施基线；允许 HEAD 是上述计划提交的后续文档修订，不要求机械等于旧 SHA。
3. 创建 feature/reviewed-brd-dsh-loop；不要直接 push/merge main。
4. 先清除所有已知真实测试开关，确认没有 AEDT solve/job 在运行，再运行完整离线测试并保存汇总，不粘贴长日志。不得只依赖 pytest 的 skip 默认值：

~~~powershell
$env:RUN_REAL_LIVE_AEDT = '0'
$env:RUN_REAL_AEDT = '0'
$env:RUN_REAL_PYEDB = '0'
$env:ANSYS_AGENT_RUN_REAL_AEDT = '0'
.\.venv\Scripts\python.exe -m pytest tests -q --no-header
~~~

5. 若失败，先区分基线失败与本次改动失败。不得删除/跳过测试掩盖问题。与本任务无关的基线失败要记录并报告；最终合入门仍需全绿或人类明确豁免。
6. 在 .gitignore 显式加入（保留 example 文件）：

~~~gitignore
config/execution_profiles/local_real_aedt.json
config/optimization_loops/reviewed_brd_remote.json
config/optimization_loops/*_input.local.json
docs/evidence/*/
!docs/evidence/README.md
~~~

### 验收

- 分支正确，工作树只包含预期变更。
- 有完整离线测试基线；没有未经说明的测试删除。real-AEDT acceptance 在本阶段只能记录为未启用/未运行，不得声称已验收。
- git check-ignore 能识别本机配置，不能忽略 *.example.json。

## 6. T1：working project 与源工程完整性的 fail-closed 修复

### 修改范围

- src/aedt_agent/agent/optimization_handlers.py
- src/aedt_agent/agent/loop_runner.py（仅确有需要，包括 graph_run_id 校验提前返回问题）
- src/aedt_agent/agent/workers/brd_progress_report.py（真实最终报告 worker）
- src/aedt_agent/infrastructure/harness/child_main.py 与同目录 local_process.py（仅在进程外失败 artifact 保留确有需要时做最小修改）
- src/aedt_agent/reporting/（仅需要持久化完整性证据时）
- tests/test_agent_optimization_handlers.py
- tests/test_agent_loop_runner.py
- tests/test_agent_brd_progress_report_worker.py
- tests/test_agent_harness_child.py 与 tests/test_agent_local_process_harness.py（对应进程外失败证据）
- tests/test_agent_graph_runner_dag.py（仅用于验证 graph failed 传播时）
- 可新增一个小型、明确命名的测试辅助模块；不得改 AEDT worker 语义。现有 WorkerReportedError.details 已能保存到 job error 与事件日志，失败证据优先使用该通道，不为它修改正常 worker 结果契约。

### 必做行为

1. source_project_path 与 working_project_path 都必须显式非空。
2. 在**任何 lock 删除、复制、目录创建或 AEDT 操作之前**，规范化并比较路径：
   - source 与 working 不得相同；
   - working 必须位于 run_root 下；
   - report_dir 必须位于 run_root 下；
   - source 必须存在且为 .aedt 文件。
3. 删除 working_project_path 缺省时回退 source 的行为。路径比较必须使用 `resolve()`/canonical 与 `os.path.normcase()` 的 Windows 大小写语义，覆盖相对路径别名以及现存 junction/symlink 父目录；不能只做字符串前缀比较。working/report_dir 尚不存在时也必须从最近的现存父目录逐层解析父链中的 junction/symlink，再拼接剩余部分；不能只解析已存在的目标本身。
4. 同路径、空 working、working 越界时返回明确错误或既有结构化失败，不得产生文件副作用。
5. 首次准备时记录源工程身份到 bounded run evidence：
   - source .aedt SHA256；
   - source/working 路径（仅本机 artifact，不进 LLM 长上下文）；
   - sidecar .aedb 是否存在及既有 bundle digest（若实现提供）；结果 sidecar 是否存在；
   - 如已有 bundle digest 工具则复用，不新写昂贵的全 AEDB 重复遍历。
6. **成功终结必须覆盖真实 worker**：在 `run_brd_optimization_report_worker()`（capability `brd.optimization.report`）返回前复核 source `.aedt` SHA256。不能只改 `write_optimization_report()`，因为 reviewed graph 实际走该 worker。
7. hash 不一致或无法读取时，在**已经写出诊断报告之后**抛 `WorkerReportedError('invalid_model', '<source_project_modified 诊断消息>', retryable=False, details={'code':'source_project_modified', 'artifact_refs':[...诊断路径...]})`。注意第二个位置参数 message 必填，不能照抄成无 message 的调用。真实 report worker 注册为 local_process，需使该异常经 child_main/HarnessResult/JobStatus.FAILED/`_execute_worker` 传播为 graph failed。JobError.details 与事件日志保存诊断路径；若验收要求 artifact 清单也保留，允许 child_main.py 的失败分支最小透传 artifact_refs。禁止只返回 `status='failed'` 字符串后仍让 graph succeeded。用进程外 worker 与 graph 测试证明最终状态和证据位置，不只测字符串。
8. **区分 worker 与 program handler 的失败语义**：真实 report worker 按上一条抛 WorkerReportedError；`fail_optimization()` 等 program handler 保持结构化 FAILED，在返回前记录完整性检查、诊断 payload 和 artifact_refs，不抛异常触发 `_failed_result()` 清空诊断。waiting_approval 是暂停，不做最终成功声明。恢复时必须沿用第一次保存的 baseline hash，不得重新记录后覆盖旧基线。若 graph executor 必须修改才能保留失败诊断，先报告缺口，不擅自扩大范围；不得编造证据。
9. `.aedt` hash 只证明主文件未变；完整 AEDB/结果 sidecar 完整性要在报告中单列检查范围，不得用主文件 hash 冒充全 bundle 证明。
10. 保持 reset_working_project=false 的复用语义；不得每轮复制新 bundle。

### 必加负向测试

- source == working（含 Windows 大小写/相对路径别名）被拒绝，source 内容和 source lock 均未改变。
- 若平台支持，junction/symlink 指向 source 或 run_root 外也被拒绝；额外覆盖“working/report_dir 尚不存在，但其现存父目录或更高层父目录是 junction”的情况。若无法创建链接，明确标记未验证而不是假称通过。
- working 缺省被拒绝。
- working 在 run_root 外被拒绝。
- source 不存在被拒绝。
- 正常 copy-once 测试继续通过。
- 第二次准备不会新建另一套 bundle。
- source digest 发生变化时真实 report worker 经 local_process 传播 JobStatus.FAILED，graph 最终为 failed；诊断 artifact 路径能从 JobError.details/事件中读取；child_main 透传后再从 artifact 清单交叉核对。program handler 的 fail_optimization 保持结构化 FAILED 且原 payload/artifacts 保留（用 fixture 模拟，不触碰真实 AEDT）。

### 验收命令

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_optimization_handlers.py tests\test_agent_loop_runner.py -q
~~~

## 7. T2：DSH reviewed / explore 双模式与可重复安装

### 安全设计（按此实现，不改成共享全开 preset）

| 项目 | ansys-reviewed（M1 只读观察） | ansys-explore |
|---|---|---|
| 用途 | 观察真实工程 Mission、报告与人工审批材料 | API 查询、临时探索、非生产试验 |
| ansys-assistant MCP | **独立 reviewed 配置，仅允许只读 workflow catalog/status** | 开启受控工具 |
| ansys-api-memory | 开启 | 开启 |
| 官方 PyAEDT-MCP | **不注册** | 注册 |
| 裸 run_python_code/script | 无入口 | 允许，结果标记非生产证据 |
| shell / 任意文件写 / 任意 PTC | 不开放 | 按 DSH 沙箱策略开放 |
| 生产 Mission/Graph | 本地 CLI/dashboard 启动、advance、human_gate 审批；DSH 仅查看 | 不作为生产入口 |

**M1 明确不让 DSH 直接启动/advance/批准 reviewed BRD graph。** 当前 `apply_start` 总会新建 mission、workflow 需要 live_session_id，且 BRD human_gate 没有对应的 MCP 批准工具。不得让执行者临时补一套不完整协议来代替这些边界。启动、advance、恢复与 human_gate 批准继续使用已有受控本地 CLI/dashboard；DSH reviewed 只读观察。这一限制取消“DSH 驱动执行”的未完成部分，不影响真实闭环验收。

### 修改范围

- integrations/dsh/agent-presets/
- integrations/dsh/ 下安装说明和模板
- src/aedt_agent/interactive/server.py 及紧邻的配置模块（仅实现 reviewed 只读 allowlist，不新增 workflow/审批协议）
- scripts/dsh/（新增安装/检查脚本）
- tests/dsh/（新增）
- tests/test_interactive_ansys_assistant.py 或 tests/test_assistant_workflows.py（对应只读 allowlist）
- .mcp.json 与 config/harness/dual_pyaedt_ansys_agent_mcp.json：只移除机器绝对路径，改为安装时生成/替换的参数或文档变量，不删除现有能力。

### 实现要求

1. 新增 ansys-reviewed preset 和 ansys-explore preset；现有 ansys-engineering 明确标记为 explore 或迁移到 ansys-explore，不得保留一个含义不清的全权限生产模式。
2. reviewed 必须使用**独立的 ansys-assistant MCP 配置/工具 allowlist**，不能仅不注册官方 MCP 就认为工具面闭合。最小正向集合为：
   - list_ansys_workflows
   - inspect_ansys_workflow
   - get_ansys_workflow_status
   以及确有必要时的只读 list_ansys_capabilities_v2。
   不允许 preview/apply workflow、直接几何/材料/Via 修改、exploration execute、capability promotion、任意 shell/文件写/PTC。优先在服务端 MCP 配置层实现 allowlist；若用 DSH scoped filter，必须提供准确的 agent-scoped 插件和工具名，不能用 persona 或普通 preset 文本冒充。
3. explore preset 可保留 PTC、官方 MCP 和探索技能；其 persona 必须声明“探索结果不是生产审批证据”。
4. reviewed 只读观察的运行配置必须与生产 CLI 一致：
   - ansys-assistant 的 brd_reviewed_model_optimize_loop 使用 mission_process_harness，并非 live-session workflow；
   - MCP 的 AEDT_AGENT_WORKFLOW_DB 必须与 CLI/dashboard 的 $db 完全一致；
   - 本地 CLI 生产执行的 --profile 必须解析到 config/execution_profiles/local_real_aedt.json，而不能误用 safe-recorded；只读 status MCP 不启动 worker，不以加载真实 solve profile 作为其验收条件；
   - 不把 live_session_id 当成 reviewed 的启动/恢复凭据；M1 不暴露 start/advance/approve。
5. 新增可重复执行的安装器，例如 scripts/dsh/install_ansys_dsh.py。
6. 安装器接口至少支持：

~~~powershell
python scripts/dsh/install_ansys_dsh.py check --repo-root <repo> --dsh-profile <profile-dir> --python <venv-python>
python scripts/dsh/install_ansys_dsh.py apply --repo-root <repo> --dsh-profile <profile-dir> --python <venv-python>
~~~

7. check 只读；apply 必须先备份，并且幂等。不得覆盖其他 MCP、preset、模型或 UI 配置。
8. 对 cordis.patch.yml 只管理带明确 begin/end marker 的 ansys-agent block。遇到**不属于本安装器的同 id/冲突条目或未标记旧条目**：check 必须失败，apply 必须在任何写入前退出；不是“报告后继续插入”。只有完成备份、且能明确证明条目属于本安装器旧版本时才可迁移。YAML 中含 !!js 时不得用普通 YAML parser 重写整个文件。
9. 本机绝对路径只允许由安装参数生成到本机 DSH profile；仓库模板中不得再写死 C:\Users\z3312。
10. 安装后输出缺少的 Python 模块、版本、MCP 入口、preset 声明及“需重启 DSH”提示。不得自动关闭用户 AEDT 或 DSH。
11. 添加临时目录测试：首次安装、重复安装、已有用户配置保留、check 无副作用、错误路径失败、未标记冲突条目在写入前失败、reviewed 不含官方裸代码 MCP、模板无本机用户名。

### 验收

- 在临时 profile 完成两次 apply，第二次内容不增加重复项。
- 用临时 SQLite DB 和 fake worker 证明 reviewed 只读观察的 DB 指向正确；另验证 CLI 生产配置加载 local_real_aedt.json 而非 safe-recorded，真实 solve 留到 T4。
- 在真实 DSH profile 先运行 check；只有用户明确要求安装时才 apply。
- 重启后 GUI 同时出现 reviewed/explore；从**实际模型可见工具清单**验证 reviewed 只有第 2 条的只读工具，无 preview/apply/直接修改/探索执行/run_python_code/shell/任意写入入口。
- 记录验收证据；不以 persona 中存在“禁止”字样代替工具面检查。

## 8. T3：离线验收、恢复语义与报告证据

### 修改范围

- src/aedt_agent/agent/、src/aedt_agent/reporting/（仅限恢复/证据缺口）
- src/aedt_agent/interactive/workflows.py（仅在确需只读 status/DB 配置时，禁止新增 start/advance/approve 协议）
- tests/test_agent_loop_runner.py
- tests/test_assistant_workflows.py
- tests/test_agent_brd_progress_report_worker.py
- tests/test_brd_optimization_report.py
- tests/test_agent_optimization_handlers.py
- docs/remote-reviewed-model-loop.md
- docs/evidence/README.md 和执行记录模板。

### 必做验收

1. 全量测试：

~~~powershell
$env:RUN_REAL_LIVE_AEDT = '0'
$env:RUN_REAL_AEDT = '0'
$env:RUN_REAL_PYEDB = '0'
$env:ANSYS_AGENT_RUN_REAL_AEDT = '0'
.\.venv\Scripts\python.exe -m pytest tests -q --no-header
~~~

2. 真实 config 静态校验（无 AEDT 调用）：

~~~powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent mission validate-loop-config --config config\optimization_loops\reviewed_brd_remote.json
~~~

必须包含：template loadable、working separate、s4p、differential、Diff1、90ohm、geometry constraints、paths 全通过。

3. 用已有测试/fixture 证明（先读现有测试，已有的只补充证据，不重复造测试）：
   - 配置中已有 graph_run_id 时不创建新 mission；
   - waiting_approval 保留 run 与 handoffs；
   - 用 fixture 推进到安全停止点（如 waiting_approval）后，销毁第一个 CLI runtime 和 AssistantWorkflowManager；使用同一临时 SQLite DB 分别重建，调用同一 graph_run_id；
   - 重建后先只用 AssistantWorkflowManager.status() 验证 mission/graph/node/handoff/DB 一致、mission 数量不增加、prepare_working_project/copy 仍只执行一次、working bundle 不新增；**status() 不得推进 graph**；
   - 持久化 human_gate 只能由已有 CLI ApprovalService/CLI 批准，再由 CLI advance_graph/resume_graph 推进一步；不得要求新 Assistant preview/token 作为恢复凭据，也不得要求它隐式批准或推进；
   - 错误 DB/未知 graph_run_id 必须失败；preview/binding 属进程内存，重启后必须重新生成，不得复用旧 token；
   - 断言只读 workflow runtime_factory 收到的 DB 精确等于安装值；dashboard、runner、MCP 使用同一个 $db；CLI runtime 的 profile 则精确等于 local_real_aedt.json；
   - graph_run_id 恢复配置的校验不得绕过 working separate/s4p/Diff1 等关键约束（loop_runner.py 56-59 当前有提前返回，T1/T3 必须处理并测试）。
4. 报告验证（使用小型 fixture，不允许只检查字段存在）：
   - 逐行核对 optimization_history.csv 的完整/partial/failed round、关键数值和状态；
   - optimization_progress.html 与 optimization_progress.json 必须可读且字段值与 fixture 一致；
   - 核对 SDD11/SDD21 最差值和频率、TDR 峰值偏差、观察端口、orientation 是否已证实、objective cost、动作与审批/失败原因；
   - **重算每个被引用 artifact 的 SHA256** 与 manifest 对比，并验证引用路径属于该 run；
   - 不把全曲线写进报告摘要或 LLM payload。无需为通过验收新增报告功能。
5. 恢复测试只使用 fixture/mock 或安全停止点；不得为测试强行 kill AEDT 求解进程或中断 geometry write。
6. 更新文档：DSH 是对话/只读观察与材料展示入口；本地 CLI/dashboard 才负责启动、advance、human_gate 审批，CLI worker 与 SQLite 持有工程运行状态；恢复使用 graph_run_id，不是依赖聊天上下文。

### 完成定义

- 单元/集成测试全绿，或剩余失败经人类逐项书面豁免。
- 没有通过删除测试、放宽断言或把真机验收标成 skip 的方式获得绿色。
- 本阶段只能声明“离线验收通过”，不能声明“真实闭环通过”。

## 9. T4：真实 AEDT 人工启动门与首轮运行

**执行者必须在完成 T0-T3 后停下。没有以下全部输入，不得启动真实 solve。**

### 9.1 用户必须确认/提供

- 人类已审查且可求解的 .aedt 源工程，当前为四端口差分模型。
- 源工程允许只读使用，working/run_root/report_dir 位于受控运行目录。
- AEDT 2026.1、本机 license、本地 venv 可用。
- Setup1/Sweep1 或明确的实际 setup/sweep 名称。
- Touchstone 为 .s4p，expected_port_count=4，sparameter_mode=differential。
- SDD11、SDD21 与 TDRZ(Diff1) 的含义符合该工程。
- **Diff1 物理观察端证据**：不是从名字猜；来源可为人工审查记录、端口位置/原理图/AEDT 截图。缺失则停止。
- 初始工艺/几何约束：anti-pad <=22mil，NFP ∈[7.875,10]mil；无路线 trace 优化。
- candidate inventory 由 candidate_inventory_builder 从实际 AEDB 生成，不能由执行者手写 layer/shape/via id。
- 用户允许首轮真实求解；首次建议 max_rounds=1，先证明 baseline 与报告链路，再决定是否进行多轮优化。
- 用户明确 LLM profile/key 已配置；未配置时 deterministic fallback 的能力限制必须记录，不能宣称使用了高推理模型。

### 9.2 启动前检查

1. 运行 T1/T3 的源完整性与 config 校验。
2. 记录源 .aedt SHA256、sidecar 状态、run 配置、代码 commit。
3. 检查 working 不存在或确为本 run 可复用副本；不得复用来源不明的旧 working。
4. 确认 dashboard 与 runner 使用同一个 $db：

~~~powershell
$env:PYTHONUTF8 = "1"
$db = "D:\aedt-agent-runs\reviewed-loop\missions.db"
~~~

5. 启动 dashboard 后才启动 run-loop；使用后台 job，不另起重复 runner：

~~~powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent --db $db mission web --host 0.0.0.0 --port 8766 --profile config\execution_profiles\local_real_aedt.json
.\.venv\Scripts\python.exe -m aedt_agent.agent --db $db mission run-loop --config config\optimization_loops\reviewed_brd_remote.json --profile config\execution_profiles\local_real_aedt.json --worker-id dsh-reviewed-operator --max-workers 1
~~~

6. 遵循 30 秒 graph 轮询，不对 AEDT 进行高频轮询。收集后台 job，不在最终报告中遗留无用 job。
7. 首次 baseline 完成并由用户审查报告后，才可设置 max_rounds=3 进行优化。多轮中的每个人工门都由用户判断，执行者不得自动批准。

### 9.3 人工审批最低信息

- round、候选 action 类型与目标结构；
- layer、shape evidence、via/padstack center 来源、radius；
- tdr_observation_port 与 orientation 证据；
- preview/digest、rollback 证据；
- 当前 SDD11/SDD21/TDR/objective；
- 用户要批准的具体变更，不接受“继续吗”这种无信息问题。

## 10. T5：真实证据包与最终验收

### 必须生成/核验

- optimization_history.csv
- optimization_progress.html
- optimization_progress.json
- Touchstone .s4p、TDR 与 SDD 有界证据
- run manifest：代码 commit、配置摘要、source .aedt SHA256、working 标识、graph/mission id
- 审批与回滚记录（没有发生 rollback 时也要明确写“未触发”，不能编造）
- 前后指标对照和下一步建议

### 结果分类（只选一个，按事实）

- completed_with_improvement：闭环完成且有符合配置目标的改善；
- completed_no_improvement：闭环完成，但未改善，附原因/下一轮建议；
- stopped_for_approval：等待人工，不能写成功；
- failed：给出失败节点、错误码、artifact 与是否恢复/回滚；
- offline_only：没有真实 AEDT 证据，只完成离线验收。

### 真实闭环通过的必要条件

- graph 正常结束，或用户批准的 max_rounds 自然结束；
- source .aedt SHA256 前后一致，working 与 source 分离；source .aedb/结果 sidecar 的检查范围和结论也有记录；
- 没有未经审批的生产修改；
- 所有实际修改均有 readback；若失败有 rollback 证据；
- 恢复语义验证没有新建重复 mission/working bundle；
- 报告与 bounded metrics 完整，artifact 可读且 hash 匹配；
- 全文没有 raw curve 大段粘贴；
- 对“指标改善”和“工程闭环完成”分别作结论，不混为一谈。

### 证据文档

在 docs/evidence/<run-id>/README.md 写摘要（或由 .gitignore 选择仅保留本机证据），正文只包含：

- 状态分类；
- 上述 bounded metrics；
- graph/mission/run id；
- artifact 相对/绝对路径与 SHA256；
- 测试命令及结果；
- 未解决问题与下一步。

真实 .aedt/.aedb、原始曲线、完整日志和密钥不得提交。最终只能由人类决定是否合并到 main。

## 11. 明确不在本计划内

- 新建更多通用 AEDT/HFSS 工具；
- 全面重写或仅为减少行数拆分 live/backend.py；
- 支持更多 solver、更多板卡或更多优化动作；
- AEDT 2024 R2 兼容性宣称、离线安装包发布、跨机 SSH；
- 自动能力晋升、热注册、自动审批；
- 修改优化目标来获得表面改善；
- 用 cheap model 执行时顺便升级 LLM、改 provider 或引入新框架。

这些分别属于 M2/M3/M4，不得混入 M1。

## 12. 给低成本执行 Agent 的直接指令

~~~text
在 C:\Users\z3312\code\ansys-agent 工作。
完整阅读 docs/plans/2026-10-24-reviewed-brd-dsh-m1-execution-plan.md，
并严格按 T0 -> T1 -> T2 -> T3 执行，每步运行其中指定的测试。
只做该文件允许的最小改动，先补负向测试，不扩大范围，不重写 backend，
不操作真实 AEDT，不自动批准，不启动 solve，不删除失败测试。
完成 T3 后停止，汇报测试证据、commit、遗留风险，并等待用户完成第 9 节
真实 AEDT 人工启动门。不要把离线验收声称为真实闭环。
~~~

## 13. 执行记录模板（每一步必填）

~~~text
Task:
Commit:
Changed files:
Tests (exact command + summary):
Negative cases verified:
Real AEDT called? (must be no before T4):
Source project touched? (must be no):
Evidence/artifact paths:
Failures or pre-existing issues:
Out-of-scope changes requested:
Next safe action:
~~~