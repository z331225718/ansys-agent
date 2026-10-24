# M1 实施计划：DSH 驱动的 Reviewed BRD 差分 Via 优化闭环

- 日期：2026-10-24
- 执行对象：可独立阅读本文件的低成本编码 Agent
- 基线分支：codex/pi-operator-poc
- 基线提交：bf2f4f1e6296f91ada78665e41510475dbb95429
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

- prepare_working_project() 当前在 working_project_path 为空时会回退到 source_project，且相同路径时不会触发复制保护。M1-0 必须在执行层 fail closed。
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

1. 当前未提交的只有本计划文档；先提交本计划（或明确保留为计划变更），再创建目标分支。不得把工作树中的未审阅改动带入实施。
2. 确认 git status --short 干净，HEAD 等于基线提交或仅比基线多本计划提交。
3. 创建 feature/reviewed-brd-dsh-loop；不要直接 push/merge main。
4. 运行完整测试，保存汇总，不粘贴长日志：

~~~powershell
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
- 有完整测试基线；没有未经说明的测试删除。
- git check-ignore 能识别本机配置，不能忽略 *.example.json。

## 6. T1：working project 与源工程完整性的 fail-closed 修复

### 修改范围

- src/aedt_agent/agent/optimization_handlers.py
- src/aedt_agent/agent/loop_runner.py（仅确有需要）
- tests/test_agent_optimization_handlers.py
- tests/test_agent_loop_runner.py
- 可新增一个小型、明确命名的测试辅助模块；不得改 AEDT worker 语义。

### 必做行为

1. source_project_path 与 working_project_path 都必须显式非空。
2. 在**任何 lock 删除、复制、目录创建或 AEDT 操作之前**，规范化并比较路径：
   - source 与 working 不得相同；
   - working 必须位于 run_root 下；
   - report_dir 必须位于 run_root 下；
   - source 必须存在且为 .aedt 文件。
3. 删除 working_project_path 缺省时回退 source 的行为。
4. 同路径、空 working、working 越界时返回明确错误或既有结构化失败，不得产生文件副作用。
5. 首次准备时记录源工程身份到 bounded run evidence：
   - source .aedt SHA256；
   - source/working 路径（仅本机 artifact，不进 LLM 长上下文）；
   - sidecar .aedb 是否存在及既有 bundle digest（若实现提供）；结果 sidecar 是否存在；
   - 如已有 bundle digest 工具则复用，不新写昂贵的全 AEDB 重复遍历。
6. 在 write_optimization_report() 和 fail_optimization() 共用的终结路径复核 source .aedt SHA256，并写入 evidence/report。不要只在成功分支检查。若不一致，覆盖为明确失败状态 source_project_modified，不得仍报告成功；无需修改图的拓扑。.aedt hash 只证明主文件未变；完整 AEDB 完整性要在报告中单列检查范围，不得用主文件 hash 冒充全 bundle 证明。
7. 保持 reset_working_project=false 的复用语义；不得每轮复制新 bundle。

### 必加负向测试

- source == working 被拒绝，source 内容和 source lock 均未改变。
- working 缺省被拒绝。
- working 在 run_root 外被拒绝。
- source 不存在被拒绝。
- 正常 copy-once 测试继续通过。
- 第二次准备不会新建另一套 bundle。
- source digest 发生变化时最终验收状态为失败（用 fixture 模拟，不触碰真实 AEDT）。

### 验收命令

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_optimization_handlers.py tests\test_agent_loop_runner.py -q
~~~

## 7. T2：DSH reviewed / explore 双模式与可重复安装

### 安全设计（按此实现，不改成共享全开 preset）

| 项目 | ansys-reviewed | ansys-explore |
|---|---|---|
| 用途 | 真实工程与生产优化 | API 查询、临时探索、非生产试验 |
| ansys-assistant MCP | 开启受控 workflow/Harness | 开启 |
| ansys-api-memory | 开启 | 开启 |
| 官方 PyAEDT-MCP | **不注册** | 注册 |
| 裸 run_python_code/script | 无入口 | 允许，结果标记非生产证据 |
| shell / 任意文件写 / 任意 PTC | 不开放 | 按 DSH 沙箱策略开放 |
| 生产 Mission/Graph | 允许 preview/approve/advance | 不作为生产入口 |

说明：reviewed 若需要启动或恢复 graph，使用 ansys-assistant 已有的 list/inspect/preview/apply workflow 与 get_ansys_workflow_status，不要为绕过限制重新开放 shell。长循环 worker 由本地 CLI/受控服务运行，DSH 只发起、审批和查看。

### 修改范围

- integrations/dsh/agent-presets/
- integrations/dsh/ 下安装说明和模板
- scripts/dsh/（新增安装/检查脚本）
- tests/dsh/（新增）
- .mcp.json 与 config/harness/dual_pyaedt_ansys_agent_mcp.json：只移除机器绝对路径，改为安装时生成/替换的参数或文档变量，不删除现有能力。

### 实现要求

1. 新增 ansys-reviewed preset 和 ansys-explore preset；现有 ansys-engineering 明确标记为 explore 或迁移到 ansys-explore，不得保留一个含义不清的全权限生产模式。
2. reviewed preset 使用直接工具调用，不启用任意 PTC；显式禁用 shell、文件写和任意代码执行相关工具。可用 DSH ctx.tools.restrict() 时按官方接口实现；无法限制的 MCP 入口通过“不注册”消除。
3. explore preset 可保留 PTC、官方 MCP 和探索技能；其 persona 必须声明“探索结果不是生产审批证据”。
4. 验证并补齐 reviewed workflow 的真实运行配置，不能只做出 GUI preset 就宣称完成：
   - ansys-assistant 的 brd_reviewed_model_optimize_loop 使用 mission_process_harness，并非 live-session workflow；
   - MCP 的 AEDT_AGENT_WORKFLOW_DB 必须与 CLI/dashboard 的 $db 完全一致；
   - AEDT_AGENT_WORKFLOW_PROFILE 必须解析到 config/execution_profiles/local_real_aedt.json，而不能误用 safe-recorded；
   - MCP workflow start 仍要求 live_session_id 与 preview/approval；BRD 图的 live_session_id 只是宿主绑定，不代表 worker 改成 live backend；
   - 新增 workflow 恢复测试时，覆盖进程重启后持久化 mission/graph/DB 的状态，不假定内存中的 live-session binding 能跨重启保存。
5. 新增可重复执行的安装器，例如 scripts/dsh/install_ansys_dsh.py。
6. 安装器接口至少支持：

~~~powershell
python scripts/dsh/install_ansys_dsh.py check --repo-root <repo> --dsh-profile <profile-dir> --python <venv-python>
python scripts/dsh/install_ansys_dsh.py apply --repo-root <repo> --dsh-profile <profile-dir> --python <venv-python>
~~~

7. check 只读；apply 必须先备份，并且幂等。不得覆盖其他 MCP、preset、模型或 UI 配置。
8. 对 cordis.patch.yml 只管理带明确 begin/end marker 的 ansys-agent block；若发现我们之前手工添加的重复条目，先报告，不重复插入。YAML 中含 !!js 时不得用普通 YAML parser 重写整个文件。
9. 本机绝对路径只允许由安装参数生成到本机 DSH profile；仓库模板中不得再写死 C:\Users\z3312。
10. 安装后输出缺少的 Python 模块、版本、MCP 入口、preset 声明及“需重启 DSH”提示。不得自动关闭用户 AEDT 或 DSH。
11. 添加临时目录测试：首次安装、重复安装、已有用户配置保留、check 无副作用、错误路径失败、reviewed 不含官方裸代码 MCP、模板无本机用户名。

### 验收

- 在临时 profile 完成两次 apply，第二次内容不增加重复项。
- 用临时 SQLite DB 和 fake worker 证明 reviewed workflow 的 DB/profile 指向正确，不能落到默认 safe-recorded DB；真实 solve 留到 T4。
- 在真实 DSH profile 先运行 check；只有用户明确要求安装时才 apply。
- 重启后 GUI 同时出现 reviewed/explore；reviewed 的工具清单中没有官方 run_python_code、shell 和任意写入入口。
- 记录验收证据；不以 persona 中存在“禁止”字样代替工具面检查。

## 8. T3：离线验收、恢复语义与报告证据

### 修改范围

- src/aedt_agent/agent/、src/aedt_agent/reporting/（仅限恢复/证据缺口）
- tests/test_agent_loop_runner.py
- tests/test_agent_brd_progress_report_worker.py
- tests/test_brd_optimization_report.py
- tests/test_agent_optimization_handlers.py
- docs/remote-reviewed-model-loop.md
- docs/evidence/README.md 和执行记录模板。

### 必做验收

1. 全量测试：

~~~powershell
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
   - 关闭第一个 runtime，再用同一 SQLite DB 新建 runtime，恢复 graph 后不创建新 mission，也不重新执行 prepare_working_project/copy；
   - 同一 graph 的 DB 路径必须一致；dashboard 与 runner 使用同一个 $db；
   - MCP workflow 使用同一 AEDT_AGENT_WORKFLOW_DB，恢复状态与 CLI 一致。
4. 报告验证：
   - optimization_history.csv 包含每轮（含 partial/failed round）；
   - optimization_progress.html 与 optimization_progress.json 存在且结构可读；
   - 有 SDD11/SDD21 最差值和频率、TDR 峰值偏差、观察端口、orientation 是否已证实、objective cost、动作与审批/失败原因；
   - 有 artifact 路径和 SHA256；
   - 不把全曲线写进报告摘要或 LLM payload。
5. 恢复测试只使用 fixture/mock 或安全停止点；不得为测试强行 kill AEDT 求解进程或中断 geometry write。
6. 更新文档：DSH 是对话/审批入口；CLI worker 与 SQLite 才持有工程运行状态；恢复使用 graph_run_id，不是依赖聊天上下文。

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