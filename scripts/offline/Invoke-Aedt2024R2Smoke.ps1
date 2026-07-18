[CmdletBinding()]
param(
    [string]$InstallRoot = "D:\ansys-agent",

    [ValidateRange(0, 65535)]
    [int]$Port = 50061,

    [string]$AedtVersion = "2024.2",

    [string]$AedtRoot,

    [switch]$StartAedt,

    [ValidateRange(5, 300)]
    [int]$StartupTimeoutSeconds = 120,

    [switch]$RequireActiveProject,

    [switch]$InstallDesktopEntry
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "OfflineRelease.Common.ps1")

function Resolve-AedtRoot {
    param(
        [string]$RequestedRoot,
        [Parameter(Mandatory = $true)][string]$Version
    )
    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        return [System.IO.Path]::GetFullPath($RequestedRoot)
    }
    if ($Version -notmatch "^(20[0-9]{2})\.([1-9])$") {
        throw "Cannot derive AEDT environment variable from version: $Version"
    }
    $name = "ANSYSEM_ROOT" + $Matches[1].Substring(2) + $Matches[2]
    foreach ($scope in @("Process", "User", "Machine")) {
        $value = [Environment]::GetEnvironmentVariable($name, $scope)
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return [System.IO.Path]::GetFullPath($value)
        }
    }
    throw "$name is not set. Pass -AedtRoot explicitly."
}

function Test-LoopbackPort {
    param([Parameter(Mandatory = $true)][int]$TargetPort)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", $TargetPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(500)) {
            return $false
        }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

$root = [System.IO.Path]::GetFullPath($InstallRoot)
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Installed project Python was not found: $python"
}
if (-not $StartAedt -and $Port -eq 0) {
    throw "Port 0 is only valid with -StartAedt. Pass the discovered AEDT gRPC port when attaching to an existing session."
}

$startedPid = $null
$launch = $null
if ($StartAedt) {
    if ($Port -ne 0 -and (Test-LoopbackPort -TargetPort $Port)) {
        throw "Port $Port is already listening. Omit -StartAedt to test the existing session, or choose another port."
    }
    $resolvedAedtRoot = Resolve-AedtRoot -RequestedRoot $AedtRoot -Version $AedtVersion
    $aedtExecutable = Find-AedtExecutable -AedtRoot $resolvedAedtRoot
    $launchText = Invoke-CapturedNativeProcess -FilePath $python -Arguments @(
        "-m", "aedt_agent.interactive", "live-launch",
        "--aedt-version", $AedtVersion,
        "--port", [string]$Port,
        "--install-dir", $aedtExecutable,
        "--non-graphical",
        "--timeout", [string]$StartupTimeoutSeconds
    ) -Label "assistant-owned non-graphical AEDT launch"
    $launch = $launchText | ConvertFrom-Json
    if ($launch.status -ne "succeeded") {
        throw "Assistant-owned AEDT launch did not succeed"
    }
    $startedPid = [int]$launch.session.pid
    $Port = [int]$launch.session.port
} else {
    $deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    while (-not (Test-LoopbackPort -TargetPort $Port)) {
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "AEDT gRPC port $Port did not become ready within $StartupTimeoutSeconds seconds."
        }
        Start-Sleep -Milliseconds 500
    }
}

$sessionsText = Invoke-CapturedNativeProcess -FilePath $python -Arguments @(
    "-m", "aedt_agent.interactive", "live-sessions"
) -Label "AEDT session discovery"
$sessions = $sessionsText | ConvertFrom-Json

$infoText = Invoke-CapturedNativeProcess -FilePath $python -Arguments @(
    "-m", "aedt_agent.interactive", "live-info", "--port", [string]$Port,
    "--aedt-version", $AedtVersion
) -Label "read-only AEDT attach"
$info = $infoText | ConvertFrom-Json
if ($info.status -ne "succeeded") {
    throw "AEDT read-only attach did not succeed"
}
if ($RequireActiveProject -and [string]::IsNullOrWhiteSpace([string]$info.project.active_project)) {
    throw "AEDT is connected, but no active project is open"
}

$desktopInstall = $null
if ($InstallDesktopEntry) {
    $installText = Invoke-CapturedNativeProcess -FilePath $python -Arguments @(
        "-m", "aedt_agent.desktop", "install", "--port", [string]$Port,
        "--version", $AedtVersion
    ) -Label "Automation Tab entry installation"
    $desktopInstall = $installText | ConvertFrom-Json
}

[ordered]@{
    status = "passed"
    mode = "read_only_attach"
    version = $AedtVersion
    port = $Port
    started_pid = $startedPid
    launch = $launch
    discovered_sessions = $sessions
    project = $info.project
    release = $info.release
    desktop_entry = $desktopInstall
    note = "This smoke does not edit, solve, save, or close AEDT/projects."
} | ConvertTo-Json -Depth 10
