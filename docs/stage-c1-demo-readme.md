# AEDT Agent Stage C.2 Demo README

## 这个 Demo 展示什么

Stage C Demo 用来展示 AEDT Agent 的产品化闭环。默认首页现在固定演示一个完整的 microstrip S-parameter workflow，避免把 catalog、planner、benchmark 调试入口混在一起：

```text
固定 Microstrip S-Parameter Workflow
        ↓
Workflow JSON
        ↓
Validator
        ↓
Controlled NodeExecutor
        ↓
Fake AEDT Adapter
        ↓
Artifact / Validation / Report
```

它的重点不是完整拖拽 UI，而是让其他团队能直观看到：

- 节点 catalog 已经结构化。
- workflow template 可以复用。
- 首页可以一键跑完一个固定 workflow。
- 自然语言请求可以在 Advanced 工作台中规划成 workflow。
- workflow 会先 validation，再执行。
- demo run 会生成可追溯 artifact。
- 真实 AEDT smoke 结果可以从报告入口查看。
- Stage C.2 增加 planner mode 和 repair loop 展示：主模型只能生成 workflow JSON，后端 validator 决定是否可执行。

## 启动命令

在仓库根目录运行：

```bash
.venv/bin/python scripts/run_stage_c1_demo_server.py --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765
```

默认配置来自：

```text
config/demo_config.example.json
```

本地私有配置可以写到：

```text
config/demo_config.local.json
```

`config/*.local.json` 已被 `.gitignore` 忽略。不要把 API key、base URL 或本机绝对路径写入可提交文件。

## Planner Mode 和本地配置

Demo 默认使用 `deterministic` planner，不需要 LLM API。要测试 LLM planner，可以在本地创建 `config/demo_config.local.json`：

```json
{
  "planner": {
    "mode": "llm",
    "provider": "openai-compatible",
    "model": "",
    "base_url": "",
    "api_key": "",
    "max_repair_attempts": 3
  }
}
```

提交仓库时这些字段必须保持空值。LLM planner 的边界是：

- 只允许输出 workflow JSON。
- 不允许输出 PyAEDT Python。
- 每一轮输出都必须经过 backend validator。
- 如果 validation 失败，会把错误作为 repair context 进入下一轮。

## 页面怎么演示

推荐演示顺序：

1. 打开首页，说明这是固定端到端 demo，不是开发调试面板。
2. 说明流程左侧参数会实例化 `microstrip_sparameter` workflow。
3. 点击 `Preview Workflow`，展示将要执行的 workflow JSON。
4. 点击 `Run Demo`，看右侧五个步骤从 `pending` 变成 `done`。
5. 查看结果区的 `Status`、`Validation Result` 和 expected outputs。
6. 打开 artifact 链接，展示每次运行都会落盘：
   - `workflow_run`
   - `validation`
   - `audit`
   - `report`
7. 点击真实 AEDT smoke 和节点进化 review 链接，展示 Stage C 已跑过的真实 AEDT artifact 和受控节点进化机制。
8. 如需展示 planner、node catalog、API 调试入口，打开：

```text
http://127.0.0.1:8765/advanced
```

## API 示例

查看状态：

```bash
curl -s http://127.0.0.1:8765/api/status
```

查看模板：

```bash
curl -s http://127.0.0.1:8765/api/templates
```

自然语言规划：

```bash
curl -s -X POST http://127.0.0.1:8765/api/plan \
  -H 'content-type: application/json' \
  -d '{"user_request":"create a microstrip s-parameter simulation at 5GHz"}'
```

指定 planner mode：

```bash
curl -s -X POST http://127.0.0.1:8765/api/plan \
  -H 'content-type: application/json' \
  -d '{"planner_mode":"deterministic","user_request":"create a wave port setup"}'
```

运行 fake adapter demo：

```bash
curl -s -X POST http://127.0.0.1:8765/api/run \
  -H 'content-type: application/json' \
  -d '{"template_id":"microstrip_sparameter"}'
```

## 为什么默认不用真实 AEDT

浏览器 demo 默认只运行 fake adapter。真实 AEDT 仍通过 CLI smoke 显式运行，因为真实 AEDT：

- 依赖本机安装和 license。
- 启动慢，容易受进程状态影响。
- 更适合用独立命令记录日志和 artifact。

真实 AEDT smoke 已在 Stage C 中跑通 3 个 workflow：

- `microstrip_sparameter`
- `wave_port_setup`
- `radiation_airbox_setup`

查看汇总报告：

```text
http://127.0.0.1:8765/reports/stage_c_real_smoke_dashboard.html
```

## Planner Benchmark

运行 5 条内置自然语言请求的小型 planner benchmark：

```bash
.venv/bin/python scripts/run_stage_c2_planner_benchmark.py
```

输出：

```text
benchmarks/reports/stage_c2_planner_benchmark.html
benchmarks/reports/stage_c2_planner_benchmark.json
```

启动 demo server 后也可以直接打开：

```text
http://127.0.0.1:8765/reports/stage_c2_planner_benchmark.html
```

## 当前边界

Stage C.1 不做：

- 完整拖拽节点编辑器。
- 浏览器启动真实 AEDT。
- 多用户任务队列。
- 云部署。
- 电磁物理正确性证明。
- 自动发布 stable 节点。
- 通用优化 agent。

## 验证命令

运行 Stage C.1 相关测试：

```bash
.venv/bin/python -m pytest \
  tests/test_stage_c1_demo_config.py \
  tests/test_stage_c1_demo_service.py \
  tests/test_stage_c1_demo_web.py \
  tests/test_stage_c2_planner.py \
  tests/test_stage_c2_planner_benchmark.py \
  -q
```

运行全量测试：

```bash
.venv/bin/python -m pytest -q
```
