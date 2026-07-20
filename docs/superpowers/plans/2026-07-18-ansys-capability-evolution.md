# Ansys Capability Evolution Implementation Plan

设计基线：[`docs/ansys-capability-evolution.md`](../../ansys-capability-evolution.md)

## Phase 0：架构与基线

- [x] 固化 Harness、Skill、Workflow、API Memory、Exploration、Promotion 的职责边界。
- [x] 定义知识证据、operation plan、trace 状态机和 promotion review 治理边界。
- [x] 明确不执行原始 Python、不热更新 Harness、不改变现有 Workflow 的安全不变量。
- [x] 记录当前 interactive/live/Desktop 和 Agent/Worker 回归基线。

## Phase 1：API Memory

- [x] 定位当前 interpreter 的 PyAEDT/PyEDB distribution、版本和源码根目录。
- [x] 生成带源码 inventory digest 的本地 manifest。
- [x] 增加 codebase-memory-mcp optional dependency、管理 CLI 和独立缓存目录。
- [x] 实现 prepare/status 和 `prepare --force` 重建，版本或 digest 变化时标记 stale。
- [x] 实现只读 facade：search、inspect、trace、source search、example search。
- [x] 建立独立 `ansys-api-memory` FastMCP server，不暴露索引和删除接口。
- [x] 使用当前本机 PyAEDT/PyEDB 真正建图并执行查询验收。

## Phase 2：受控 Exploration

- [x] 定义并严格解析 `ansys-operation-plan/v1`。
- [x] 实现 public-path、JSON argument、step 数量和返回大小限制。
- [x] 实现危险方法、私有属性、文件/网络/进程、save/close/solve 禁止规则。
- [x] 要求所有调用绑定版本化知识 evidence；写操作要求 readback 和 server-snapshot rollback。
- [x] 在 live broker 增加只读 preflight、preview freeze、apply、readback 和 rollback command。
- [x] 在 LiveAedtSessionManager 接入 project/port/design 限制和 Desktop approval。
- [x] 在 MCP 注册 propose/validate/preview/apply tools；capture 由 Phase 3 trace store 提供。

## Phase 3：Capability Trace

- [x] 实现 append-only hash-chain trace store、终态 seal、server-held HMAC 认证和不可逆状态迁移校验。
- [x] 保存 intent、plan、evidence、版本、target、preview/readback/rollback 和稳定错误码。
- [x] 对 token、secret、环境敏感值做 schema 级排除和导出脱敏。
- [x] 实现 trace get/list/export、完整性校验和返回限制。

## Phase 4：Promotion

- [x] 实现 Harness/Skill/Workflow 分类规则和人工 override；单-plan trace 缺少循环信号时由 reviewer 显式选择 Workflow。
- [x] 从 verified trace 生成参数化候选 manifest、报告、代码、测试和 unified diff。
- [x] 拒绝未验证/被篡改 trace，并移除 target、本机路径和凭据硬编码。
- [x] 新增 `.agents/skills/ansys-capability-promoter/SKILL.md` 和 review checklist。
- [x] 候选仅写入 `.aedt-agent/capability-candidates`，不应用、不 commit、不热加载。

## Phase 5：Desktop 与路由集成

- [x] Desktop session MCP config 同时加载 `ansys-assistant` 和 `ansys-api-memory`。
- [x] System context 明确 Harness 优先、unknown fallback 顺序和禁止绕过。
- [x] 缺少/陈旧知识图时保留已有工具，未知 fallback 明确关闭。
- [x] Router 仅在 API Memory ready 且 Exploration policy enabled 时返回 `code_fallback`。
- [x] PowerShell finally 关闭 approval Host；Runtime/Knowledge 由 Claude MCP 生命周期托管。

## Phase 6：测试与 Benchmark

- [x] source locator、manifest、CLI adapter、evidence replay 和只读 MCP 单元测试。
- [x] operation plan typed schema 与 validator 允许/拒绝矩阵。
- [x] fake PyAEDT preflight/apply/readback/rollback、stale preview 和 target 隔离测试。
- [x] trace state machine、脱敏、hash chain、HMAC 防重签和持久化测试。
- [x] promotion 生成、拒绝门和 patch 快照测试。
- [x] Claude Code/DeepSeek v4 flash v11 benchmark：五项编排均 `100`，35 次工具调用 0 错误。
- [x] 本机 PyAEDT/PyEDB 图查询、真实 AEDT 只读 probe 和一次性工程写验收。
- [x] 重跑 interactive/live/Desktop、Agent/Worker、YAML Graph 和 BRD solve 目标兼容回归：`248 passed, 4 skipped`。
- [x] 审计全量测试：`1092 passed, 7 skipped, 11 failed`；10 项为与本次能力演化无关的既有失败，另 1 项 fan-out 顺序相关失败单独复跑通过。

## 完成定义

只有以下证据同时成立才能标记完成：

1. 两个版本化知识图可查询，且结果包含与当前安装版本匹配的源码证据。
2. 未知操作无法绕过声明式 plan、preview、审批、readback 和 rollback。
3. verified trace 能生成候选 patch，但运行中的 Harness 和仓库源码没有被自动修改。
4. Desktop 一键入口加载双 MCP，退出后无残留进程。
5. 本次变更覆盖的兼容测试、benchmark 和本机 acceptance 通过，文档与实际工具列表一致；全量测试中的既有无关失败已单独记录，不能误报为全量全绿。

## 回归记录

- 目标兼容集合：`248 passed, 4 skipped in 36.69s`。
- 全量集合：`1092 passed, 7 skipped, 11 failed in 129.28s`。
- 10 项残余集中在既有 action 判定、Windows path 表达、缺失旧 benchmark fixture、Stage B path
  scrubbing 和 Stage C demo request；本轮新增 API Memory、Exploration、Trace、Promotion、Desktop
  与 benchmark 覆盖未出现失败。
- 额外的 `test_fan_out_with_join_all_converges` 只在全量顺序中失败，随后单独复跑 `1 passed`；作为
  既有全局状态/顺序依赖风险记录，不把它误算成本轮能力演化回归。
