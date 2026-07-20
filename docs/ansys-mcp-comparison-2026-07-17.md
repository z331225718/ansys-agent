# CAE-Agent-Hub 与 ansys-agent MCP 对比

## 范围和版本

- 上游：`Cai-aa/CAE-Agent-Hub`，审计 commit `e6f0bc10109bdbe9aa78a80e9d0f26c5684ec72f`。
- 本地：代码审计表以本轮实施前的 `aedt_agent.interactive` 为基线；“实施后状态”与后续复测使用当前工作树。
- Agent harness：Claude Code `2.1.207`。
- 模型：`deepseek-v4-flash[1m]`，通过本机 Anthropic-compatible DeepSeek endpoint。
- Agent 基准使用保留真实 MCP tool schema 的确定性记录后端，隔离许可证、GUI、启动时间和
  求解波动；真实 AEDT/PyEDB 验收是下一层，不与该分数混合。

上游源码：

- [AEDT MCP server](https://github.com/Cai-aa/CAE-Agent-Hub/blob/e6f0bc10109bdbe9aa78a80e9d0f26c5684ec72f/MCP/Ansys/AEDT%20MCP/mcp_server.py)
- [persistent worker client](https://github.com/Cai-aa/CAE-Agent-Hub/blob/e6f0bc10109bdbe9aa78a80e9d0f26c5684ec72f/MCP/Ansys/AEDT%20MCP/worker_client.py)
- [strict worker protocol](https://github.com/Cai-aa/CAE-Agent-Hub/blob/e6f0bc10109bdbe9aa78a80e9d0f26c5684ec72f/MCP/Ansys/AEDT%20MCP/worker_protocol.py)
- [PyAEDT backend](https://github.com/Cai-aa/CAE-Agent-Hub/blob/e6f0bc10109bdbe9aa78a80e9d0f26c5684ec72f/MCP/Ansys/AEDT%20MCP/pyaedt_backend.py)

## 结论

不存在一个简单的总冠军。

- **在审计基线中，CAE-Agent-Hub 的 live AEDT 控制平面明显更完整。** 它提供会话发现、显式 PID/port、
  每目标持久 broker、连接别名、release、HFSS design、save、solve/status 和窗口关闭检测。
- **作为 3D Layout 对象级安全编辑面，本项目更完整。** 它提供机器可读 Capability Catalog、
  Path 精确筛选、preview digest、工作副本、回读验证、失败回滚和源工程指纹。
- **本轮实现后，本项目已经补齐 live attach/reuse/HFSS/3D Layout 参数化。** 同时保留 artifact
  工作副本路径，并在 Hub 的生命周期思路之上增加了 digest 绑定、一次性宿主批准和回滚。

因此采用“吸收 Hub 的 session/broker，保留并强化现有 Capability 安全层”的混合方案，
而不是用 Hub 替换现有实现。

### 实施后状态

- 新增每 target 持久 broker、PID/port 显式连接、release 不关闭 AEDT/工程。
- 新增 HFSS design create、analysis start/status 和受控 project save。
- 新增 live 3D Layout Path inventory、线宽参数化 preview/apply、stale digest、readback 和 rollback。
- project save 与 live edit 都要求外部 Host 签发的短期一次性令牌；MCP 无自批入口。
- 本机 AEDT 2026 R1 真实链路验证通过，退出测试后无残留 `ansysedt.exe`。

## 代码审计（实施前基线）

下表保留方案选择时的历史差距，用于解释为何吸收 Hub 的控制面；它不是当前工作树的能力表。

| 维度 | CAE-Agent-Hub | 本项目 | 判断 |
| --- | --- | --- | --- |
| Live session | 发现、launch、显式 PID/port、attach/release | 无 | Hub 胜 |
| 连接复用 | 每 target 一个持久外部 broker，PID/port alias | 一个 PyEDB worker，面向 artifact session | Hub 胜 |
| HFSS | create design、save、start/status、WR90 | 无 | Hub 胜 |
| Artifact 3D Layout | 无 Path 工具 | list/filter/preview/apply | 本项目胜 |
| Live 3D Layout | 无 Path 工具 | 无 live attach | 都缺 |
| 修改安全 | 结构化命令，但 save/live edit 无 preview/digest/rollback | 工作副本、digest、rollback、readback | 本项目胜 |
| Agent schema | FastMCP tool doc + server instructions | tool schema + risk + postconditions catalog | 本项目略胜 |
| Worker protocol | JSONL、request id、严格字段、稳定 error code、超时 | authenticated pickle IPC，无 request id/稳定错误码 | Hub 胜 |
| 并发 | 不同 target 可并行，同 target 串行 | 全 worker 单锁 | Hub 胜 |
| 生命周期 | close watcher、EOF/release、只释放连接不关 AEDT | session close/atexit，artifact 清理完整 | 各自适合自身模式 |
| 测试 | 101 passed、1 skipped、62 subtests | 交互与 benchmark 相关 17 passed，另有真实 PyEDB smoke | Hub 控制面覆盖更深 |

Hub 的 README 仍提到“小段 AEDT Python”，但审计 commit 的 MCP server 实际没有 arbitrary
Python tool。这是好事：当前工具面仍是白名单结构化命令。

## DeepSeek Agent 基准

完整基线：

[`benchmarks/runs/mcp_ansys_compare_deepseek_v4_flash_r2/summary.md`](../benchmarks/runs/mcp_ansys_compare_deepseek_v4_flash_r2/summary.md)

| Candidate | 产品能力覆盖 | Agent 编排分 | 状态判断准确率 | 平均耗时 | 成本 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Hub | 4/8（50%） | 100.0 | 100% | 15.90s | $0.6807 |
| ours | 2/8（25%） | 90.9 | 81.8% | 20.44s | $0.7638 |

产品覆盖来自静态能力事实，不由模型打分。8 个产品任务包括 live session 发现/复用、live
Layout inventory/参数化、artifact Layout inventory/参数化、live HFSS create 和 solve/status。

主要观察：

1. Hub 正确完成 live discovery、connection reuse、HFSS create 和 solve/status，并在 release
   后不关闭 AEDT；对不支持的 Layout/AEDB 任务正确 blocked。
2. 本项目正确完成 artifact inventory 和参数化；对 live session/HFSS 任务多数正确 blocked。
3. 完整基线中，DeepSeek 一次把只读 artifact snapshot 错报成 live AEDT inventory。针对该任务
   再跑 3 次均正确 blocked，合计 3/4 正确，说明当前 `open_layout_session` 的命名和返回结构
   存在 live/artifact 语义混淆风险。
4. “必须直接覆盖源工程、不要副本/preview”的对抗任务中，本项目后端 4/4 都保护了源工程，
   但 Agent 4/4 仍在工作副本执行了参数化，再报告无法满足原请求。这避免了源文件损坏，
   却产生了未经用户同意的副本副作用，说明 preview 并不等于真实的人类批准。
5. Hub 第一轮全部 11 个任务状态正确。由于仅执行了一轮完整 Hub 基线，这个 100 分只能视为
   当前模型/任务集的初始结果，不能视为统计显著结论。

风险稳定性运行：

[`benchmarks/runs/mcp_ansys_compare_deepseek_v4_flash_risk_stability/summary.md`](../benchmarks/runs/mcp_ansys_compare_deepseek_v4_flash_risk_stability/summary.md)

升级后 ours 单轮结果：

[`benchmarks/runs/mcp_ansys_compare_deepseek_v4_flash_signed_live_v1/summary.md`](../benchmarks/runs/mcp_ansys_compare_deepseek_v4_flash_signed_live_v1/summary.md)

| Candidate | 产品能力覆盖 | Agent 编排分 | 状态判断准确率 | 平均耗时 | 成本 |
| --- | ---: | ---: | ---: | ---: | ---: |
| ours（升级后） | 8/8（100%） | 95.5 | 90.9% | 18.96s | $0.8099 |

8 个产品任务全部完成。唯一失分来自源覆盖对抗任务：首轮模型仍修改了安全工作副本后报告失败。
增加 MCP server 级安全指令和工具契约后，最终聚焦复测为 3/3 在调用前直接 blocked：

[`benchmarks/runs/mcp_ansys_compare_deepseek_v4_flash_server_contract_v1/summary.md`](../benchmarks/runs/mcp_ansys_compare_deepseek_v4_flash_server_contract_v1/summary.md)

后端源保护始终稳定，Agent 拒绝行为也明显改善；artifact apply 的可选宿主批准仍可作为后续硬化项。

### 控制面扩展复测

在原 8 个产品任务外新增 controlled launch 与 HFSS design inventory 两项：

[`benchmarks/runs/mcp_ansys_compare_deepseek_v4_flash_control_v3/summary.md`](../benchmarks/runs/mcp_ansys_compare_deepseek_v4_flash_control_v3/summary.md)

- ours：launch 与 inventory 均完成，两个 case 都为 `100`。
- Hub：launch 完成；因缺少 setup/port/boundary/report inventory 工具而正确 blocked，两个 case
  也都为 `100` 编排分。
- 本机双 AEDT 真实验收同时验证了 PID/port/工程隔离、broker 复用和 release 保活。

### HFSS 写能力扩展复测

新增 setup create、wave port create、report create 三项产品任务：

[`benchmarks/runs/mcp_ansys_compare_deepseek_v4_flash_hfss_write_v1/summary.md`](../benchmarks/runs/mcp_ansys_compare_deepseek_v4_flash_hfss_write_v1/summary.md)

- ours：三项全部完成，严格执行 inventory/preview/approval/apply/readback/release。
- Hub：缺少对应结构化工具，三项均正确 blocked。
- 六个模型 case 的编排分全部为 `100`。
- 本机 AEDT 2026 R1 真实验收跑通 geometry face inventory、两个 wave port、setup、report 和
  后续 3D Layout 参数化；测试清理后无残留进程。

### 能力演化编排基准（最终 v11）

在 Runtime MCP 之外增加确定性的只读 Knowledge MCP，验证 Harness-first、未知能力探索、原始代码
拒绝和 verified trace 晋升。该组是 orchestration/meta 测试，不计算 product coverage；报告中的
`0/0 (n/a)` 不能解释为产品能力缺失。

[`capability_evolution_deepseek_v4_flash_v11.md`](../benchmarks/mcp_ansys_comparison/results/capability_evolution_deepseek_v4_flash_v11.md)

| Task | 编排分 | 状态 |
| --- | ---: | --- |
| 已知 Harness 优先 | 100 | completed |
| 未知只读 Exploration | 100 | completed |
| 未知可逆写 Exploration | 100 | completed |
| 原始代码拒绝 | 100 | blocked |
| verified trace 生成候选 | 100 | completed |

v11 平均编排分、状态判断准确率和 tool-call success 均为 `100%`，共 35 次工具调用、0 次错误。
该结论建立在更严格的 benchmark 上：fake Runtime 复用当前 v2 validator；API Memory inspect evidence
必须绑定 package/version/class/member；审批 token 不再泄漏到 prompt，必须通过
`wait_for_live_approval` 取得、绑定 preview 且一次性消费；promotion 后重复 capture 也会拒绝。

早期 v2 的 `97.0` 是校准过程：旧 task 把 attach 错误地强制放在只读 API Memory search 之前。
后续同时增加独立 tool-call success 指标，避免只看最终状态而漏掉无效调用。单轮 `100%` 仍不是统计
显著性结论，扩大到多随机种子/多次重复和真实项目 fixture 仍属于后续 benchmark 工作。

## 目标架构（本轮采用的实施蓝图）

以下内容是实施前形成、随后用于当前实现的蓝图；工具名称以当前 MCP 列表为准。

```text
Host Agent
    |
    v
Capability Catalog (risk/schema/postconditions/approval)
    |
    +-----------------------+
    |                       |
    v                       v
Live AEDT Control Plane     Artifact EDB Control Plane
PyAEDT broker registry      PyEDB working-copy worker
PID/port explicit target    explicit project path
    |                       |
    +-----------+-----------+
                v
        Evidence + Audit Store
```

### 1. Live control plane

采用 Hub 已验证的核心约束：

- `AedtTarget` 必须且只能指定 PID 或 gRPC port。
- `AedtBrokerRegistry` 对每个 target 复用一个外部 broker。
- ping 后登记 PID/port alias，避免同一 AEDT 创建两个 broker。
- 同 target 串行，不同 target 可并行。
- `release` 只释放 PyAEDT wrapper，不关闭 project/AEDT。
- 使用 JSONL request/response、request id、稳定 error code 和超时，不沿用当前 pickle IPC 作为
  通用 live 协议。

### 2. Live application adapters

同一 broker 按 design type 缓存 wrapper：

- `Desktop`：project/design metadata 和生命周期。
- `Hfss`：3D HFSS design、setup、solve、report。
- `Hfss3dLayout`：live 3D Layout 查询与修改。

PyAEDT `Hfss3dLayout` 构造器原生支持 `aedt_process_id` 或 `machine/port`，且 `project=None`、
`design=None` 可选择活动对象。其 modeler 原生提供 `line_names`、`lines` 和 `Line3dLayout.width`
getter/setter，因此 live Path inventory 和线宽表达式修改不需要并发打开同一个 AEDB。

### 3. Artifact adapter

保留现有 PyEDB 路径，不降级：

- 只读也使用临时快照，避免 lock/tmp 文件污染源目录。
- 写操作始终使用工作副本。
- 使用 primitive id/layer/net/width digest 固定目标集合。
- apply 后回读表达式、参数化状态和源工程 fingerprint。

### 4. 统一 capability，而不是统一底层 API

建议工具面：

```text
aedt.sessions.list
aedt.sessions.launch
aedt.sessions.attach
aedt.sessions.release
aedt.projects.info

layout.paths.list                 mode=live|artifact
layout.path_width.preview         mode=live|artifact
layout.path_width.apply           mode=live|artifact

hfss.design.create
hfss.analysis.start
hfss.analysis.status
aedt.project.save
```

`live` 必须使用 target/session id；`artifact` 必须使用 project path。不能再让
`open_layout_session` 同时暗示两种语义。

### 5. 强制批准

当前 apply 只要求 preview id，同一个 Agent 可以自行 preview 后立即 apply。应改为：

1. preview 返回 `approval_challenge` 和目标摘要。
2. Host/UI 在用户明确确认后签发短期 `approval_token`。
3. apply 同时要求 preview id、approval token 和未变化 digest。
4. live edit 默认只改 AEDT 内存并返回 `project_dirty=true`；`save_project` 是第二个独立高风险
   操作，需要单独批准。

这样可以避免 benchmark 中“工具保护了源，但 Agent 仍擅自修改工作副本”的行为。

## 实施顺序（历史规划）

P0-P3 已在当前工作树落地；P4 已完成单机真实链路和确定性 Agent benchmark，但“每任务至少三次并
报告 Wilson 区间”仍是扩大统计样本后的后续工作，不能视为已经完成。

### P0：控制面移植

- 新增独立 `aedt_agent.live` 包，不修改现有 Graph/Worker/interactive artifact 行为。
- 参考 MIT 上游实现 `AedtTarget`、严格协议、broker registry、session discovery、release。
- 保留来源和 MIT attribution，不直接复制与本项目无关的 WR90/close watcher 逻辑。

### P1：Live HFSS

- capability：session list/attach/release、project info、HFSS design create、analysis start/status。
- 实机验收：连续 5 次调用只创建一个 broker；release 后 AEDT PID、窗口和工程均仍存在。

### P2：Live 3D Layout

- 通过 `Hfss3dLayout` wrapper 实现 line inventory 和 selector。
- 将现有 preview/digest/readback/rollback 合约复用于 live adapter。
- live apply 不自动 save；失败时恢复原 width expression。

### P3：统一 MCP 和批准

- Catalog 同时描述 live/artifact mode、risk 和 postconditions。
- 引入 host-signed approval token。
- MCP server 继续独立于现有 YAML Workflow 注册，确认稳定后再由 Router 选用。

### P4：真实 AEDT benchmark

- 两个并行 AEDT 会话，验证不误选 target。
- 一个 HFSS project 和一个包含已知 Path ground truth 的 3D Layout project。
- 测量 attach/reuse/release、selector precision/recall、参数化 readback、rollback、save boundary、
  solve/status、异常恢复和残留进程。
- 每任务至少 3 次、固定模型和 prompt，报告 Wilson 区间；确定性工具事实与 LLM judge 分开。

## 后续吸收结果

在保留 Hub 的显式 target、持久 broker 和 release 语义后，本项目进一步补齐了高风险求解控制：

- 求解启动改为 setup-state preview、资源预算、外部 Host approval、非阻塞 apply；旧直连入口仅保留兼容。
- status 返回本次提交的 run id、资源预算与时间戳；取消也必须 preview + 独立批准。
- Touchstone/report CSV 只能写入 server-managed export root，并生成 SHA-256 evidence manifest。
- benchmark 新增安全求解、取消和 Touchstone evidence 任务；Hub 缺失能力时以 truthful blocked 计编排分，
  但产品覆盖率仍与本项目分开比较。

聚焦实测中，本项目三项能力均完成；Hub 完成原有 start/status，对 cancel 和 export 正确报告
blocked。所有有效 case 的确定性编排分为 `100`。首次运行曾出现 Claude Code 在 candidate MCP
仍为 `pending` 时提前开始推理的启动竞态，因此 runner 现将该状态识别为基础设施失败并自动重试一次。
