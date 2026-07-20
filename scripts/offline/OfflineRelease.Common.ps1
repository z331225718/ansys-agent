function New-StrictUtf8NoBomEncoding {
    return [System.Text.UTF8Encoding]::new($false, $true)
}

function Write-Utf8NoBomText {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text
    )

    [System.IO.File]::WriteAllText(
        [System.IO.Path]::GetFullPath($Path),
        $Text,
        (New-StrictUtf8NoBomEncoding)
    )
}

function Write-Utf8NoBomLines {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Lines
    )

    [System.IO.File]::WriteAllLines(
        [System.IO.Path]::GetFullPath($Path),
        $Lines,
        (New-StrictUtf8NoBomEncoding)
    )
}

function Read-StrictUtf8Text {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.File]::ReadAllText(
        [System.IO.Path]::GetFullPath($Path),
        (New-StrictUtf8NoBomEncoding)
    )
}

function Read-StrictUtf8Lines {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.File]::ReadAllLines(
        [System.IO.Path]::GetFullPath($Path),
        (New-StrictUtf8NoBomEncoding)
    )
}

function Remove-SafeTemporaryFile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedPrefix,
        [Parameter(Mandatory = $true)][string]$ExpectedExtension
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd("\")
    $parent = (Split-Path -Parent $fullPath).TrimEnd("\")
    $leaf = Split-Path -Leaf $fullPath
    if (-not $parent.Equals($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove temporary file outside the OS temp directory: $fullPath"
    }
    if (-not $leaf.StartsWith($ExpectedPrefix, [System.StringComparison]::Ordinal) -or
        -not $leaf.EndsWith($ExpectedExtension, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a temporary file with an unexpected name: $fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        $item = Get-Item -LiteralPath $fullPath -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to remove a temporary reparse point: $fullPath"
        }
        Remove-Item -LiteralPath $fullPath -Force
    }
}

function Invoke-CapturedNativeProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $stdoutPrefix = "ansys-agent-process-stdout-"
    $stderrPrefix = "ansys-agent-process-stderr-"
    $stdoutPath = Join-Path ([System.IO.Path]::GetTempPath()) ($stdoutPrefix + [Guid]::NewGuid().ToString("N") + ".txt")
    $stderrPath = Join-Path ([System.IO.Path]::GetTempPath()) ($stderrPrefix + [Guid]::NewGuid().ToString("N") + ".txt")
    $stdout = ""
    $stderr = ""
    $exitCode = $null
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 promotes native stderr to NativeCommandError when
        # ErrorActionPreference is Stop, even when stderr is redirected.
        $ErrorActionPreference = "Continue"
        & $FilePath @Arguments 1> $stdoutPath 2> $stderrPath
        $exitCode = $LASTEXITCODE
        if (Test-Path -LiteralPath $stdoutPath -PathType Leaf) {
            $stdout = [System.IO.File]::ReadAllText($stdoutPath)
        }
        if (Test-Path -LiteralPath $stderrPath -PathType Leaf) {
            $stderr = [System.IO.File]::ReadAllText($stderrPath)
        }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Remove-SafeTemporaryFile -Path $stdoutPath -ExpectedPrefix $stdoutPrefix -ExpectedExtension ".txt"
        Remove-SafeTemporaryFile -Path $stderrPath -ExpectedPrefix $stderrPrefix -ExpectedExtension ".txt"
    }

    if ($exitCode -ne 0) {
        $details = @($stdout.Trim(), $stderr.Trim()) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        $suffix = if ($details.Count -gt 0) {
            "`n" + ($details -join [Environment]::NewLine)
        } else {
            ""
        }
        throw "$Label failed with exit code $exitCode$suffix"
    }
    return $stdout.Trim()
}

function Find-AedtExecutable {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$AedtRoot
    )

    $root = [System.IO.Path]::GetFullPath($AedtRoot)
    $candidates = @(
        (Join-Path $root "ansysedt.exe"),
        (Join-Path $root "Win64\ansysedt.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    throw "AEDT executable was not found under: $root"
}
