# Agent v2 实现计划

> 参考 Superpowers `writing-plans` 模块：每个 task 2-5 分钟，精确文件路径，验证步骤

## Phase A: GraphNode + GraphTemplate 扩展

### A1: 扩展数据模型
- **File:** `src/aedt_agent/agent/graph_template.py`
- **Change:** GraphNode 新增 `system_prompt`, `model`, `profile`, `constraints`。GraphTemplate 新增 `prompts`, `profiles`, `environment`, `security`。
- **Test:** `tests/test_agent_graph_template.py` — 新字段解析+验证
- **Verify:** `pytest -k "template" -q`

### A2: YAML 解析
- **File:** `src/aedt_agent/agent/graph_template.py` — `graph_template_from_mapping` 解析新层
- **Test:** 加载含 prompts/profiles 的 YAML
- **Verify:** 所有现有 YAML 模板仍可加载

## Phase B: Agent Executor

### B1: execute_agent_node
- **File:** `src/aedt_agent/agent/graph_executors.py` — 新增 agent 执行器
- **Change:** `kind: agent` 节点走 `execute_agent_node()`: resolve prompt → call LLM → parse JSON → validate handoff
- **Test:** mock LLM, 验证 prompt 解析 + handoff 校验

### B2: prompt 解析
- **File:** `src/aedt_agent/agent/graph_executors.py`
- **Change:** `_resolve_prompt(key, prompts_dict)` — key 为空则用 node.system_prompt 内联
- **Test:** 内联 prompt 和 key 引用两种

### B3: 约束解析
- **File:** `src/aedt_agent/agent/graph_executors.py`
- **Change:** `_resolve_constraints(node, template)` — merge profile + node.constraints
- **Test:** profile 覆盖 + node 覆盖

## Phase C: Code Agent 三层约束

### C1: 语法校验
- **File:** `src/aedt_agent/agent/code_agent.py` — 新文件
- **Change:** `validate_python_syntax(code: str) -> str | None` — ast.parse, 返回错误或 None
- **Test:** 合法代码通过, 语法错误被捕获

### C2: Import 白名单
- **File:** `src/aedt_agent/agent/code_agent.py`
- **Change:** `validate_imports(code: str, allowed: list[str]) -> list[str]` — 返回违规 import
- **Test:** 白名单内通过, 白名单外拒绝

### C3: 代码提取
- **File:** `src/aedt_agent/agent/code_agent.py`
- **Change:** `extract_code_block(llm_output: str) -> str` — 从 markdown code fence 提取
- **Test:** ```python ... ``` 格式提取

### C4: 集成到 executor
- **File:** `src/aedt_agent/agent/graph_executors.py`
- **Change:** `execute_agent_node` 中 `capability == "code_writer"` 走三层约束
- **Test:** end-to-end code agent mock

## Phase D: YAML 模板升级

### D1: 升级 brd_local_cut_build.yaml
- **File:** `docs/agent_templates/brd_local_cut_build.yaml`
- **Change:** 节点改为 `kind: agent`, 加 prompts/profiles/security 层
- **Test:** 加载成功, graph run 通过

### D2: 创建 brd_channel_optimize.yaml
- **File:** `docs/agent_templates/brd_channel_optimize.yaml`
- **Change:** 完整六层配置示例
- **Test:** 加载成功

## Phase E: Scorecard 扩展

### E1: Agent 输出审计
- **File:** `src/aedt_agent/agent/scorecard.py`
- **Change:** 检查 agent 节点输出是否包含 llm_model, planning_source, token 估算
- **Test:** agent 节点 scorecard 检查

## 验证

每一步完成后:
```bash
.venv/Scripts/python.exe -m pytest -q
```
确保失败集合不扩大。
