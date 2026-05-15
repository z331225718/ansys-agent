# Stage B 收口与可复现报告计划

> 面向后续执行者：本计划不继续扩大 benchmark 任务集，目标是把当前 Stage B 结果整理成可复现、可汇报、可继续迭代的交付状态。

## 背景

Stage B 当前已经完成 10-task B/C 对照：

- Group B：Claude harness + GitNexus + 官方 PyAEDT 源码/examples，输出自由 Python。
- Group C：同一 harness 生成 JSON node plan，本地受控节点执行，不允许自由 Python fallback。
- 判据：真实 AEDT 2026.1 non-graphical 执行 + validation script。
- 最大尝试次数：3。

当前结果：

- B 组首轮成功率：70%
- B 组三次内成功率：90%
- C 组首轮成功率：80%
- C 组三次内成功率：100%
- C 组自由代码执行次数：0
- C 组平均成功轮次：1.2

当前汇报文件：

- `benchmarks/reports/stage_b_10task_compare.html`
- `benchmarks/reports/stage_b_10task_compare.json`

## 总目标

把 Stage B MVP 固化为一个可复现交付：

1. 任何人能根据 README/config 复跑 Stage B benchmark。
2. 一条命令能生成中文汇报 HTML。
3. 报告明确说明 B/C 方法、指标、失败案例和 validation 边界。
4. 现有 10-task 结果可作为 Stage B 收口证据。
5. 后续 Stage B 继续增强 validation，而不是马上扩任务或进入完整 DAG/node 产品化。

## 非目标

本计划不做：

- 扩展到 30/90 task。
- 新增大量 node。
- 做可视化 node editor。
- 做完整 DAG runtime。
- 把 GitNexus 变成执行链核心。
- 宣称 validation 等价于完整电磁物理正确性。

## 成功标准

- `README` 或专门文档中有清楚的 Stage B 复跑步骤。
- 存在一键报告脚本，能从指定 B/C run 或默认最新 run 生成中文 HTML。
- 报告不包含本机绝对路径、API key、base URL 等敏感信息。
- 报告中明确写出：
  - Group B 与 Group C 的区别。
  - 三次修复机制。
  - pass/fail 判定依据。
  - 当前 validation 是结构性判卷，不是完整物理正确性证明。
- 全量测试通过。
- `git diff --check` 通过。
- 变更已推送到 GitHub public 仓库。

## 任务 1：整理 Stage B 复现文档

**目标：** 让用户能独立复跑 benchmark 并理解配置项。

修改/新增：

- `docs/stage-b-controlled-node-benchmark.md`

内容至少包括：

- 环境前置条件：
  - Python venv
  - PyAEDT
  - AEDT 2026.1 non-graphical
  - Claude harness CLI
  - GitNexus eval-server
- 配置文件：
  - `config/benchmark_config.json`
  - `config/harness/group_b.json`
  - `config/harness/group_c.json`
- 常用命令：
  - C-only 10-task
  - B-only 10-task
  - B/C 报告合成
- 关键输出：
  - run dir
  - audit jsonl
  - `stage_b_report.json`
  - `stage_b_report.html`
  - presentation report

验收：

```bash
rg -n "Stage B|Group B|Group C|stage_b_10task_compare|run_stage_b_benchmark" docs/stage-b-controlled-node-benchmark.md
```

## 任务 2：增加一键中文报告脚本

**目标：** 不再靠临时 Python snippet 合成 B/C presentation report。

新增：

- `scripts/build_stage_b_report.py`

建议 CLI：

```bash
.venv/bin/python scripts/build_stage_b_report.py \
  --group-b-report benchmarks/runs/stage_b_b_10task_after_node_fixes/stage_b_report.json \
  --group-c-report benchmarks/runs/stage_b_c_10task_after_node_fixes/stage_b_report.json \
  --output-html benchmarks/reports/stage_b_10task_compare.html \
  --output-json benchmarks/reports/stage_b_10task_compare.json
```

脚本职责：

- 读取 B-only report 和 C-only report。
- 合成 presentation report。
- 删除 artifact path 字段。
- 脱敏本机路径。
- 调用 `write_html_report_stage_b()` 生成中文 HTML。

验收：

```bash
.venv/bin/python scripts/build_stage_b_report.py \
  --group-b-report benchmarks/runs/stage_b_b_10task_after_node_fixes/stage_b_report.json \
  --group-c-report benchmarks/runs/stage_b_c_10task_after_node_fixes/stage_b_report.json \
  --output-html benchmarks/reports/stage_b_10task_compare.html \
  --output-json benchmarks/reports/stage_b_10task_compare.json

rg -n "B 组三次内成功率|C 组三次内成功率|实验设计|判定依据" benchmarks/reports/stage_b_10task_compare.html
rg -n "/home/zzmjay|sk-|api\\.deepseek\\.com|deepseek-v4-flash" benchmarks/reports/stage_b_10task_compare.html benchmarks/reports/stage_b_10task_compare.json || true
```

## 任务 3：给报告合成逻辑加单元测试

**目标：** 防止后续改报告时重新引入绝对路径或敏感字段。

建议实现：

- 把报告合成/脱敏逻辑放到可测试函数中，例如：
  - `src/aedt_agent/benchmark/stage_b_presentation.py`
- 新增测试：
  - `tests/test_stage_b_presentation.py`

测试点：

- B/C report 合并后保留两组指标。
- artifact path 字段被删除。
- error summary 中本机路径被替换为 `<repo>`。
- HTML 中包含中文汇报章节。

验收：

```bash
.venv/bin/python -m pytest tests/test_stage_b_presentation.py tests/test_report_html_stage_b.py -q
```

## 任务 4：复查并收紧报告表述

**目标：** 确保报告可以拿出去展示，但不过度宣称。

检查点：

- 报告标题、摘要和结论清楚。
- 明确说明“结构性判卷”边界。
- 失败案例展示聚焦原因，不暴露本机路径。
- 指标说明不混淆：
  - 首轮成功率
  - 三次内成功率
  - 平均成功轮次
  - 自由代码执行次数
  - GitNexus 查询数
  - 节点覆盖率

验收：

```bash
rg -n "完整电磁|结构性|自由代码执行次数|三次修复|判定依据" benchmarks/reports/stage_b_10task_compare.html
```

## 任务 5：最终验证、提交、推送

执行：

```bash
.venv/bin/python -m pytest -q
git diff --check
rg -n "sk-[A-Za-z0-9]{10,}|api\\.deepseek\\.com|deepseek-v4-flash|apikey|api_key|baseurl" . --glob '!benchmarks/runs/**' --glob '!.venv/**' --glob '!tmp/**' || true
git status --short
```

提交建议：

```bash
git add docs/stage-b-controlled-node-benchmark.md scripts/build_stage_b_report.py src/aedt_agent/benchmark/stage_b_presentation.py tests/test_stage_b_presentation.py benchmarks/reports/stage_b_10task_compare.html benchmarks/reports/stage_b_10task_compare.json
git commit -m "Document stage b reproducible reporting"
git push origin stage-a-grounding-benchmark
```

## 完成后的下一步

Stage B 收口后再决定方向：

1. 如果目标是汇报/demo：停止扩展任务集，准备 slides 和口径。
2. 如果目标是继续工程化：优先增强 validation，而不是增加 task 数。
3. 如果目标是 Stage C：再开始 node schema 细分、节点组合、DAG runtime 和更强物理语义判卷。
