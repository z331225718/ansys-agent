# SI Forge · 高速 SI/PI 通道优化工作台

一个可直接运行的高保真桌面工作台原型，面向**差分过孔 / 反焊盘 / NFP 的信号完整性优化**，而不是天线设计。

## 打开

~~~powershell
.\\.venv\\Scripts\\python.exe integrations\\si-workbench\\serve.py
~~~

浏览器访问 \`http://127.0.0.1:8769/\`。使用 \`--no-browser\` 可禁止自动打开浏览器，使用 \`--port <port>\` 更换端口。也可直接打开 \`index.html\` 查看静态界面。

## 已实现的交互

- 物理结构：差分过孔剖面、顶层版图、七层层叠视图、缩略图多视角、缩放、全屏。
- 参考视频的浅色三栏工程布局：模型工作区、参数/审查面板、常驻 AI 设计助手。
- AI 设计助手：可询问 TDR 峰值、SDD 指标、下一轮反焊盘建议与证据边界；当前为规则化演示回答，不调用真实模型。
- 参数：参考层选择、反焊盘半径、NFP 开关与半径，双向数值同步和范围检查。
- 分析：TDR、SDD11、SDD21 切换、采样点悬浮、目标线、候选/基线对照。
- 工程闭环演示：修改预览 → 人工批准 → 应用 → 模拟求解/评分 → 优化历史。
- 审查：源模型审查记录、Diff1 观察端方向、物理约束与工作副本隔离。
- 导出：下载有界演示证据 JSON，不包含完整曲线采样数组或 AEDT 工程。

## 重要边界

**这是纯前端演示，不是 AEDT 仿真结果，也不连接或修改真实工程。**

- 所有几何、曲线、指标、模型名和连接状态均为演示数据。
- 不调用 AEDT，不修改 source/working project，不启动 Mission/Graph。
- “批准”“readback”“验证完成”仅用于展示目标交互，不构成生产证据。
- 导出的 JSON 明确带有 \`demo: true\` 与非生产验收声明。
- 已读取视频关键截帧并据此采用浅色三栏工程布局、模型多视角、参数/审查面板与常驻 AI 助手；未宣称与参考应用逐像素相同。模型、曲线和助手内容均替换为 SI/PI 差分通道优化语义。

## 生产集成方向

后续应将演示状态替换为只读/受控的应用服务：

1. 从 Mission/Graph/SQLite 读取工程、轮次、指标与审批状态。
2. 从实际 AEDB/native inventory 读取 via、层叠、shape、padstack 与候选动作。
3. 通过已有 Harness 执行 preview/apply/readback/rollback，不在浏览器直接运行 PyAEDT。
4. 从评分与报告 artifact 读取有界 SDD/TDR 指标和 plot 路径，不将全曲线放入 Agent 上下文。
5. 生产启动、advance 和 human_gate 审批仍由受控本地 CLI/dashboard 执行；此 UI 不自行发明第二套审批协议。

## 验证

本机 Chrome + Playwright 已完成 26 项浏览器检查，覆盖：

- 页面与 SVG 正常渲染，无 JavaScript 异常；
- 1440px / 1100px 无横向溢出；
- 结构视图、层叠、缩略图切换、缩放；
- AI 助手提问、工程回答与上下文保留；
- 参数同步与预览/审批；
- TDR/SDD11/SDD21 切换；
- 优化完成与历史状态；
- 审查证据和导出 JSON 的 demo 标识。

测试脚本（仓库根目录执行，需先启动预览服务）：

~~~powershell
.\\.venv\\Scripts\\python.exe tmp\\test_si_workbench.py
~~~

截图与测试结果默认输出到系统临时测试目录，不纳入生产证据。


## 连接真实 Mission（只读）

工作台已挂到现有 dashboard 的同一服务与 Mission DB：

~~~powershell
.\.venv\Scripts\python.exe -m aedt_agent.agent --db <missions.db> mission web --host 127.0.0.1 --port 8766 --profile config\execution_profiles\local_real_aedt.json
~~~

打开 http://127.0.0.1:8766/workbench ，点击「连接项目」，选择 Mission / Graph。

- 读取真实 Mission、Graph 状态、worker SDD/TDR/objective、约束、历史和已注册 artifact。
- 不启动求解、不推进 graph、不批准 human_gate、不写 AEDT。
- 真实数据标记 LIVE；无几何 inventory 时显示「几何预览未接入」，无 plot artifact 时显示「真实曲线 artifact 未注册」，不混入演示值。
- 文件访问沿用 dashboard 的 mission/path 权限校验。

验证记录：后端 8 项集成测试通过；真实连接浏览器 19 项检查通过；独立演示浏览器 26 项检查通过。此接入仍不包含 AEDB inventory 模型渲染和生产写入，二者不得由前端自行补造。

安全边界补充：工作台 artifact 投影只允许报告/历史/评分/plot 类型，拒绝 `.aedt/.aedb/.edb/.brd` 等工程文件，避免源工程经 dashboard 下载；source SHA256 标记为 worker 上报且读取时未重算，不作为已验证证据。
