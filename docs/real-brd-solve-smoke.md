# 真实 BRD 求解烟雾测试

该测试用于在装有 AEDT、PyAEDT 且许可证可用的机器上，验证
`BrdRealSolveAdapter` 能完成一次真实 HFSS 3D Layout 求解，并导出
solved project、Touchstone、TDR CSV 和求解 manifest。

## 使用边界

- 输入工程必须是已经人工审批的 BRD local-cut 副本。
- 不得把 `ANSYS_AGENT_REAL_AEDT_PROJECT` 指向生产原件。
- pytest adapter 默认会把输入复制到临时目录后再求解。真实远端闭环也应保留这个隔离：
  model edit 修改受控 working project，solve worker 使用 `checkpoint_copy` 求解副本，
  避免 COM engine 挂起或无效 `.aedtresults` 污染 working project。
- 首次运行建议确认 AEDT 版本、setup、sweep、端口数量和 TDR 表达式均与工程一致。

## 前置条件

- Windows 机器已安装目标版本 AEDT。
- 当前 Python 环境可导入与 AEDT 版本兼容的 `ansys-aedt-core`。
- AEDT/HFSS 3D Layout 许可证可用。
- 工程已包含可求解的 setup 和 sweep。
- 当前差分 BRD local-cut 闭环默认使用四端口 Touchstone `channel.s4p`，
  score 看 `SDD11/SDD21`，TDR 观察端口使用 `Diff1`。
- `TDRZt(Diff1,Diff1)` 中的差分端口名必须存在于工程的 differential
  pair/excitation 定义中。LLM/人工仍需确认 `Diff1` 对应哪一端。

## 运行命令

在项目根目录执行：

```powershell
$env:ANSYS_AGENT_RUN_REAL_AEDT = "1"
$env:ANSYS_AGENT_REAL_AEDT_PROJECT = "D:\cases\approved_local_cut.aedt"
$env:ANSYS_AGENT_REAL_AEDT_SETUP = "Setup1"
$env:ANSYS_AGENT_REAL_AEDT_SWEEP = "Sweep1"
$env:ANSYS_AGENT_REAL_AEDT_TDR_EXPRESSION = "TDRZt(Diff1,Diff1)"
$env:ANSYS_AGENT_REAL_AEDT_PORT_COUNT = "4"
$env:ANSYS_AGENT_REAL_AEDT_VERSION = "2026.1"

.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_agent_brd_real_solve_smoke.py
```

未设置 `ANSYS_AGENT_RUN_REAL_AEDT=1` 时，测试会默认跳过，不会启动 AEDT。

## 通过 SSH runner 跑 solve + score

如果 AEDT 在远端 Windows 工作站上运行，而 orchestrator 在本机运行，可以使用
`scripts/smoke_ssh_real_solve_score.py` 验证最小真实闭环：

```powershell
.\.venv\Scripts\python.exe scripts\smoke_ssh_real_solve_score.py `
  --host 192.168.71.51 `
  --user z3312 `
  --identity-file C:\Users\z3312\.ssh\ansys_agent_ed25519 `
  --remote-root D:\aedt-agent-runs `
  --remote-repo D:\ansys-agent `
  --remote-python python `
  --ssh-exe C:\Windows\System32\OpenSSH\ssh.exe `
  --scp-exe C:\Windows\System32\OpenSSH\scp.exe `
  --run-id real-solve-score-smoke `
  --project-path D:\cases\approved_local_cut.aedt `
  --setup-name Setup1 `
  --sweep-name Sweep1 `
  --tdr-expression "TDRZt(Diff1,Diff1)" `
  --tdr-observation-port Diff1 `
  --expected-port-count 4 `
  --touchstone-name channel.s4p `
  --sparameter-mode differential `
  --project-copy-mode checkpoint_copy `
  --aedt-version 2026.1 `
  --keep-local-temp
```

`--project-path` 是远端机器上的已批准 local-cut `.aedt` 工程路径。
脚本会先提交 `brd.local_cut.solve`，再把返回的 Touchstone/TDR 远端路径
直接提交给 `brd.channel.score`。本机只拉回 `solve_manifest.json`、
`brd_channel_score_evidence.json`、TDR/SDD11/SDD21 plot refs 等 bounded
evidence，不拉回原始曲线。

## 成功标准

测试必须确认：

- AEDT setup 阻塞求解完成；
- solved `.aedt` 工程存在；
- Touchstone `.s4p` 可被项目解析器读取且至少包含一个采样点；
- bounded score evidence 中 `return_loss_trace=SDD11`、
  `insertion_loss_trace=SDD21`；
- TDR CSV 可被项目解析器读取且至少包含一个采样点；
- `solve_manifest.json` 存在。

## 故障定位

- `project_path must end with .aedt`：输入不是 AEDT 工程副本。
- setup/sweep 不存在：检查环境变量与工程中的名称，注意空格和大小写。
- 端口数量不匹配：更新 `ANSYS_AGENT_REAL_AEDT_PORT_COUNT`，或检查 local-cut 端口。
- TDR 导出为空：确认表达式端口名有效，且 sweep 已产生可用于 TDR 的解。
- AEDT 启动或求解失败：先检查版本、许可证和残留 AEDT 进程，再查看 pytest 临时目录中的异常信息。
