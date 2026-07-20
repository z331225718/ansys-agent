[CmdletBinding()]
param(
    [string]$BundleRoot,

    [string]$InstallRoot = "D:\ansys-agent",

    [string]$PythonExe,

    [switch]$VerifyOnly,

    [switch]$SkipKnowledgePrepare
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
. (Join-Path $PSScriptRoot "OfflineRelease.Common.ps1")

function Get-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Assert-PathInsideRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Candidate
    )
    $rootPath = (Get-FullPath $Root).TrimEnd("\")
    $candidatePath = Get-FullPath $Candidate
    if (-not $candidatePath.StartsWith($rootPath + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Checksum path escapes bundle root: $candidatePath"
    }
    $parent = Split-Path -Parent $candidatePath
    while (-not $parent.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        $parentItem = Get-Item -LiteralPath $parent -Force -ErrorAction Stop
        if (($parentItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Bundle payload may not traverse a reparse point: $parent"
        }
        $next = Split-Path -Parent $parent
        if ($next -eq $parent -or [string]::IsNullOrWhiteSpace($next)) {
            throw "Unable to prove checksum path containment: $candidatePath"
        }
        $parent = $next
    }
    return $candidatePath
}

function Test-BundleIntegrity {
    param([Parameter(Mandatory = $true)][string]$Root)
    $bundlePath = (Resolve-Path -LiteralPath $Root).Path
    $checksumPath = Join-Path $bundlePath "SHA256SUMS"
    $manifestPath = Join-Path $bundlePath "bundle.json"
    foreach ($required in @(
        $checksumPath,
        $manifestPath,
        (Join-Path $bundlePath "requirements-desktop.txt"),
        (Join-Path $bundlePath "requirements-bootstrap.txt"),
        (Join-Path $bundlePath "runtime\pyproject.toml"),
        (Join-Path $bundlePath "runtime\src\aedt_agent"),
        (Join-Path $bundlePath "runtime\knowledge"),
        (Join-Path $bundlePath "runtime\nodes"),
        (Join-Path $bundlePath "runtime\workflow_templates"),
        (Join-Path $bundlePath "runtime\workflows"),
        (Join-Path $bundlePath "wheelhouse")
    )) {
        if (-not (Test-Path -LiteralPath $required)) {
            throw "Bundle is missing required payload: $required"
        }
    }

    $verified = 0
    $listedPaths = @{}
    foreach ($line in Read-StrictUtf8Lines -Path $checksumPath) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        if ($line -notmatch "^([0-9a-fA-F]{64}) \*(.+)$") {
            throw "Invalid SHA256SUMS line: $line"
        }
        $expected = $Matches[1].ToLowerInvariant()
        $relative = $Matches[2].Replace("/", "\")
        if ([System.IO.Path]::IsPathRooted($relative) -or $relative -match "(^|\\)\.\.?(\\|$)" -or $relative.Contains(":")) {
            throw "Unsafe checksum path: $relative"
        }
        $pathKey = $relative.ToLowerInvariant()
        if ($listedPaths.ContainsKey($pathKey)) {
            throw "Duplicate checksum path: $relative"
        }
        $listedPaths[$pathKey] = $true
        $candidate = Assert-PathInsideRoot -Root $bundlePath -Candidate (Join-Path $bundlePath $relative)
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "Bundle file is missing: $relative"
        }
        $item = Get-Item -LiteralPath $candidate -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Bundle checksum target may not be a reparse point: $relative"
        }
        $actual = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) {
            throw "SHA256 mismatch for $relative"
        }
        $verified += 1
    }
    if ($verified -lt 1) {
        throw "SHA256SUMS did not contain any files"
    }
    $actualPayloads = @(
        Get-ChildItem -LiteralPath $bundlePath -Recurse -Force -File |
            Where-Object { $_.FullName -ne $checksumPath }
    )
    if ($actualPayloads.Count -ne $verified) {
        throw "Bundle contains unlisted or missing payload files"
    }
    foreach ($payload in $actualPayloads) {
        $relative = $payload.FullName.Substring($bundlePath.TrimEnd("\").Length + 1).ToLowerInvariant()
        if (-not $listedPaths.ContainsKey($relative)) {
            throw "Bundle contains unlisted payload file: $relative"
        }
    }

    $manifest = Read-StrictUtf8Text -Path $manifestPath | ConvertFrom-Json
    if ($manifest.schema_version -notin @(1, 2) -or $manifest.project.name -ne "aedt-agent") {
        throw "Unsupported offline bundle manifest"
    }
    if ($manifest.target.os -ne "windows" -or $manifest.target.architecture -ne "amd64") {
        throw "This installer accepts only Windows amd64 bundles"
    }
    if ($manifest.payload_file_count -ne $verified) {
        throw "Bundle manifest expected $($manifest.payload_file_count) files but SHA256SUMS verified $verified"
    }
    if ($manifest.schema_version -ge 2) {
        $nativeTool = $null
        $nativeToolsProperty = $manifest.PSObject.Properties["native_tools"]
        if ($null -ne $nativeToolsProperty -and $null -ne $nativeToolsProperty.Value) {
            $nativeToolProperty = $nativeToolsProperty.Value.PSObject.Properties["codebase_memory_mcp"]
            if ($null -ne $nativeToolProperty) {
                $nativeTool = $nativeToolProperty.Value
            }
        }
        if ($null -eq $nativeTool -or
            [string]::IsNullOrWhiteSpace([string]$nativeTool.version) -or
            $nativeTool.platform -ne "windows-amd64" -or
            [string]::IsNullOrWhiteSpace([string]$nativeTool.path) -or
            [string]$nativeTool.sha256 -notmatch "^[0-9a-fA-F]{64}$") {
            throw "Bundle manifest is missing the pinned codebase-memory-mcp native executable"
        }
        $nativeToolRelative = ([string]$nativeTool.path).Replace("/", "\")
        if ([System.IO.Path]::IsPathRooted($nativeToolRelative) -or
            $nativeToolRelative -match "(^|\\)\.\.?(\\|$)" -or
            $nativeToolRelative.Contains(":")) {
            throw "Unsafe native tool path: $nativeToolRelative"
        }
        $nativeToolPath = Assert-PathInsideRoot `
            -Root $bundlePath `
            -Candidate (Join-Path $bundlePath $nativeToolRelative)
        if (-not (Test-Path -LiteralPath $nativeToolPath -PathType Leaf)) {
            throw "Bundled codebase-memory-mcp executable is missing"
        }
        $nativeToolHash = (
            Get-FileHash -LiteralPath $nativeToolPath -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        if ($nativeToolHash -ne ([string]$nativeTool.sha256).ToLowerInvariant()) {
            throw "Bundled codebase-memory-mcp executable does not match bundle.json"
        }
    }
    return [ordered]@{
        root = $bundlePath
        manifest = $manifest
        verified_files = $verified
    }
}

function Resolve-TargetPython {
    param(
        [string]$RequestedPython,
        [Parameter(Mandatory = $true)][string]$RequiredVersion
    )
    if ([string]::IsNullOrWhiteSpace($RequestedPython)) {
        $launcher = Get-Command py -ErrorAction Stop | Select-Object -First 1
        $selected = & $launcher.Source "-$RequiredVersion" -c "import sys; print(sys.executable)"
        if ($LASTEXITCODE -ne 0 -or -not $selected) {
            throw "CPython $RequiredVersion x64 is required. Install it offline or pass -PythonExe."
        }
        $RequestedPython = ($selected | Select-Object -Last 1).Trim()
    }
    $resolved = (Resolve-Path -LiteralPath $RequestedPython).Path
    $infoText = & $resolved -c "import json,platform,struct,sys; print(json.dumps({'version':f'{sys.version_info.major}.{sys.version_info.minor}','bits':struct.calcsize('P')*8,'implementation':platform.python_implementation()}))"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect Python: $resolved"
    }
    $info = $infoText | ConvertFrom-Json
    if ($info.version -ne $RequiredVersion -or $info.bits -ne 64 -or $info.implementation -ne "CPython") {
        throw "Expected CPython $RequiredVersion x64; got $($info.implementation) $($info.version) $($info.bits)-bit."
    }
    return $resolved
}

function Invoke-SafeInstallRollback {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$MarkerPath,
        [Parameter(Mandatory = $true)][string]$InstallationId,
        [Parameter(Mandatory = $true)][bool]$RemoveRoot
    )
    $rootPath = Get-FullPath $Root
    $driveRoot = [System.IO.Path]::GetPathRoot($rootPath).TrimEnd("\")
    if ($rootPath.TrimEnd("\").Equals($driveRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Rollback refused a drive root: $rootPath"
    }
    $expectedMarker = Join-Path $rootPath ".ansys-agent-installing.json"
    if (-not (Get-FullPath $MarkerPath).Equals((Get-FullPath $expectedMarker), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Rollback marker is outside the installation root"
    }
    if (-not (Test-Path -LiteralPath $expectedMarker -PathType Leaf)) {
        throw "Rollback marker is missing: $expectedMarker"
    }
    $marker = Read-StrictUtf8Text -Path $expectedMarker | ConvertFrom-Json
    if ($marker.schema_version -ne 1 -or $marker.installation_id -ne $InstallationId -or
        -not ([string]$marker.install_root).Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Rollback marker does not match this installation attempt"
    }
    $rootItem = Get-Item -LiteralPath $rootPath -Force
    if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Rollback refused a reparse-point InstallRoot: $rootPath"
    }
    if ($RemoveRoot) {
        Remove-Item -LiteralPath $rootPath -Recurse -Force
    } else {
        Get-ChildItem -LiteralPath $rootPath -Force | ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Recurse -Force
        }
    }
}

if ([string]::IsNullOrWhiteSpace($BundleRoot)) {
    $BundleRoot = Split-Path -Parent $PSScriptRoot
}
$bundle = Test-BundleIntegrity -Root $BundleRoot
if ($VerifyOnly) {
    [ordered]@{
        status = "verified"
        bundle_root = $bundle.root
        verified_files = $bundle.verified_files
        project_version = $bundle.manifest.project.version
        target_python = $bundle.manifest.target.python
        target_aedt = $bundle.manifest.target.aedt
    } | ConvertTo-Json -Depth 4
    exit 0
}

$installPath = Get-FullPath $InstallRoot
$installDriveRoot = [System.IO.Path]::GetPathRoot($installPath).TrimEnd("\")
if ($installPath.TrimEnd("\").Equals($installDriveRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "InstallRoot may not be a drive root: $installPath"
}
$bundlePath = (Get-FullPath $bundle.root).TrimEnd("\")
if ($installPath.StartsWith($bundlePath + "\", [System.StringComparison]::OrdinalIgnoreCase) -or
    $bundlePath.StartsWith($installPath.TrimEnd("\") + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "InstallRoot and BundleRoot may not contain each other"
}

$installRootCreated = $false
if (Test-Path -LiteralPath $installPath) {
    $installRootItem = Get-Item -LiteralPath $installPath -Force
    if (($installRootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "InstallRoot may not be a reparse point: $installPath"
    }
    $existing = @(Get-ChildItem -LiteralPath $installPath -Force)
    if ($existing.Count -gt 0) {
        throw "InstallRoot must be absent or empty; refusing to overwrite: $installPath"
    }
} else {
    New-Item -ItemType Directory -Path $installPath -Force | Out-Null
    $installRootCreated = $true
}

$installationId = [Guid]::NewGuid().ToString("N")
$rollbackMarker = Join-Path $installPath ".ansys-agent-installing.json"
$rollbackMarkerJson = [ordered]@{
    schema_version = 1
    installation_id = $installationId
    install_root = $installPath
    bundle_root = $bundle.root
    created_utc = [DateTime]::UtcNow.ToString("o")
} | ConvertTo-Json -Depth 3
Write-Utf8NoBomText -Path $rollbackMarker -Text ($rollbackMarkerJson + [Environment]::NewLine)

try {
    $runtime = Join-Path $bundle.root "runtime"
    Get-ChildItem -LiteralPath $runtime -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $installPath -Recurse -Force
    }

    $targetPython = Resolve-TargetPython -RequestedPython $PythonExe -RequiredVersion ([string]$bundle.manifest.target.python)
    $venvPath = Join-Path $installPath ".venv"
    Invoke-Checked -FilePath $targetPython -Arguments @("-m", "venv", $venvPath) -Label "virtual environment creation"
    $venvPython = Join-Path $venvPath "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "Virtual environment did not create its Python interpreter: $venvPython"
    }

    $wheelhouse = Join-Path $bundle.root "wheelhouse"
    $commonPip = @("-m", "pip", "install", "--no-index", "--disable-pip-version-check", "--find-links", $wheelhouse)
    Invoke-Checked -FilePath $venvPython -Arguments ($commonPip + @(
        "--require-hashes", "-r", (Join-Path $bundle.root "requirements-bootstrap.txt")
    )) -Label "offline bootstrap installation"
    Invoke-Checked -FilePath $venvPython -Arguments ($commonPip + @(
        "--require-hashes", "-r", (Join-Path $bundle.root "requirements-desktop.txt")
    )) -Label "offline desktop dependency installation"
    Invoke-Checked -FilePath $venvPython -Arguments @(
        "-m", "pip", "install", "--no-index", "--disable-pip-version-check",
        "--no-deps", "--no-build-isolation", "-e", $installPath
    ) -Label "aedt-agent editable installation"

    $nativeToolRecord = $null
    if ($bundle.manifest.schema_version -ge 2) {
        $nativeTool = $bundle.manifest.native_tools.codebase_memory_mcp
        $nativeToolSource = Assert-PathInsideRoot `
            -Root $bundle.root `
            -Candidate (Join-Path $bundle.root ([string]$nativeTool.path).Replace("/", "\"))
        $nativeToolDestination = Join-Path $venvPath "Scripts\codebase-memory-mcp.exe"
        Copy-Item -LiteralPath $nativeToolSource -Destination $nativeToolDestination -Force
        $installedNativeHash = (
            Get-FileHash -LiteralPath $nativeToolDestination -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        if ($installedNativeHash -ne ([string]$nativeTool.sha256).ToLowerInvariant()) {
            throw "Installed codebase-memory-mcp native executable SHA256 mismatch"
        }
        $nativeVersionOutput = & $nativeToolDestination "--version"
        if ($LASTEXITCODE -ne 0 -or
            ($nativeVersionOutput -join "`n") -notmatch [regex]::Escape([string]$nativeTool.version)) {
            throw "Installed codebase-memory-mcp native executable failed its version check"
        }
        $nativeToolRecord = [ordered]@{
            version = [string]$nativeTool.version
            path = $nativeToolDestination
            sha256 = $installedNativeHash
            source = "bundled-native-executable"
        }
    }
    Invoke-Checked -FilePath $venvPython -Arguments @("-m", "pip", "check") -Label "pip dependency check"

    $knowledgeReady = $false
    $knowledgeError = $null
    if (-not $SkipKnowledgePrepare) {
        try {
            Invoke-Checked -FilePath $venvPython -Arguments @(
                "-m", "aedt_agent.knowledge.api_memory_cli", "prepare"
            ) -Label "API Memory preparation"
            $knowledgeReady = $true
        } catch {
            $knowledgeError = $_.Exception.Message
            Write-Warning "API Memory preparation failed. Known Harness capabilities remain available: $knowledgeError"
        }
    }

    $stateDirectory = Join-Path $installPath ".aedt-agent"
    New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
    $installRecord = [ordered]@{
        schema_version = 1
        installed_utc = [DateTime]::UtcNow.ToString("o")
        bundle = [ordered]@{
            project_version = $bundle.manifest.project.version
            git_revision = $bundle.manifest.project.git_revision
            source_dirty = $bundle.manifest.project.source_dirty
            verified_files = $bundle.verified_files
        }
        install_root = $installPath
        python = $targetPython
        venv_python = $venvPython
        native_tools = [ordered]@{
            codebase_memory_mcp = $nativeToolRecord
        }
        knowledge_ready = $knowledgeReady
        knowledge_error = $knowledgeError
    }
    $installRecordJson = $installRecord | ConvertTo-Json -Depth 6
    Write-Utf8NoBomText `
        -Path (Join-Path $stateDirectory "install.json") `
        -Text ($installRecordJson + [Environment]::NewLine)
    Remove-Item -LiteralPath $rollbackMarker -Force

    [ordered]@{
        status = "installed"
        install_root = $installPath
        python = $venvPython
        project_version = $bundle.manifest.project.version
        target_aedt = $bundle.manifest.target.aedt
        codebase_memory_mcp = $nativeToolRecord
        knowledge_ready = $knowledgeReady
        knowledge_error = $knowledgeError
        next = "Run scripts\offline\Test-AnsysAgentOffline.ps1, then Invoke-Aedt2024R2Smoke.ps1."
    } | ConvertTo-Json -Depth 4
} catch {
    $installError = $_
    try {
        Invoke-SafeInstallRollback -Root $installPath -MarkerPath $rollbackMarker `
            -InstallationId $installationId -RemoveRoot $installRootCreated
        Write-Warning "Installation failed; the partial InstallRoot was safely rolled back."
    } catch {
        Write-Error "Installation failed and automatic rollback also failed. Keep the marker for diagnosis: $rollbackMarker. Rollback error: $($_.Exception.Message)"
    }
    throw $installError
}
