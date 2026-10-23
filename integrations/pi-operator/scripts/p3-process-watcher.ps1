param(
  [Parameter(Mandatory = $true)][string]$StopFile,
  [int]$MaxEvents = 10000
)

$ErrorActionPreference = "Stop"
$startSubscription = $null
$stopSubscription = $null
$eventCount = 0
$stage = "subscribe_start"
$stopRequestedAt = $null
$lastEventAt = Get-Date
$namespace = "root/cimv2"

function Write-Record([hashtable]$Record) {
  [Console]::Out.WriteLine(($Record | ConvertTo-Json -Compress -Depth 4))
  [Console]::Out.Flush()
}

function Write-ErrorRecord([string]$Code, [string]$Detail = "") {
  Write-Record @{ type = "error"; code = $Code; detail = $Detail }
}

try {
  $startSubscription = Register-CimIndicationEvent -Namespace $namespace -Query "SELECT * FROM Win32_ProcessStartTrace" -SourceIdentifier "aedt-p3-process-start"
  $stage = "subscribe_stop"
  $stopSubscription = Register-CimIndicationEvent -Namespace $namespace -Query "SELECT * FROM Win32_ProcessStopTrace" -SourceIdentifier "aedt-p3-process-stop"
  $stage = "event_loop"
  Write-Record @{ type = "ready"; namespace = $namespace; start_subscription = $true; stop_subscription = $true }

  while ($true) {
    if ($null -eq $stopRequestedAt -and (Test-Path -LiteralPath $StopFile)) {
      $stopRequestedAt = Get-Date
      $lastEventAt = $stopRequestedAt
    }
    $queued = Wait-Event -Timeout 1
    if ($null -eq $queued) {
      if ($null -ne $stopRequestedAt -and ((Get-Date) - $lastEventAt).TotalSeconds -ge 2) { break }
      continue
    }
    try {
      $eventCount += 1
      $lastEventAt = Get-Date
      if ($eventCount -gt $MaxEvents) {
        Write-ErrorRecord "event_overflow"
        exit 2
      }
      $source = $queued.SourceIdentifier
      $trace = $queued.SourceEventArgs.NewEvent
      if ($source -eq "aedt-p3-process-start") {
        Write-Record @{
          type = "start"; pid = [string]$trace.ProcessID; parent_pid = [string]$trace.ParentProcessID
          name = [string]$trace.ProcessName; time_created = [string]$trace.TIME_CREATED
        }
      } elseif ($source -eq "aedt-p3-process-stop") {
        Write-Record @{
          type = "stop"; pid = [string]$trace.ProcessID; name = [string]$trace.ProcessName
          time_created = [string]$trace.TIME_CREATED
        }
      } else {
        Write-ErrorRecord "unexpected_subscription_event"
        exit 2
      }
    } finally {
      Remove-Event -EventIdentifier $queued.EventIdentifier -ErrorAction SilentlyContinue
    }
  }
  Write-Record @{ type = "stopped"; event_count = $eventCount }
} catch {
  $kind = $_.Exception.GetType().Name -replace "[^A-Za-z0-9_]", "_"
  $hresult = "unknown"
  try { $hresult = ("{0:X8}" -f ([uint32]([int64]$_.Exception.HResult -band 0xffffffff))).ToLowerInvariant() } catch {}
  $fqid = [string]$_.FullyQualifiedErrorId -replace "[^A-Za-z0-9_]", "_"
  if ($fqid.Length -gt 120) { $fqid = $fqid.Substring(0, 120) }
  Write-ErrorRecord "watcher_failure_${stage}_${kind}_0x$hresult" $fqid
  exit 2
} finally {
  if ($null -ne $startSubscription) { Unregister-Event -SubscriptionId $startSubscription.Id -ErrorAction SilentlyContinue }
  if ($null -ne $stopSubscription) { Unregister-Event -SubscriptionId $stopSubscription.Id -ErrorAction SilentlyContinue }
}
