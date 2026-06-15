# 真实 BRD 求解烟雾测试

该测试用于在装有 AEDT、PyAEDT 且许可证可用的机器上，验证
`BrdRealSolveAdapter` 能完成一次真实 HFSS 3D Layout 求解，并导出
solved project、Touchstone、TDR CSV 和求解 manifest。

## 使用边界

- 输入工程必须是已经人工审批的 BRD local-cut 副本。
- 不得把 `ANSYS_AGENT_REAL_AEDT_PROJECT` 指向生产原件。
- Adapter 会把输入复制到 pytest 临时目录后再求解，但源工程仍应保持只读和可恢复。
- 首次运行建议确认 AEDT 版本、setup、sweep、端口数量和 TDR 表达式均与工程一致。

## 前置条件

- Windows 机器已安装目标版本 AEDT。
- 当前 Python 环境可导入与 AEDT 版本兼容的 `ansys-aedt-core`。
- AEDT/HFSS 3D Layout 许可证可用。
- 工程已包含可求解的 setup 和 sweep。
- `TDRZt(P1,P1)` 中的端口名必须存在于工程中。

## 运行命令

在项目根目录执行：

```powershell
$env:ANSYS_AGENT_RUN_REAL_AEDT = "1"
$env:ANSYS_AGENT_REAL_AEDT_PROJECT = "D:\cases\approved_local_cut.aedt"
$env:ANSYS_AGENT_REAL_AEDT_SETUP = "Setup1"
$env:ANSYS_AGENT_REAL_AEDT_SWEEP = "Sweep1"
$env:ANSYS_AGENT_REAL_AEDT_TDR_EXPRESSION = "TDRZt(P1,P1)"
$env:ANSYS_AGENT_REAL_AEDT_PORT_COUNT = "2"
$env:ANSYS_AGENT_REAL_AEDT_VERSION = "2026.1"

.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_agent_brd_real_solve_smoke.py
```

未设置 `ANSYS_AGENT_RUN_REAL_AEDT=1` 时，测试会默认跳过，不会启动 AEDT。

## 成功标准

测试必须确认：

- AEDT setup 阻塞求解完成；
- solved `.aedt` 工程存在；
- Touchstone 可被项目解析器读取且至少包含一个采样点；
- TDR CSV 可被项目解析器读取且至少包含一个采样点；
- `solve_manifest.json` 存在。

## 故障定位

- `project_path must end with .aedt`：输入不是 AEDT 工程副本。
- setup/sweep 不存在：检查环境变量与工程中的名称，注意空格和大小写。
- 端口数量不匹配：更新 `ANSYS_AGENT_REAL_AEDT_PORT_COUNT`，或检查 local-cut 端口。
- TDR 导出为空：确认表达式端口名有效，且 sweep 已产生可用于 TDR 的解。
- AEDT 启动或求解失败：先检查版本、许可证和残留 AEDT 进程，再查看 pytest 临时目录中的异常信息。
