[CmdletBinding()]
param(
    [string]$InstallRoot = "D:\ansys-agent",

    [string]$BundleRoot,

    [string]$AedtVersion = "2024.2",

    [string]$AedtRoot,

    [string]$ClaudeExecutable = "claude",

    [switch]$RequireClaude,

    [switch]$SkipAedtCheck,

    [switch]$SkipApiMemoryCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "OfflineRelease.Common.ps1")

function Invoke-Captured {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $output = & $FilePath @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE`n$($output -join [Environment]::NewLine)"
    }
    return ($output -join [Environment]::NewLine)
}

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
    $code = $Matches[1].Substring(2) + $Matches[2]
    $name = "ANSYSEM_ROOT$code"
    foreach ($scope in @("Process", "User", "Machine")) {
        $value = [Environment]::GetEnvironmentVariable($name, $scope)
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return [System.IO.Path]::GetFullPath($value)
        }
    }
    throw "$name is not set. Pass -AedtRoot explicitly."
}

$root = [System.IO.Path]::GetFullPath($InstallRoot)
$python = Join-Path $root ".venv\Scripts\python.exe"
$installRecordPath = Join-Path $root ".aedt-agent\install.json"
foreach ($required in @(
    $python,
    (Join-Path $root "pyproject.toml"),
    (Join-Path $root "src\aedt_agent"),
    $installRecordPath
)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Installed runtime is missing: $required"
    }
}
$installRecord = Read-StrictUtf8Text -Path $installRecordPath | ConvertFrom-Json

if (-not [string]::IsNullOrWhiteSpace($BundleRoot)) {
    $installer = Join-Path $BundleRoot "scripts\Install-AnsysAgentOffline.ps1"
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "Bundle verification script is missing: $installer"
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -BundleRoot $BundleRoot -VerifyOnly | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Bundle verification failed"
    }
}

$versionJson = Invoke-Captured -FilePath $python -Arguments @(
    "-c",
    "import importlib.metadata as m,json,struct,sys; names=['aedt-agent','pyaedt','pyedb','ansys-pythonnet','fastmcp','codebase-memory-mcp']; print(json.dumps({'python':f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}','bits':struct.calcsize('P')*8,'packages':{n:m.version(n) for n in names}}))"
) -Label "runtime version inspection"
$versions = $versionJson | ConvertFrom-Json
if ($versions.bits -ne 64) {
    throw "Installed virtual environment is not 64-bit"
}

$nativeToolStatus = "not-required"
$nativeToolVersion = $null
$nativeToolPath = Join-Path $root ".venv\Scripts\codebase-memory-mcp.exe"
$nativeToolRecord = $null
$nativeToolsProperty = $installRecord.PSObject.Properties["native_tools"]
if ($null -ne $nativeToolsProperty -and $null -ne $nativeToolsProperty.Value) {
    $nativeToolProperty = $nativeToolsProperty.Value.PSObject.Properties["codebase_memory_mcp"]
    if ($null -ne $nativeToolProperty) {
        $nativeToolRecord = $nativeToolProperty.Value
    }
}
if (-not [string]::IsNullOrWhiteSpace($BundleRoot) -or $null -ne $nativeToolRecord) {
    if ($null -eq $nativeToolRecord) {
        throw "Offline installation record is missing the bundled codebase-memory-mcp native executable"
    }
    $recordedNativePath = [System.IO.Path]::GetFullPath([string]$nativeToolRecord.path)
    if (-not $recordedNativePath.Equals(
        [System.IO.Path]::GetFullPath($nativeToolPath),
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Installed codebase-memory-mcp path does not point to the project virtual environment"
    }
    if (-not (Test-Path -LiteralPath $nativeToolPath -PathType Leaf)) {
        throw "Bundled codebase-memory-mcp native executable is missing after installation"
    }
    $nativeToolItem = Get-Item -LiteralPath $nativeToolPath -Force
    if ($nativeToolItem.Length -lt 1MB) {
        throw "codebase-memory-mcp is still a network downloader shim instead of the bundled native executable"
    }
    $nativeToolHash = (
        Get-FileHash -LiteralPath $nativeToolPath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($nativeToolHash -ne ([string]$nativeToolRecord.sha256).ToLowerInvariant()) {
        throw "Installed codebase-memory-mcp native executable SHA256 mismatch"
    }
    $nativeToolVersionText = Invoke-Captured `
        -FilePath $nativeToolPath `
        -Arguments @("--version") `
        -Label "codebase-memory-mcp native version check"
    $nativeToolVersion = [string]$nativeToolRecord.version
    if ($nativeToolVersionText -notmatch [regex]::Escape($nativeToolVersion)) {
        throw "Bundled codebase-memory-mcp native executable reported an unexpected version"
    }
    $nativeToolStatus = "bundled-native-verified"
}

$dependencyImportProbe = @'
import importlib
import json
import os

os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")
modules = ("ansys.aedt.core", "pyedb", "clr", "fastmcp", "codebase_memory_mcp")
results = {}
for name in modules:
    try:
        module = importlib.import_module(name)
    except Exception as exc:
        results[name] = {
            "imported": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
        }
    else:
        results[name] = {
            "imported": True,
            "file": str(getattr(module, "__file__", ""))[:1000],
        }
status = "passed" if all(item["imported"] for item in results.values()) else "failed"
print("ANSYS_AGENT_IMPORT_STATUS=" + json.dumps(
    {"status": status, "modules": results},
    ensure_ascii=True,
    separators=(",", ":"),
))
'@
$dependencyImportProbePrefix = "ansys-agent-dependency-import-"
$dependencyImportProbePath = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    ($dependencyImportProbePrefix + [Guid]::NewGuid().ToString("N") + ".py")
try {
    Write-Utf8NoBomText -Path $dependencyImportProbePath -Text $dependencyImportProbe
    $dependencyImportOutput = Invoke-Captured -FilePath $python -Arguments @(
        $dependencyImportProbePath
    ) -Label "desktop dependency import preflight"
} finally {
    Remove-SafeTemporaryFile `
        -Path $dependencyImportProbePath `
        -ExpectedPrefix $dependencyImportProbePrefix `
        -ExpectedExtension ".py"
}
$dependencyImportLine = @(
    $dependencyImportOutput -split "`r?`n" |
        Where-Object { $_.StartsWith("ANSYS_AGENT_IMPORT_STATUS=", [System.StringComparison]::Ordinal) }
) | Select-Object -Last 1
if ([string]::IsNullOrWhiteSpace($dependencyImportLine)) {
    throw "Desktop dependency import preflight did not return structured status"
}
$dependencyImports = $dependencyImportLine.Substring("ANSYS_AGENT_IMPORT_STATUS=".Length) | ConvertFrom-Json
if ($dependencyImports.status -ne "passed") {
    $failedImports = @(
        $dependencyImports.modules.PSObject.Properties |
            Where-Object { $_.Value.imported -ne $true } |
            ForEach-Object { "$($_.Name): $($_.Value.error_type): $($_.Value.error)" }
    )
    throw "Desktop dependency import preflight failed: $($failedImports -join '; ')"
}

Invoke-Captured -FilePath $python -Arguments @("-m", "pip", "check") -Label "pip dependency check" | Out-Null
Invoke-Captured -FilePath $python -Arguments @("-m", "aedt_agent.interactive", "capabilities-v2") -Label "assistant capability check" | Out-Null
Invoke-Captured -FilePath $python -Arguments @("-m", "aedt_agent.desktop", "--help") -Label "desktop CLI check" | Out-Null

$apiMemoryStatus = "skipped"
if (-not $SkipApiMemoryCheck) {
    $apiText = Invoke-Captured -FilePath $python -Arguments @(
        "-m", "aedt_agent.knowledge.api_memory_cli", "status"
    ) -Label "API Memory status"
    $apiStatus = $apiText | ConvertFrom-Json
    if ($apiStatus.ready -eq $true) {
        $apiMemoryStatus = "ready"
    } else {
        $apiMemoryStatus = [string]$apiStatus.status
    }
}

$aedtExecutable = $null
if (-not $SkipAedtCheck) {
    $resolvedAedtRoot = Resolve-AedtRoot -RequestedRoot $AedtRoot -Version $AedtVersion
    $aedtExecutable = Find-AedtExecutable -AedtRoot $resolvedAedtRoot
}

$claudePath = $null
$claudeVersion = $null
$claudeStatus = "missing"
$claudeMissingFlags = @()
$claudeError = $null
try {
    $claudeCommand = Get-Command $ClaudeExecutable -ErrorAction Stop | Select-Object -First 1
    $claudePath = $(if ($claudeCommand.Source) { $claudeCommand.Source } else { $claudeCommand.Path })
    $claudeVersion = Invoke-Captured -FilePath $claudePath -Arguments @("--version") -Label "Claude Code version"
    $claudeHelp = Invoke-Captured -FilePath $claudePath -Arguments @("--help") -Label "Claude Code help"
    $requiredFlagPatterns = [ordered]@{
        "--bare" = [regex]::Escape("--bare")
        "--settings" = [regex]::Escape("--settings")
        "--setting-sources" = [regex]::Escape("--setting-sources")
        "--mcp-config" = [regex]::Escape("--mcp-config")
        "--strict-mcp-config" = [regex]::Escape("--strict-mcp-config")
        "--tools" = [regex]::Escape("--tools")
        "--allowedTools" = [regex]::Escape("--allowedTools")
        "--disallowedTools" = [regex]::Escape("--disallowedTools")
        "--disable-slash-commands" = [regex]::Escape("--disable-slash-commands")
        "--no-chrome" = [regex]::Escape("--no-chrome")
        "--append-system-prompt-file" = "--append-system-prompt(?:-file|\[-file\])"
        "--permission-mode" = [regex]::Escape("--permission-mode")
    }
    $claudeMissingFlags = @(
        $requiredFlagPatterns.Keys |
            Where-Object { $claudeHelp -notmatch $requiredFlagPatterns[$_] }
    )
    if ($claudeMissingFlags.Count -gt 0) {
        $claudeStatus = "incompatible"
        $claudeError = "Claude Code is missing required flags: $($claudeMissingFlags -join ', ')"
    } else {
        $probeSettings = [System.IO.Path]::GetTempFileName()
        $probeMcp = [System.IO.Path]::GetTempFileName()
        $probePrompt = [System.IO.Path]::GetTempFileName()
        try {
            Set-Content -LiteralPath $probeSettings -Value '{"env":{}}' -Encoding UTF8
            Set-Content -LiteralPath $probeMcp -Value '{"mcpServers":{}}' -Encoding UTF8
            Set-Content -LiteralPath $probePrompt -Value '# Ansys Agent offline preflight' -Encoding UTF8
            Invoke-Captured -FilePath $claudePath -Arguments @(
                "--bare",
                "--settings", $probeSettings,
                "--setting-sources=",
                "--mcp-config", $probeMcp,
                "--strict-mcp-config",
                "--tools", "AskUserQuestion",
                "--allowedTools", "AskUserQuestion",
                "--disallowedTools", "Bash",
                "--disable-slash-commands",
                "--no-chrome",
                "--append-system-prompt-file", $probePrompt,
                "--permission-mode", "manual",
                "--version"
            ) -Label "Claude Code option parser preflight" | Out-Null
        } finally {
            foreach ($probeFile in @($probeSettings, $probeMcp, $probePrompt)) {
                Remove-Item -LiteralPath $probeFile -Force -ErrorAction SilentlyContinue
            }
        }
        $claudeStatus = "compatible"
    }
} catch {
    $claudeError = $_.Exception.Message
}
if ($claudeStatus -ne "compatible") {
    if ($RequireClaude) {
        throw $claudeError
    }
    Write-Warning "$claudeError. Install a compatible approved offline distribution and rerun with -RequireClaude."
}

[ordered]@{
    status = "passed"
    install_root = $root
    python = $versions.python
    packages = $versions.packages
    dependency_import_status = $dependencyImports.status
    dependency_imports = $dependencyImports.modules
    codebase_memory_mcp_native = [ordered]@{
        status = $nativeToolStatus
        version = $nativeToolVersion
        path = $(if ($nativeToolStatus -eq "bundled-native-verified") { $nativeToolPath } else { $null })
    }
    api_memory = $apiMemoryStatus
    aedt_version = $AedtVersion
    aedt_executable = $aedtExecutable
    claude_status = $claudeStatus
    claude_executable = $claudePath
    claude_version = $claudeVersion
    claude_missing_flags = $claudeMissingFlags
    claude_error = $claudeError
} | ConvertTo-Json -Depth 6
