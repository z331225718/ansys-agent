# AEDT Agent Stage C.1 Demo/API 设计

## 目标

Stage C.1 的目标是把当前 Stage C 的产品骨架变成一个小而完整、可以对外演示的产品闭环。它不是完整节点编辑器，而是一个本地 Web Demo 加一层小型 HTTP API，让其他团队可以看到 agent 架构、查看节点和模板、规划 workflow、校验 workflow、运行受控 demo，并打开生成的报告。

## 当前基础

Stage C 已经具备以下后端能力：

- 节点 catalog：包含 metadata、schema、version 和 UI hints。
- Workflow JSON 模型、validator、executor 和 templates。
- Fake adapter 和真实 PyAEDT adapter。
- Inspector 与模型事实 validation rules。
- 确定性 chat planner 骨架。
- Node evolution miner、proposer、evaluator。
- 中文 HTML 报告和 3 个真实 AEDT smoke artifact。

Stage C.1 必须复用这些能力，不另起一套执行链路。

## 面向用户

Stage C.1 面向三类人：

1. 不熟悉 Ansys 仿真的其他团队：需要快速理解这个 agent 在做什么。
2. 仿真工程师：需要先看到节点、模板和执行 artifact，再判断 workflow 模型是否可信。
3. 后续开发者：需要一个稳定 API 和 demo 边界，继续开发真正的 UI、聊天入口和节点进化审核。

## 产品形态

Stage C.1 提供一个轻量本地 Web App，后面接一个小型 HTTP API。

第一个页面应该是实际 demo workspace，而不是宣传 landing page。页面需要展示：

- 当前 Stage C 状态指标。
- 节点 catalog 摘要。
- Workflow template 列表。
- 当前选中的 workflow 预览。
- Validation 结果。
- Run 结果和 artifact 链接。
- 现有真实 AEDT smoke dashboard。
- 节点进化 proposal review 链接。

UI 需要能完成演示闭环，但不做完整拖拽式节点图编辑器。

## API 边界

API 保持小而稳定：

- `GET /api/status`
  - 返回 demo 状态、可测试能力、报告链接。
- `GET /api/nodes`
  - 返回序列化后的 node catalog。
- `GET /api/templates`
  - 返回 template 摘要列表。
- `GET /api/templates/{template_id}`
  - 返回单个 template 和完整 workflow JSON。
- `POST /api/plan`
  - 输入：用户自然语言请求和可选参数。
  - 输出：planner 结果，包括 selected template 或 generated workflow、missing information、assumptions、confidence、validation errors。
- `POST /api/validate`
  - 输入：workflow JSON。
  - 输出：workflow validation 结果。
- `POST /api/run`
  - 输入：workflow JSON，或 template id 加参数。
  - 输出：fake adapter run artifact 路径和 validation summary。
- `GET /api/reports`
  - 返回 Stage C report、smoke dashboard、demo index、node evolution review 链接。

API 应直接调用已有 Python 模块。除非某个报告生成脚本已经是唯一受支持的 artifact builder，否则不要通过 shell 调脚本。

## 执行策略

Web Demo 默认只使用 fake adapter。真实 AEDT 执行仍然通过显式 CLI 运行，因为它慢、依赖本机环境和 license，也更容易受进程状态影响。

Stage C.1 中：

- `POST /api/run` 只跑 fake adapter。
- 页面展示已有真实 AEDT smoke artifact。
- 页面可以展示真实 smoke 的 CLI 命令，但本 milestone 不从浏览器启动 AEDT。

这样 demo 响应快，也不会把 AEDT license 或启动失败隐藏在一个网页按钮后面。

## LLM Planner 边界

Stage C.1 需要增加可替换 planner 边界，但第一版默认仍可使用确定性 planner。

Planner 模式：

- `deterministic`：使用当前本地 template selection / workflow generation 逻辑。
- `llm`：可选，通过 local config 或环境变量配置，配置文件不能提交真实密钥。

LLM 模式必须遵守 Stage C 的核心规则：

- LLM 输出 planner decision 或 workflow JSON。
- LLM 输出必须先经过 validator。
- LLM 不能输出可直接执行的 PyAEDT Python。
- Validation 失败时返回 repair context，不启动执行。

如果 API credential 缺失，系统应回退到 deterministic 模式，或返回明确配置错误，但不能破坏 demo 的基础能力。

## 配置

使用一个可提交的 example config 和一个被 ignore 的 local config：

- 提交：`config/demo_config.example.json`
- 本地使用：`config/demo_config.local.json`

Example config 保留空字段：

- planner provider
- model
- base_url
- api_key
- default adapter

不得提交真实 key、base URL、token 或本机绝对路径。

## 报告和 Artifact

Stage C.1 延续当前报告策略：

- 生成报告放在 `benchmarks/reports/`。
- Demo run artifact 放在小型精选路径，例如 `benchmarks/runs/stage_c1_demo_latest/`。
- 大量历史 benchmark run 继续忽略。

Web App 只链接已有报告文件，不复制报告内容到新位置。

## 最小 UI

UI 应该偏实用、信息密度适中：

- 左侧：nodes、templates、reports 导航。
- 中间：当前 template / workflow 预览和 validation 状态。
- 右侧：run 控制、状态、artifact 链接。

控制项：

- Template selector。
- Planner demo 的用户请求输入框。
- 当前 template 参数输入。
- Plan、Validate、Run fake demo、打开报告链接等按钮。

Stage C.1 避免做完整拖拽节点编辑。只要静态 workflow 预览能清楚展示 nodes 和 edges 即可。

## 节点进化线

Stage C.1 只把 node evolution 暴露成 review view，不做自动变更系统。

Demo 需要展示：

- Proposal 数量。
- Evidence 数量。
- Gate 状态。
- Required tests。
- Proposal 为什么被阻塞。

除非后续有单独 review 过的实现计划，否则 Stage C.1 不把任何 proposal 自动提升为 stable。

## 测试

Stage C.1 必须不依赖真实 AEDT 也能测试：

- API tests 使用本地 test client。
- UI/data contract tests 校验 JSON shape 和 HTML 关键内容。
- 报告生成脚本 smoke tests。
- 现有完整 pytest suite 必须继续通过。

真实 AEDT smoke 仍然是 opt-in，不进入常规测试。

## 成功标准

Stage C.1 完成时应满足：

- 一个本地命令可以启动 Web Demo。
- Demo 能列出 nodes 和 templates。
- Demo 能用 deterministic mode 从自然语言请求规划 workflow。
- Demo 能 validate workflow。
- Demo 能运行 fake-adapter workflow 并展示 artifact 链接。
- Demo 能链接 Stage C report、smoke dashboard、demo index、node evolution review。
- API credential 可配置，但提交文件中保持空值。
- 常规测试无需真实 AEDT 即可通过。

## 非目标

Stage C.1 不做：

- 完整拖拽节点编辑器。
- 浏览器启动真实 AEDT。
- 多用户作业系统。
- 云部署。
- 电磁物理正确性证明。
- 自动发布 stable 节点。
- 通用优化 agent。

这些都放到 demo/API 边界稳定之后的后续阶段。

## 推荐实现顺序

1. 增加 config loader 和 example config。
2. 在已有 catalog、template、planner、validator、executor 上封装 API service 层。
3. 增加 API tests。
4. 增加轻量 Web UI，消费 API。
5. 增加 demo start script。
6. 展示报告链接和 fake run artifact。
7. 更新文档并做最终验证。
