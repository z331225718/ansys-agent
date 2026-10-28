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


## 工作区 / 任务模式（P2，可写）

左侧新增「工作区 / 优化任务」面板，这是工作台**唯一**能下令的地方：

- **工作区** = 一个工程目录（点 ＋ 选择）。任务数据写在
  `<工作区>/.ai-hfss/tasks/<task_id>/missions.db`，不碰工程文件；
- **新建任务**：填目标、源 `.aedt`、模板（1GHz 单点冒烟 / reviewed BRD 循环）、
  Setup 与频率点；创建后立即生成独立 Mission 库与运行目录；
- 顶部任务状态条：任务状态、graph 状态、step、自动推进标记；
- 操作：刷新 / **推进** / **自动**（可停）/ **批准**、**拒绝**（有待审批时出现）。

接口（同一 dashboard）：`/api/workbench/workspaces`、`/api/workbench/workspaces/<id>/tasks`、
`/api/workbench/tasks/<id>`、`.../advance`、`.../auto`、`.../approvals`、
`.../decide/<approval_id>`。

- **删除任务**：状态条最右「删除任务」→ 二次确认弹窗（显示将删除的目录与内容）；
  只删 `<工作区>/.ai-hfss/tasks/<task_id>/`（`missions.db`、`runs/`、`artifacts/`、`logs/`），
  不动工程文件、不影响其他任务；自动推进中的任务会被拒绝（先「停止自动」）；
  接口为 `POST /api/workbench/tasks/<id>/delete`，必须带 `{"confirm":"<task_id>"}`。

环境变量：`AIHFSS_WORKSPACES_FILE` 可覆盖工作区注册表路径（默认
`~/.dsh/ai-hfss-workstation/workspaces.json`）；`AIHFSS_EXECUTION_PROFILE`
指定执行 profile（决定是否允许真实 AEDT 作业）。

> 旧的三栏只读展示仍保留：未选任务时显示演示/只读数据；选中任务后指标、
> 轮次、产物切换到该任务真实数据。

## 模型预览（结构化几何）

选中一个任务后，若它的 loop context 携带 `model_inventory`（由图节点
`aihfss.inventory.layout_model` 生成），几何面板会从"未接入"变成可交互视图：

- **俯视**：按层过滤（chips）、铺铜多边形、走线（按网络着色，线宽取真实 width）、
  过孔圆点（半径取真实孔径）；悬停显示对象名 / 网络 / 层；
- **剖面**：真实层叠（标高 + 厚度，来自 AEDT stackup）、过孔贯穿其 start/stop 层、
  走线所在层；悬停同样有对象信息。

渲染细节（v2）：

- **器件**：component 的 footprint 用虚线框画出（名字 + part），pin 用网络色圆点；
- **尺寸**：俯视左下角有比例尺 + 右下角显示真实范围（如 2.37 × 2.85 mm）；
- **剖面 1:1**：横纵同比例（mm/px 一致），层带按真实厚度/标高画，器件画在 placement 层，
  走线按真实线宽画成小方块，过孔按孔径画成贯穿筒；
- **文字**：所有标注加深色描边（`paint-order: stroke`）并按图幅换算字号，
  避免在 mm 单位的 viewBox 里被 CSS `px` 字号放大到覆盖整张图；

数据来源是只读抽取（`src/aedt_agent/agent/aihfss_inventory.py`）：
层叠 / 走线 / 过孔 / 铺铜统一换算成 **mm**，段落级失败会写进
`unavailable_sections`，不会伪造缺失结构。生成方式：用模板
`aihfss_layout_inventory` 建一个任务并推进（附着到已打开的 AEDT 会话，**不求解**）。

- **层叠图**：第四个视图显示标准层叠图（层名 / 铜厚 µm / 材料 / 板厚），由
  `render_stackup_plot()` 用 matplotlib 从 AEDT 抽出的层叠记录绘制；
  `GET /api/workbench/tasks/<id>/artifact/stackup_plot.png` 提供图片（白名单，只允许这一张）。
  依赖 `matplotlib`（可选，未安装时该视图不出现，其余视图不受影响）：
  `pip install matplotlib`。

> **为什么自己画**：PyEDB 的 `edb.stackup.plot()` 必须先打开 EDB 会话，而本机许可被 FlexLM
> 判为非法（`ISSUER="Team SolidSQUAD-SSQ"`，`Invalid license file syntax`），独立 EDB API 拿不到授权，
> 所以按官方图的样式自绘：白底、居中 `Stackup` 标题、左侧介质层名、右侧铜/过孔层名、
> 下方双列图例（`name, material, thick: N um, elev: M um`），层厚按官方规则压缩
> （`max = 3 × min`）以便薄铜可见。图上标注「仿 PyEDB 官方风格 · 数据来自 AEDT stackup」。rge · 高速 SI/PI 通道优化工作台

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


## 工作区 / 任务模式（P2，可写）

左侧新增「工作区 / 优化任务」面板，这是工作台**唯一**能下令的地方：

- **工作区** = 一个工程目录（点 ＋ 选择）。任务数据写在
  `<工作区>/.ai-hfss/tasks/<task_id>/missions.db`，不碰工程文件；
- **新建任务**：填目标、源 `.aedt`、模板（1GHz 单点冒烟 / reviewed BRD 循环）、
  Setup 与频率点；创建后立即生成独立 Mission 库与运行目录；
- 顶部任务状态条：任务状态、graph 状态、step、自动推进标记；
- 操作：刷新 / **推进** / **自动**（可停）/ **批准**、**拒绝**（有待审批时出现）。

接口（同一 dashboard）：`/api/workbench/workspaces`、`/api/workbench/workspaces/<id>/tasks`、
`/api/workbench/tasks/<id>`、`.../advance`、`.../auto`、`.../approvals`、
`.../decide/<approval_id>`。

- **删除任务**：状态条最右「删除任务」→ 二次确认弹窗（显示将删除的目录与内容）；
  只删 `<工作区>/.ai-hfss/tasks/<task_id>/`（`missions.db`、`runs/`、`artifacts/`、`logs/`），
  不动工程文件、不影响其他任务；自动推进中的任务会被拒绝（先「停止自动」）；
  接口为 `POST /api/workbench/tasks/<id>/delete`，必须带 `{"confirm":"<task_id>"}`。

环境变量：`AIHFSS_WORKSPACES_FILE` 可覆盖工作区注册表路径（默认
`~/.dsh/ai-hfss-workstation/workspaces.json`）；`AIHFSS_EXECUTION_PROFILE`
指定执行 profile（决定是否允许真实 AEDT 作业）。

> 旧的三栏只读展示仍保留：未选任务时显示演示/只读数据；选中任务后指标、
> 轮次、产物切换到该任务真实数据。

## 模型预览（结构化几何）

选中一个任务后，若它的 loop context 携带 `model_inventory`（由图节点
`aihfss.inventory.layout_model` 生成），几何面板会从"未接入"变成可交互视图：

- **俯视**：按层过滤（chips）、铺铜多边形、走线（按网络着色，线宽取真实 width）、
  过孔圆点（半径取真实孔径）；悬停显示对象名 / 网络 / 层；
- **剖面**：真实层叠（标高 + 厚度，来自 AEDT stackup）、过孔贯穿其 start/stop 层、
  走线所在层；悬停同样有对象信息。

渲染细节（v2）：

- **器件**：component 的 footprint 用虚线框画出（名字 + part），pin 用网络色圆点；
- **尺寸**：俯视左下角有比例尺 + 右下角显示真实范围（如 2.37 × 2.85 mm）；
- **剖面 1:1**：横纵同比例（mm/px 一致），层带按真实厚度/标高画，器件画在 placement 层，
  走线按真实线宽画成小方块，过孔按孔径画成贯穿筒；
- **文字**：所有标注加深色描边（`paint-order: stroke`）并按图幅换算字号，
  避免在 mm 单位的 viewBox 里被 CSS `px` 字号放大到覆盖整张图；

数据来源是只读抽取（`src/aedt_agent/agent/aihfss_inventory.py`）：
层叠 / 走线 / 过孔 / 铺铜统一换算成 **mm**，段落级失败会写进
`unavailable_sections`，不会伪造缺失结构。生成方式：用模板
`aihfss_layout_inventory` 建一个任务并推进（附着到已打开的 AEDT 会话，**不求解**）。

- **层叠图**：第四个视图显示标准层叠图（层名 / 铜厚 µm / 材料 / 板厚），由
  `render_stackup_plot()` 用 matplotlib 从 AEDT 抽出的层叠记录绘制；
  `GET /api/workbench/tasks/<id>/artifact/stackup_plot.png` 提供图片（白名单，只允许这一张）。
  依赖 `matplotlib`（可选，未安装时该视图不出现，其余视图不受影响）：
  `pip install matplotlib`。

> **优先用官方 PyEDB**：`render_stackup_plot()` 先调 `edb.stackup.plot()`（DotNet 后端 `grpc=False`，
> 失败会快速返回而不是卡住），成功则图上标注「PyEDB 官方层叠图」；
> 若本机没有 EDB feature（`Can't acquire license`），自动回退到 matplotlib 等价图，
> 并在图上标注原因，例如：`matplotlib 等价图 · PyEDB: EDBErrorException: Can't acquire license`。
> 想启用官方图：让 `ANSYSLMD_LICENSE_FILE` 指向含 EDB/SIWave feature 的 license（当前是 `1055@localhost`，
> 只有 AEDT 能起、EDB 拿不到 license），无需改代码。

- **3D 视图（第三档）**：按真实 stackup 标高把铺铜/走线/过孔拉伸成 3D，
  器件画成线框；可拖拽旋转、滚轮缩放；Z 轴放大 3× 并明确标注（真实板厚 0.49mm，
  不放大几乎看不见）；纯 Canvas 2D 画家算法，不依赖 three.js 等外部库。

> 为什么不用 PyAEDT 官方 PyVista 渲染：`get_model_plotter_geometries` / `plot_model_obj`
> 源码里明确排除 `HFSS3DLayout`（`post_common_3d.py` 里 `solution_type not in [...]`），
> 对 3D Layout 返回空；`export_3d_model` 只导 Parasolid `.x_t`/`.sat`（B-rep，浏览器无法直接渲染）；
> 原生可用的只有 `export_model_picture` 静态截图。所以 3D 视图是从 inventory 自行拉伸的真实几何。

实测样本（E:\ai-hfss-test 工程）：9 层、42 过孔、4 走线、4 铺铜，
局部范围 2.37 × 2.84 mm。

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