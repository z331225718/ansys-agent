# 运行证据索引（M1 Reviewed BRD 差分 Via 优化闭环）

本目录只保存**有界摘要**证据；原始 S 参数/TDR 曲线、.aedt/.aedb、完整日志与密钥
一律不进入 Git（见根 .gitignore：`docs/evidence/*/` 已忽略，仅本 README 被跟踪）。

## 目录约定

```text
docs/evidence/
  README.md              # 本索引（跟踪）
  <run-id>/README.md     # 单次运行摘要（本机保留，不提交）
```

单次运行摘要只写以下内容（依据实施计划第 10 节）：

- 状态分类（completed_with_improvement / completed_no_improvement /
  stopped_for_approval / failed / offline_only）；
- bounded 指标：SDD11/SDD21 最差值与频率、TDR 峰值偏差与观察端口、
  orientation 是否已证实、objective total cost；
- graph_run_id / mission_id；
- artifact 相对路径与 SHA256；
- 测试命令与结果；
- 未解决问题与下一步。

## 当前状态

- M1 离线部分（T0-T3）已完成；**真实 AEDT 闭环尚未启动**，第 9 节人工启动门待用户完成。
- 因此本索引目前只记录离线测试基线，不得据此声明“真实闭环通过”。

## T0 离线测试基线（2026-10-24）

- 分支：`feature/reviewed-brd-dsh-loop`
- 实施基线提交：`f7f84111c73ea9deb9c49562da9747ca4ca9fda5`
- 真实 AEDT 调用：无（环境开关全部置 0）

命令：

```powershell
$env:RUN_REAL_LIVE_AEDT = '0'
$env:RUN_REAL_AEDT = '0'
$env:RUN_REAL_PYEDB = '0'
$env:ANSYS_AGENT_RUN_REAL_AEDT = '0'
.\.venv\Scripts\python.exe -m pytest tests -q --no-header
```

结果：

| 运行 | 结果 |
|---|---|
| `pytest tests` | 1434 passed, **10 failed**, 31 skipped |
| `pytest tests --ignore=tests\test_offline_release_scripts.py` | **1424 passed**, 31 skipped |

10 个失败全部位于 `tests/test_offline_release_scripts.py`，为**改动前既有基线失败**，
与本计划无关：测试在子进程启动 Windows PowerShell 5.1 校验离线安装包时，
该 PowerShell 环境报 `Get-FileHash is not recognized`（离线发布属于 M4 范围，
本计划第 11 节明确排除）。未删除、未 skip、未放宽任何测试。
