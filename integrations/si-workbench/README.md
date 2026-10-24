# SI Forge · 高速 SI/PI 通道优化工作台

一个可直接运行的高保真桌面工作台原型，面向**差分过孔 / 反焊盘 / NFP 的信号完整性优化**，而不是天线设计。

## 打开

~~~powershell
.\\.venv\\Scripts\\python.exe integrations\\si-workbench\\serve.py
~~~

浏览器访问 \`http://127.0.0.1:8769/\`。使用 \`--no-browser\` 可禁止自动打开浏览器，使用 \`--port <port>\` 更换端口。也可直接打开 \`index.html\` 查看静态界面。

## 已实现的交互

- 物理结构：差分过孔剖面、顶层版图、七层层叠视图、缩放、全屏。
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
- 原始视频的视觉风格未作已验证的逐像素复刻：当前运行环境无法读取该视频的截帧。实现以 SI/PI 工程信息架构为主；若提供界面截图，可进一步校准布局与风格。

## 生产集成方向

后续应将演示状态替换为只读/受控的应用服务：

1. 从 Mission/Graph/SQLite 读取工程、轮次、指标与审批状态。
2. 从实际 AEDB/native inventory 读取 via、层叠、shape、padstack 与候选动作。
3. 通过已有 Harness 执行 preview/apply/readback/rollback，不在浏览器直接运行 PyAEDT。
4. 从评分与报告 artifact 读取有界 SDD/TDR 指标和 plot 路径，不将全曲线放入 Agent 上下文。
5. 生产启动、advance 和 human_gate 审批仍由受控本地 CLI/dashboard 执行；此 UI 不自行发明第二套审批协议。

## 验证

本机 Chrome + Playwright 已完成 23 项浏览器检查，覆盖：

- 页面与 SVG 正常渲染，无 JavaScript 异常；
- 1440px / 1100px 无横向溢出；
- 结构视图、层叠、缩放；
- 参数同步与预览/审批；
- TDR/SDD11/SDD21 切换；
- 优化完成与历史状态；
- 审查证据和导出 JSON 的 demo 标识。

测试脚本（仓库根目录执行，需先启动预览服务）：

~~~powershell
.\\.venv\\Scripts\\python.exe tmp\\test_si_workbench.py
~~~

截图与测试结果默认输出到系统临时测试目录，不纳入生产证据。
