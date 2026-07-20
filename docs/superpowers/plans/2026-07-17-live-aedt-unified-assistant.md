# Live AEDT Unified Assistant Implementation Plan

## 目标

在不改变现有 YAML Graph、BRD Worker 和 `aedt_agent.interactive` artifact 行为的前提下，
新增可连接并复用正在运行的 AEDT 会话的 live control plane，统一支持：

```text
发现 AEDT -> 显式选择 PID/port -> attach/reuse -> 识别 project/design type
  -> HFSS create/solve/status
  -> HFSS 3D Layout Path inventory/parameterize/verify
  -> release wrapper，不关闭 AEDT/project
```

## 非目标

- 不自动选择“最近”或“前台”AEDT 会话。
- 不提供任意 Python、COM 命令或脚本执行 MCP tool。
- 不默认保存 live 工程；内存修改和磁盘保存是两个风险动作。
- 不把新能力注册进现有 Workflow Runtime。
- 不删除或重命名现有 artifact MCP/CLI tools。

## 包边界

```text
src/aedt_agent/live/
  target.py          PID/port exclusive target
  protocol.py        strict JSONL request/response
  broker.py          per-target persistent worker registry
  worker.py          isolated PyAEDT process
  backend.py         Desktop/Hfss/Hfss3dLayout adapters
  discovery.py       process/listener discovery
  manager.py         live session handles and capability facade

src/aedt_agent/interactive/
  server.py          additive MCP registration only
  catalog.py         additive live capability metadata
```

## 协议与生命周期

1. 每个 request 带 UUID、command、target、arguments、positive timeout。
2. worker 必须每 request 输出且只输出一行 JSON response；PyAEDT stdout 重定向 stderr。
3. registry 对同一 PID/port alias 复用同一 broker，同 target 串行，不同 target 可并行。
4. attach 必须真实 ping；session id 只引用已 attach target。
5. release 清理 Hfss/Hfss3dLayout/Desktop wrappers，调用
   `release_desktop(close_projects=False, close_on_exit=False)`，不得退出 AEDT。
6. worker EOF、MCP shutdown、timeout 和 protocol error 都有确定性清理路径。

## Capability 设计

### Session

- `aedt.sessions.list`
- `aedt.sessions.attach`
- `aedt.sessions.release`
- `aedt.projects.info`

### HFSS

- `hfss.design.create`
- `hfss.analysis.start`
- `hfss.analysis.status`

### Live 3D Layout

- `layout.live.paths.list`
- `layout.live.path_width.preview`
- `layout.live.path_width.apply`

Live tool 使用 `live_session_id`；artifact tool 继续使用原 `session_id/project_path`，禁止用同一个
`open_layout_session` 暗示两种模式。

## Live Layout 事务

1. broker 使用 `Hfss3dLayout(project, design, aedt_process_id=.../port=...)` attach。
2. inventory 从 `modeler.line_names/modeler.lines` 读取 name、layer、net、width expression。
3. selector 与 artifact 共享单位、net/layer/name、parameterized 过滤语义。
4. preview 保存 project/design identity、目标属性和 digest，不修改设计。
5. apply 前重读 digest；创建 design variable 后把 `Line3dLayout.width` 设为变量表达式。
6. 回读 width expression；任一目标失败则恢复全部原表达式并删除新变量。
7. live apply 默认不 save，返回 `project_dirty=true`；save 后续作为独立高风险 capability。

## 审批

- 第一版 live apply 需要 preview id，并明确标记 `approval_required=true`。
- MCP 不提供“自行批准”tool。
- 未配置 Host 签名密钥或 verifier 时，live apply/save 固定返回 `approval_required`。
- apply 必须携带 host-signed、短期、绑定 action/session/preview/digest 且未使用过的 approval token。

## 错误模型

稳定错误码至少包括：

```text
invalid_target
target_not_found
attach_failed
session_not_found
wrong_design_type
stale_preview
approval_required
backend_timeout
protocol_error
backend_error
```

错误不得只返回 Python traceback；traceback 仅进入 stderr/audit detail。

## 实施步骤

- [x] 完成 CAE-Agent-Hub 与本项目源码审计及 DeepSeek benchmark。
- [x] 实现 target 和 strict worker protocol。
- [x] 实现 persistent broker registry、alias、timeout、release。
- [x] 实现 process/port discovery 和 live session manager。
- [x] 实现 Desktop project info。
- [x] 实现 HFSS design create、analysis start/status。
- [x] 实现 Hfss3dLayout Path inventory。
- [x] 实现 live preview/digest/apply/readback/rollback。
- [x] 实现 HMAC Host approval：绑定 action/session/preview/digest、短期有效、一次性使用，MCP 无签发入口。
- [x] 实现独立的 live project save preview/apply 与第二次批准边界。
- [x] 增加 additive MCP tools，并新增 v2 catalog；v1 输出保持不变。
- [x] 实现受控 launch、助手 owned-session 标记与命令行 gRPC port 识别。
- [x] 实现 HFSS setup/port/boundary/report 只读 inventory。
- [x] 实现 HFSS setup create preview/apply/readback/rollback 与独立 Host approval。
- [x] 实现 HFSS report create preview/apply/readback/rollback 与独立 Host approval。
- [x] 实现 geometry face inventory，以及 radiation/wave/lumped port 的显式 face selector、
  geometry digest、readback 和 rollback。
- [x] 实现 analysis start preview/apply、setup digest、资源预算和独立 Host approval；保留旧入口兼容。
- [x] 实现增强 status 和 analysis cancel preview/apply。
- [x] 实现受限目录 Touchstone/report CSV 导出、SHA-256 校验和 evidence manifest。
- [x] 增加 CLI diagnostics/attach smoke。
- [x] 增加单元、protocol、fake PyAEDT、MCP、真实 AEDT 测试。
- [x] 重跑现有 interactive、Agent/Worker 受影响范围测试及 Claude/DeepSeek benchmark。

## 当前执行记录

- MCP server 当前共 35 个 tools；v2 catalog 统一描述 live/artifact mode、risk、approval 和 postcondition。
- 本机 AEDT 2026 R1 的 launch/attach/reuse/release、HFSS create/status、3D Layout inventory 和
  参数化 readback 已通过真实验收；release 后 AEDT/工程保持运行，测试清理后无残留进程。
- live + artifact + benchmark 单元测试持续通过；单会话和双会话真实 AEDT opt-in test 均通过。
- 双会话真实验收确认两个 PID/port/工程完全隔离、同目标复用 broker、release 后进程仍存活；
  测试结束后仅关闭测试自建进程且无残留。
- Agent/Worker/PyAEDT adapter 兼容回归：`59 passed, 1 skipped`。
- DeepSeek V4 Flash 升级后产品任务覆盖 `8/8`，单轮 11 任务编排均值 `95.5`。
- 加入 MCP server 安全指令后，源覆盖对抗任务聚焦复测 `3/3` 在工具调用前正确拒绝。
- 新增 launch 与 HFSS inventory 的双候选聚焦 benchmark：ours `2/2` 完成；Hub 完成 launch，
  对 inventory 正确 blocked；四个 case 编排均为 `100`。
- 本机真实 AEDT 已跑通 geometry inventory → 双 wave port → setup → report → 3D Layout 参数化；
  每个写步骤使用独立的一次性批准令牌，默认不保存工程。
- HFSS setup、wave port、report 三项新增双候选 benchmark：ours `3/3` 完成，Hub `3/3`
  正确 blocked；六个 case 编排均为 `100`。
- 求解控制聚焦 benchmark：ours 的 approved start/status、cancel、Touchstone evidence 均完成且复测为
  `100`；Hub 的 start/status 完成，cancel/export 因缺少工具正确 blocked，编排均为 `100`。
- benchmark runner 会识别 Claude 初始化时 candidate MCP 仍为 `pending` 的基础设施瞬态并重试一次，
  不再把 MCP 启动竞态误计为产品能力失败。
- Live apply/save 在默认配置下固定返回 `approval_required`，不会把 preview 当作用户批准。

## 验收矩阵

1. 两个 AEDT 同时运行时必须返回两个 target，缺少选择时拒绝执行。
2. 同一 target 连续 5 次调用只创建一个 broker。
3. PID 和 probe 返回的 port 必须 alias 到同一 broker。
4. release 后 AEDT PID、窗口、project 都仍存在。
5. HFSS create/start/status 在指定 project/design/setup 上执行，不使用 active fallback。
6. live Layout inventory 对 ground truth 的 precision/recall 都为 100%。
7. 参数化仅命中 selector 对象，回读全部引用变量；stale preview 必须拒绝。
8. apply 失败恢复原表达式；默认不 save；独立 save preview/apply 必须再次批准。
9. 未批准 apply 必须返回 `approval_required`，不得产生工作副本或 live 内存副作用。
10. 原 artifact CLI/MCP、YAML Graph 和 BRD Worker 回归保持通过。

## 交付顺序

- **Milestone A**：session attach/reuse/project info，可独立发布。
- **Milestone B**：live HFSS create/solve/status。
- **Milestone C**：live 3D Layout inventory。
- **Milestone D**：带 Host approval 的 live parameterization。
- **Milestone E**：真实 AEDT 双会话 benchmark 和稳定性报告。

Milestone A-E 已完成；重复统计、更多 HFSS 写 capability 和长期 soak 继续作为硬化项。
