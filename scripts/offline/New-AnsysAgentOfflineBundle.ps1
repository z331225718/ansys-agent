[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [string]$RepositoryRoot,

    [string]$PythonExe,

    [ValidatePattern("^3\.[0-9]+$")]
    [string]$TargetPython = "3.11",

    [string]$BundleName,

    [switch]$KeepExpanded
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
. (Join-Path $PSScriptRoot "OfflineRelease.Common.ps1")

function Get-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Get-CommandPath {
    param([Parameter(Mandatory = $true)][string]$Name)
    $command = Get-Command $Name -ErrorAction Stop | Select-Object -First 1
    if ($command.Source) {
        return $command.Source
    }
    return $command.Path
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

function Copy-FilteredTree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $sourcePath = (Get-FullPath $Source).TrimEnd("\")
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
        throw "Source directory does not exist: $sourcePath"
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $excludedDirectories = @(
        ".git", ".venv", "__pycache__", ".pytest_cache", ".aedt-agent",
        ".codegraph", ".reasonix", ".temp_whiteboard", "tmp", "tmp-art"
    )
    Get-ChildItem -LiteralPath $sourcePath -Recurse -Force -File | ForEach-Object {
        $relative = $_.FullName.Substring($sourcePath.Length).TrimStart("\")
        $segments = $relative -split "[\\/]"
        $skip = $false
        foreach ($segment in $segments[0..([Math]::Max(0, $segments.Count - 2))]) {
            if ($excludedDirectories -contains $segment -or $segment -like "*.egg-info") {
                $skip = $true
                break
            }
        }
        if ($skip -or $_.Name -like "*.pyc" -or $_.Name -like "*.pyo" -or $_.Name -like "*.local.*") {
            return
        }
        $target = Join-Path $Destination $relative
        $targetParent = Split-Path -Parent $target
        New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $target -Force
    }
}

function Assert-NoObviousSecrets {
    param([Parameter(Mandatory = $true)][string]$Root)
    $patterns = @(
        "sk-[A-Za-z0-9_-]{20,}",
        "-----BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"
    )
    $textExtensions = @(".json", ".yaml", ".yml", ".toml", ".md", ".py", ".ps1", ".txt")
    foreach ($file in Get-ChildItem -LiteralPath $Root -Recurse -Force -File) {
        if ($textExtensions -notcontains $file.Extension.ToLowerInvariant()) {
            continue
        }
        $content = Get-Content -LiteralPath $file.FullName -Raw
        foreach ($pattern in $patterns) {
            if ($content -match $pattern) {
                throw "Potential credential material found in release input: $($file.FullName)"
            }
        }
    }
}

function Get-RelativeBundlePath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$File
    )
    $rootPath = (Get-FullPath $Root).TrimEnd("\")
    $filePath = Get-FullPath $File
    if (-not $filePath.StartsWith($rootPath + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "File is outside bundle root: $filePath"
    }
    return $filePath.Substring($rootPath.Length + 1).Replace("\", "/")
}

function Remove-SafeStagingDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$StagingPath,
        [Parameter(Mandatory = $true)][string]$ExpectedParent
    )
    $resolvedStaging = Get-FullPath $StagingPath
    $resolvedParent = (Get-FullPath $ExpectedParent).TrimEnd("\")
    $actualParent = (Split-Path -Parent $resolvedStaging).TrimEnd("\")
    $leaf = Split-Path -Leaf $resolvedStaging
    if (-not $actualParent.Equals($resolvedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove staging directory outside output directory: $resolvedStaging"
    }
    if (-not $leaf.StartsWith(".building-", [System.StringComparison]::Ordinal)) {
        throw "Refusing to remove unexpected staging directory: $resolvedStaging"
    }
    if (Test-Path -LiteralPath $resolvedStaging) {
        Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
    }
}

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Join-Path $PSScriptRoot "..\.."
}
$repository = (Resolve-Path -LiteralPath $RepositoryRoot).Path
foreach ($required in @("pyproject.toml", "uv.lock", "src\aedt_agent")) {
    if (-not (Test-Path -LiteralPath (Join-Path $repository $required))) {
        throw "Repository root is missing ${required}: $repository"
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $pyLauncher = Get-CommandPath "py"
    $selected = & $pyLauncher "-$TargetPython" -c "import sys; print(sys.executable)"
    if ($LASTEXITCODE -ne 0 -or -not $selected) {
        throw "Python $TargetPython x64 is required on the connected build machine. Pass -PythonExe explicitly."
    }
    $PythonExe = ($selected | Select-Object -Last 1).Trim()
}
$python = (Resolve-Path -LiteralPath $PythonExe).Path
$pythonInfoText = & $python -c "import json,platform,struct,sys; print(json.dumps({'version':f'{sys.version_info.major}.{sys.version_info.minor}','bits':struct.calcsize('P')*8,'implementation':platform.python_implementation(),'executable':sys.executable}))"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to inspect target Python: $python"
}
$pythonInfo = $pythonInfoText | ConvertFrom-Json
if ($pythonInfo.version -ne $TargetPython -or $pythonInfo.bits -ne 64 -or $pythonInfo.implementation -ne "CPython") {
    throw "Bundle builds require CPython $TargetPython x64; got $($pythonInfo.implementation) $($pythonInfo.version) $($pythonInfo.bits)-bit."
}

$uv = Get-CommandPath "uv"
Invoke-Checked -FilePath $uv -Arguments @("lock", "--check", "--directory", $repository) -Label "uv lock check"

$projectInfoText = & $python -c "import json,sys,tomllib; p=tomllib.load(open(sys.argv[1],'rb'))['project']; print(json.dumps({'version':p['version'],'desktop':p.get('optional-dependencies',{}).get('desktop',[])}))" (Join-Path $repository "pyproject.toml")
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read pyproject.toml"
}
$projectInfo = $projectInfoText | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace($BundleName)) {
    $pythonTag = $TargetPython.Replace(".", "")
    $BundleName = "ansys-agent-offline-$($projectInfo.version)-win-amd64-py$pythonTag"
}
if ($BundleName -notmatch "^[A-Za-z0-9._-]+$") {
    throw "BundleName may contain only letters, digits, dot, underscore, and hyphen."
}

$output = Get-FullPath $OutputDirectory
New-Item -ItemType Directory -Path $output -Force | Out-Null
$finalExpanded = Join-Path $output $BundleName
$zipPath = Join-Path $output ($BundleName + ".zip")
$zipHashPath = $zipPath + ".sha256"
foreach ($target in @($finalExpanded, $zipPath, $zipHashPath)) {
    if (Test-Path -LiteralPath $target) {
        throw "Output already exists; refusing to overwrite: $target"
    }
}

$staging = Join-Path $output (".building-" + [Guid]::NewGuid().ToString("N"))
$bundleRoot = Join-Path $staging $BundleName
$runtimeRoot = Join-Path $bundleRoot "runtime"
$wheelhouse = Join-Path $bundleRoot "wheelhouse"
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
New-Item -ItemType Directory -Path $wheelhouse -Force | Out-Null

try {
    foreach ($rootFile in @("pyproject.toml", "uv.lock", "README.md", "CLAUDE.md", "ANSYS_AGENT.md")) {
        Copy-Item -LiteralPath (Join-Path $repository $rootFile) -Destination (Join-Path $runtimeRoot $rootFile)
    }
    foreach ($directory in @(
        "src", "config", "docs", "scripts", "knowledge", "nodes",
        "workflow_templates", "workflows"
    )) {
        Copy-FilteredTree -Source (Join-Path $repository $directory) -Destination (Join-Path $runtimeRoot $directory)
    }
    foreach ($benchmarkDirectory in @("reference_scripts", "tasks", "validation_scripts")) {
        Copy-FilteredTree `
            -Source (Join-Path $repository "benchmarks\$benchmarkDirectory") `
            -Destination (Join-Path $runtimeRoot "benchmarks\$benchmarkDirectory")
    }
    foreach ($skill in @("ansys-brd-via-optimization", "ansys-capability-promoter")) {
        $skillSource = Join-Path $repository ".agents\skills\$skill"
        if (Test-Path -LiteralPath $skillSource -PathType Container) {
            Copy-FilteredTree -Source $skillSource -Destination (Join-Path $runtimeRoot ".agents\skills\$skill")
        }
    }
    Assert-NoObviousSecrets -Root $runtimeRoot

    $bundleScripts = Join-Path $bundleRoot "scripts"
    Copy-FilteredTree -Source (Join-Path $repository "scripts\offline") -Destination $bundleScripts

    $desktopRequirements = Join-Path $bundleRoot "requirements-desktop.txt"
    Invoke-Checked -FilePath $uv -Arguments @(
        "export", "--frozen", "--extra", "desktop", "--no-dev", "--no-emit-project",
        "--format", "requirements.txt", "--output-file", $desktopRequirements,
        "--directory", $repository
    ) -Label "uv desktop requirements export"

    Invoke-Checked -FilePath $python -Arguments @(
        "-m", "pip", "download", "--dest", $wheelhouse, "--require-hashes",
        "--only-binary=:all:", "-r", $desktopRequirements
    ) -Label "desktop wheel download"

    $bootstrapPackages = @(
        @{ Requirement = "pip==25.3"; Pattern = "pip-25.3-*.whl" },
        @{ Requirement = "setuptools==80.9.0"; Pattern = "setuptools-80.9.0-*.whl" },
        @{ Requirement = "wheel==0.45.1"; Pattern = "wheel-0.45.1-*.whl" }
    )
    Invoke-Checked -FilePath $python -Arguments @(
        "-m", "pip", "download", "--dest", $wheelhouse, "--only-binary=:all:",
        $bootstrapPackages[0].Requirement, $bootstrapPackages[1].Requirement, $bootstrapPackages[2].Requirement
    ) -Label "bootstrap wheel download"

    $bootstrapLines = @()
    foreach ($package in $bootstrapPackages) {
        $matches = @(Get-ChildItem -LiteralPath $wheelhouse -File -Filter $package.Pattern)
        if ($matches.Count -ne 1) {
            throw "Expected one wheel matching $($package.Pattern); found $($matches.Count)."
        }
        $hash = (Get-FileHash -LiteralPath $matches[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $bootstrapLines += "$($package.Requirement) --hash=sha256:$hash"
    }
    $bootstrapRequirements = Join-Path $bundleRoot "requirements-bootstrap.txt"
    Write-Utf8NoBomLines -Path $bootstrapRequirements -Lines $bootstrapLines

    $nonWheels = @(Get-ChildItem -LiteralPath $wheelhouse -File | Where-Object { $_.Extension -ne ".whl" })
    if ($nonWheels.Count -gt 0) {
        throw "The wheelhouse contains non-wheel artifacts: $($nonWheels.Name -join ', ')"
    }

    $gitRevision = $null
    $sourceDirty = $null
    try {
        $git = Get-CommandPath "git"
        $gitRevision = (& $git -C $repository rev-parse HEAD).Trim()
        $dirtyOutput = & $git -C $repository status --porcelain
        $sourceDirty = [bool]$dirtyOutput
    } catch {
        $gitRevision = $null
        $sourceDirty = $null
    }

    $preManifestFiles = @(Get-ChildItem -LiteralPath $bundleRoot -Recurse -Force -File)
    $manifest = [ordered]@{
        schema_version = 1
        created_utc = [DateTime]::UtcNow.ToString("o")
        project = [ordered]@{
            name = "aedt-agent"
            version = $projectInfo.version
            git_revision = $gitRevision
            source_dirty = $sourceDirty
        }
        target = [ordered]@{
            os = "windows"
            architecture = "amd64"
            python = $TargetPython
            aedt = "2024.2"
        }
        desktop_dependencies = @($projectInfo.desktop)
        install_layout = [ordered]@{
            mode = "editable-runtime"
            required_venv = ".venv"
            reason = "Desktop launcher binds to a source root and its .venv interpreter."
        }
        payload_file_count = $preManifestFiles.Count + 1
    }
    $manifestPath = Join-Path $bundleRoot "bundle.json"
    $manifestJson = $manifest | ConvertTo-Json -Depth 8
    Write-Utf8NoBomText -Path $manifestPath -Text ($manifestJson + [Environment]::NewLine)

    $checksumLines = @()
    foreach ($file in Get-ChildItem -LiteralPath $bundleRoot -Recurse -Force -File | Sort-Object FullName) {
        $relative = Get-RelativeBundlePath -Root $bundleRoot -File $file.FullName
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $checksumLines += "$hash *$relative"
    }
    $checksumPath = Join-Path $bundleRoot "SHA256SUMS"
    Write-Utf8NoBomLines -Path $checksumPath -Lines $checksumLines

    Compress-Archive -LiteralPath $bundleRoot -DestinationPath $zipPath -CompressionLevel Optimal
    $zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Utf8NoBomLines -Path $zipHashPath -Lines @(
        "$zipHash *$([System.IO.Path]::GetFileName($zipPath))"
    )

    if ($KeepExpanded) {
        Move-Item -LiteralPath $bundleRoot -Destination $finalExpanded
    }

    [ordered]@{
        status = "succeeded"
        zip = $zipPath
        zip_sha256 = $zipHash
        expanded = $(if ($KeepExpanded) { $finalExpanded } else { $null })
        target_python = $TargetPython
        source_dirty = $sourceDirty
    } | ConvertTo-Json -Depth 4
} finally {
    Remove-SafeStagingDirectory -StagingPath $staging -ExpectedParent $output
}
