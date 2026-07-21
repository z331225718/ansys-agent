# 开放 AEDT 执行验收矩阵

`preview_live_open_aedt_python` / `apply_live_open_aedt_python` 是完全访问模式。它的
放行依据不能只是“代码返回 completed”：必须证明审批、目标绑定、磁盘快照和恢复路径都成立。

## 运行层次

| 层次 | 目的 | 运行方式 |
| --- | --- | --- |
| 单元 | 覆盖拒绝路径和文件系统故障，不需要 AEDT license | `.venv\Scripts\python.exe -m pytest tests/test_live_aedt.py -k open_aedt_python -q` |
| 全量回归 | 确保 launcher、MCP、审批和既有 Harness 没有回归 | `.venv\Scripts\python.exe -m pytest -q` |
| 真实 AEDT | 在新建临时目录、全新 AEDT 进程中验证 PyAEDT/EDB 行为 | `RUN_REAL_LIVE_AEDT=1` 后运行 `tests/test_live_open_aedt_python_real.py` |
| 恢复演练 | 用新的 AEDT 进程重新打开 backup，不复用原 app 内存 | 包含在真实 AEDT 用例中 |

真实验收只能使用临时工程；不得将用户正在编辑的工程作为测试输入。离线 Release 的 `desktop` 环境包含
`pytest`，因此下面的命令可直接在已安装根目录运行。

## Case 矩阵

| ID | 场景 | 必须断言 |
| --- | --- | --- |
| V1 | HFSS `.aedt` | preview、原生审批、apply；创建唯一几何；备份先于执行产生；新 AEDT 进程打开备份后不存在该几何。 |
| V2 | 3D Layout `.aedb`，缺失/过期 `.aedt` | 从同名 `.aedb` 回退；真实创建 Layout rectangle 并读回；备份目录能被新 AEDT 导入且不含执行后几何。 |
| A1 | 审批绑定 | 无 token、错误 token、过期 token、重放 token、错误 preview/session 均在 `save_project`/备份/代码执行前拒绝。 |
| D1 | 内存目标漂移 | preview 后切换 project/design 或变更关联 app identity，apply 返回 stale，零备份、零代码执行。 |
| D2 | 磁盘目标漂移 | preview 后改动 `.aedt` 或伴随 `.aedb` 内容，apply 返回 stale，零保存、零备份、零代码执行。 |
| B1 | 路径解析 | `.aedt`、`.aedt + .aedb`、仅 `.aedb`、缺失路径、多候选路径；仅唯一且存在的来源可继续，歧义不得猜测。 |
| B2 | 备份原子性 | save/copy/权限/空间失败时不得执行代码；最终 backup 目录不存在，临时 staging 目录被清理。 |
| F1 | 用户代码失败 | 先输出/emit 再抛异常；结果为 failed，保留可定位 backup 和 manifest，preview 失效。 |
| L1 | Broker 韧性 | `SystemExit` 被记录为失败，随后独立只读调用仍可完成。无限循环只能在有 worker 超时隔离时测试。 |
| R1/R2 | 恢复 | `.aedt` 和 `.aedb` 两种 backup 都必须由新的 AEDT 进程重新打开验证；不能以原进程内存状态代替恢复证据。 |

## 版本门槛

每一个真实 Case 至少需要在目标部署版本 **AEDT 2024 R2** 和当前开发验证版本 **AEDT 2026.1** 各跑一次。
版本结果必须分别记录，不允许以 2026.1 的通过替代 2024 R2 的证据。

```powershell
$env:RUN_REAL_LIVE_AEDT = '1'
$env:REAL_AEDT_VERSION = '2024.2'
.\.venv\Scripts\python.exe -m pytest tests/test_live_open_aedt_python_real.py -q
```

2026.1 的对应命令只需把 `REAL_AEDT_VERSION` 改为 `2026.1`。测试会自行创建临时目录、临时 gRPC 端口和新 AEDT
进程，并在 finally 中关闭测试进程；它不应附着到用户的当前 Desktop 会话。

## 当前证据

- 本机 AEDT 2026.1：V1、V2、R1、R2 已通过。
- 单元层：A1、D1、D2、B1、B2、F1、L1 已自动覆盖。
- 在远端 AEDT 2024 R2 完成上述命令前，发布包只能标记为“2026.1 已验收、2024 R2 待验收”。
