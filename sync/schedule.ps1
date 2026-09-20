<#
  Register (or remove) the Windows scheduled task that keeps the dashboard data
  current.

    powershell -ExecutionPolicy Bypass -File schedule.ps1 -Minutes 15
    powershell -ExecutionPolicy Bypass -File schedule.ps1 -Status
    powershell -ExecutionPolicy Bypass -File schedule.ps1 -Unregister

  The task runs run_update.cmd, which appends everything to sync.log. It runs as
  the current user and only while that user is logged on -- deliberate for a
  prototype on a workstation. If this ever moves to a server, run it under a
  service account, or better, do the ingest in Lambda on the S3 put event and
  delete this file (HANDOVER.md step 4).

  The device uploads roughly every 5 minutes, so 15 minutes is a reasonable
  default: often enough that the dashboard is never far behind, rare enough that
  a listing of a large bucket is not constant.
#>
param(
  [int]$Minutes = 15,
  [string]$Name = "BlueICETelemetrySync",
  [switch]$Unregister,
  [switch]$Status
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$existing = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue

if ($Status) {
  if ($null -eq $existing) { Write-Host "Not registered: $Name"; exit 1 }
  $info = Get-ScheduledTaskInfo -TaskName $Name
  Write-Host "Task        $Name"
  Write-Host "State       $($existing.State)"
  Write-Host "Last run    $($info.LastRunTime)  (result $($info.LastTaskResult))"
  Write-Host "Next run    $($info.NextRunTime)"
  $log = Join-Path $here "sync.log"
  if (Test-Path $log) {
    Write-Host ""
    Write-Host "Tail of sync.log:"
    Get-Content $log -Tail 15 | ForEach-Object { Write-Host "  $_" }
  }
  exit 0
}

if ($Unregister) {
  if ($null -eq $existing) { Write-Host "Not registered: $Name"; exit 0 }
  Unregister-ScheduledTask -TaskName $Name -Confirm:$false
  Write-Host "Removed $Name"
  exit 0
}

if (-not (Test-Path (Join-Path $here "config.json"))) {
  Write-Host "No config.json -- run install.ps1 first." -ForegroundColor Red
  exit 1
}

$action  = New-ScheduledTaskAction -Execute (Join-Path $here "run_update.cmd") -WorkingDirectory $here
# 10 years, not [TimeSpan]::MaxValue: MaxValue serialises to
# P99999999DT23H59M59S, which Task Scheduler rejects as out of range
# (HRESULT 0x80041318). Ten years is indefinite for every practical purpose --
# and this tool is meant to be deleted long before then, once the Lambda exists.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
             -RepetitionInterval (New-TimeSpan -Minutes $Minutes) `
             -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
              -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
              -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
              -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

if ($null -ne $existing) { Unregister-ScheduledTask -TaskName $Name -Confirm:$false }
Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger `
  -Settings $settings -Principal $principal `
  -Description "Pulls BlueICE telemetry logs from the ingest bucket and rebuilds the dashboard datasets." | Out-Null

Write-Host "Registered $Name -- every $Minutes minute(s), starting in a minute." -ForegroundColor Green
Write-Host "  output    $(Join-Path $here 'sync.log')"
Write-Host "  status    .\schedule.ps1 -Status"
Write-Host "  remove    .\schedule.ps1 -Unregister"
Write-Host ""
Write-Host "MultipleInstances is IgnoreNew: if a run is still going when the next"
Write-Host "one is due, the new one is skipped rather than racing it into state.json."
