# Interactive Ansys Assistant Implementation Plan

## 目标

在不改变现有 YAML Graph、BRD Worker 和受控优化闭环行为的前提下，新增一套可由
LLM/Codex/Claude 通过 MCP 或 CLI 调用的通用 Ansys 交互能力。

第一条可验收纵向切片：

```text
打开 HFSS 3D Layout 项目或 AEDB
  -> 枚举所有 Path/trace
  -> 按线宽、net、layer、primitive id 精确筛选
  -> 预览参数化变更
  -> 在自动创建的工作副本中把线宽绑定到参数变量
  -> 回读表达式和 primitive 参数化状态
  -> 保存、输出结构化证据或失败后回滚
```

## 兼容性边界

- 新能力放在独立的 `aedt_agent.interactive` 包中。
- 不修改已有 graph template、mission state machine 或 BRD capability 的语义。
- 不把通用交互能力默认注册到现有 Workflow Runtime。
- 新增独立 `ansys-assistant` 和 `ansys-assistant-mcp` 入口。
- 读取操作可以直接打开源 AEDB；写操作必须使用自动创建的工作副本。
- 第一阶段不提供覆盖源工程或任意 Python 执行能力。
- PyEDB/PyAEDT 采用延迟 import，不安装 Ansys 依赖时仍可导入和测试其他模块。

## 架构

```text
Host Agent / CLI
       |
       v
Capability Catalog  ---- schema/risk/postcondition ----+
       |                                                |
       v                                                v
Interactive Kernel ----------------------------> Audit Result
       |
       v
Layout Session Manager
       |
       +--> read-only source session
       |
       +--> writable working-copy session
                    |
                    v
               PyEDB adapter
```

MCP stdio server 使用独立 PyEDB worker process，并通过专用 IPC 通道传递结构化结果。这样
PyEDB 及其 gRPC 子进程的 stdout 不会污染 MCP 协议流；CLI 则继续使用进程内 manager。

### Capability 层

首批稳定能力：

| Capability | 风险 | 用途 |
| --- | --- | --- |
| `layout.paths.list` | read_only | 枚举和筛选走线 |
| `layout.path_width.parameterize.preview` | read_only | 固定目标集合并生成 preview digest |
| `layout.path_width.parameterize.apply` | reversible_edit | 在工作副本创建参数并修改线宽 |

Capability Catalog 面向 LLM 暴露名称、说明、JSON Schema、风险等级和后置条件；执行器仍然是
确定性 Python，不让 LLM 猜测底层 PyEDB API。

### 会话和工作副本

- 输入支持 `.aedt` 加同名 `.aedb` sidecar，或直接输入 `.aedb`。
- 只读会话也使用临时快照副本并设置 `isreadonly=True`，关闭时删除快照，避免 EDB lock/tmp 文件触碰源目录。
- 可写会话先复制 `.aedt` 和 `.aedb` 到独立 workspace，再打开副本。
- Session 保存源路径、工作路径、AEDT 版本、EDB backend 和未完成 preview。
- 关闭 Session 时始终关闭 EDB；写入成功时显式 `save()`。

### 对象解析

Path 使用 `primitive_id + layer + net + snapshot digest` 标识。筛选器支持：

- 目标线宽和显式容差，统一换算成米比较。
- net、layer 和 primitive id 白名单。
- 已参数化/未参数化状态。

Apply 必须携带 Preview 返回的 ID。执行前重新枚举并校验 digest，避免工程状态变化后修改旧对象。

### 参数化和验证

- 新变量默认为 design parameter，不允许静默复用同名但非参数变量。
- gRPC backend 通过 active cell variable server 创建 `is_param=True` 变量。
- .NET backend 使用 `add_design_variable(..., is_parameter=True)`。
- Path 宽度写入变量表达式，而不是写入变量当前数值。
- 回读必须同时证明目标 primitive 数量未变化、表达式引用变量、primitive 报告参数化。
- 任一步失败时恢复原线宽并删除本次新建变量；源工程始终不受影响。

## 实施步骤

- [x] 审计现有 Runtime、Worker、Action、MCP 和本地 PyEDB 0.77.0 API。
- [x] 新增交互任务合约、Capability Catalog 和 JSON Schema 输出。
- [x] 新增 Layout Session Manager 和工作副本策略。
- [x] 新增 Path inventory、单位解析、筛选和稳定 preview digest。
- [x] 新增参数变量创建、线宽表达式修改、回读验证和失败回滚。
- [x] 新增 JSON CLI，支持 capabilities、inspect-layout、parameterize-width。
- [x] 新增独立 FastMCP server，暴露会话与三项 layout capabilities。
- [x] 新增 fake EDB 单元测试、CLI 测试、MCP 契约测试和导入隔离测试。
- [x] 运行已有 Agent/Worker 回归测试和全量测试。受影响范围回归为 49 passed、1 skipped；
  全量为 1016 passed、4 skipped、10 failed，10 项失败均位于本次未修改的既有模块或缺失的
  benchmark fixture，未据此改动现有功能。
- [x] 使用本机 PyEDB 0.77.0 gRPC 创建最小真实 AEDB，完成 CLI 和 MCP open/list/preview/apply/close smoke。

## 可用状态验收

必须同时满足：

1. `ansys-assistant capabilities` 输出机器可读的能力与 schema。
2. `inspect-layout` 能从 fake 和真实 PyEDB backend 返回结构化 Path inventory。
3. `parameterize-width` 默认只预览，只有显式 `--apply` 才修改工作副本。
4. Apply 后回读证据能证明目标宽度引用指定参数变量。
5. 源 `.aedt/.aedb` 内容在成功和失败路径都不改变。
6. MCP server 可以完成 open/list/preview/apply/close 的完整调用链。
7. 新增测试通过，受影响范围的现有测试无行为回归；全量测试中的既有失败单独记录，不为
   追求全绿而修改无关模块。

## 验证记录

- 新增 fake/CLI/MCP/导入隔离测试：`14 passed`。
- 真实 PyEDB 0.77.0 gRPC worker 冒烟：`1 passed`。
- Agent、Worker、PyAEDT adapter 兼容回归：`49 passed, 1 skipped`。
- 实际 MCP stdio 完整链路已验证 open/list/preview/apply/close，回读表达式为 `trace_w`，
  `source_unchanged=true`。
- `git diff --check` 无空白错误；当前环境未安装 Ruff，因此未运行 Ruff。

## 后续阶段

纵向切片稳定后，按同一合约增加变量管理、对象属性查询、端口、边界、setup、report 和 solve
能力。Catalog 未覆盖的长尾任务再接入受限 Code Agent；成功脚本经过测试后提升为 Capability，
而不是让自由脚本成为默认执行路径。
