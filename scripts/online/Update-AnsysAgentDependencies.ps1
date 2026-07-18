[CmdletBinding()]
param(
    [string]$InstallRoot = "D:\ansys-agent",

    [string]$PythonExe,

    [switch]$SkipApiMemoryRebuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\offline\OfflineRelease.Common.ps1")

$expectedPyAedt = "1.3.0"
$expectedPyEdb = "0.80.2"
$root = [System.IO.Path]::GetFullPath($InstallRoot)
if (-not (Test-Path -LiteralPath (Join-Path $root "pyproject.toml") -PathType Leaf)) {
    throw "Installed project root was not found: $root"
}

$python = if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    Join-Path $root ".venv\Scripts\python.exe"
} else {
    [System.IO.Path]::GetFullPath($PythonExe)
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python was not found: $python"
}

$editableTarget = $root + "[desktop]"
Invoke-CapturedNativeProcess -FilePath $python -Arguments @(
    "-m", "pip", "install",
    "--upgrade",
    "--upgrade-strategy", "only-if-needed",
    "--editable", $editableTarget
) -Label "online Ansys dependency update" | Out-Null

Invoke-CapturedNativeProcess -FilePath $python -Arguments @(
    "-m", "pip", "check"
) -Label "Python dependency check" | Out-Null

$probeCode = @"
import importlib.metadata as metadata
import json

import ansys.aedt.core
import pyedb

versions = {
    "pyaedt": metadata.version("pyaedt"),
    "pyedb": metadata.version("pyedb"),
    "ansys-pythonnet": metadata.version("ansys-pythonnet"),
}
print(json.dumps(versions, sort_keys=True))
"@
$probePrefix = "ansys-agent-online-dependency-probe-"
$probePath = Join-Path ([System.IO.Path]::GetTempPath()) (
    $probePrefix + [Guid]::NewGuid().ToString("N") + ".py"
)
try {
    [System.IO.File]::WriteAllText(
        $probePath,
        $probeCode,
        [System.Text.UTF8Encoding]::new($false)
    )
    $probeText = Invoke-CapturedNativeProcess -FilePath $python -Arguments @(
        $probePath
    ) -Label "updated Ansys dependency import check"
} finally {
    Remove-SafeTemporaryFile `
        -Path $probePath `
        -ExpectedPrefix $probePrefix `
        -ExpectedExtension ".py"
}
$versions = $probeText | ConvertFrom-Json
if ($versions.pyaedt -ne $expectedPyAedt -or $versions.pyedb -ne $expectedPyEdb) {
    throw "Unexpected installed versions: pyaedt=$($versions.pyaedt), pyedb=$($versions.pyedb)"
}

$apiMemory = "skipped"
if (-not $SkipApiMemoryRebuild) {
    $memoryText = Invoke-CapturedNativeProcess -FilePath $python -Arguments @(
        "-m", "aedt_agent.knowledge.api_memory_cli", "prepare", "--force"
    ) -Label "API Memory rebuild"
    $memory = $memoryText | ConvertFrom-Json
    if ($memory.status -ne "ready") {
        throw "API Memory rebuild did not become ready"
    }
    $apiMemory = "ready"
}

[ordered]@{
    status = "updated"
    install_root = $root
    python = $python
    pyaedt = [string]$versions.pyaedt
    pyedb = [string]$versions.pyedb
    dotnet_runtime = [string]$versions."ansys-pythonnet"
    api_memory = $apiMemory
    restart_assistant_required = $true
} | ConvertTo-Json -Depth 4
