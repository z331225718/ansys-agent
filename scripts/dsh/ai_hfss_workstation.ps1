<#
.SYNOPSIS
    One-click install / verify / rollback for the AI-HFSS Workstation DSH surface.

.DESCRIPTION
    Wraps install_ai_hfss_workstation.py so the three operations are a single
    word. The bundle registers a top-level sidebar panel and main page in the
    DSH web GUI (parity with 插件 / 自动化任务). The panel STARTS and STOPS the
    workbench dashboard itself — no command has to be run by hand — and embeds
    it read-only once it answers.

    rollback removes every artifact this tool created and restores the profile
    package.json to its pre-install bytes.

.PARAMETER Action
    check | apply | rollback  (default: check)

.PARAMETER Profile
    dsh profile directory. Defaults to $env:USERPROFILE\.dsh\profiles\desktop

.PARAMETER WorkbenchUrl
    URL the panel embeds AND the host/port the launcher binds.
    Default: http://127.0.0.1:8766/workbench

.PARAMETER MissionsDb
    Mission SQLite database the workbench reads.
    Default: $env:USERPROFILE\.dsh\ai-hfss-workstation\missions.db

.PARAMETER Python
    Interpreter that runs the dashboard. Default: <repo>\.venv\Scripts\python.exe

.PARAMETER LogFile
    Dashboard stdout/stderr log. Default: $env:USERPROFILE\.dsh\ai-hfss-workstation\dashboard.log

.EXAMPLE
    .\scripts\dsh\ai_hfss_workstation.ps1 apply
.EXAMPLE
    .\scripts\dsh\ai_hfss_workstation.ps1 apply -MissionsDb D:\aedt-agent-runs\reviewed-loop\missions.db
.EXAMPLE
    .\scripts\dsh\ai_hfss_workstation.ps1 rollback
#>
[CmdletBinding()]
param(
    [ValidateSet('check', 'apply', 'rollback')]
    [string]$Action = 'check',
    [string]$Profile = (Join-Path $env:USERPROFILE '.dsh\profiles\desktop'),
    [string]$WorkbenchUrl = 'http://127.0.0.1:8766/workbench',
    [string]$MissionsDb = '',
    [string]$Python = '',
    [string]$LogFile = ''
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
$pythonExe = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $pythonExe)) { $pythonExe = 'python' }

$installer = Join-Path $repoRoot 'scripts\dsh\install_ai_hfss_workstation.py'
$arguments = @($installer, $Action, '--repo-root', $repoRoot, '--dsh-profile', $Profile)
if ($Action -ne 'rollback') {
    $arguments += @('--workbench-url', $WorkbenchUrl)
    if ($MissionsDb -ne '') { $arguments += @('--missions-db', $MissionsDb) }
    if ($Python -ne '') { $arguments += @('--python', $Python) }
    if ($LogFile -ne '') { $arguments += @('--log-file', $LogFile) }
}

& $pythonExe @arguments
exit $LASTEXITCODE
