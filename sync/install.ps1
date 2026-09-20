<#
  One-time setup for the telemetry sync (Windows).

    powershell -ExecutionPolicy Bypass -File install.ps1
    powershell -ExecutionPolicy Bypass -File install.ps1 -Bucket blueice-telemetry-raw -Prefix logs/
    powershell -ExecutionPolicy Bypass -File install.ps1 -Local     # no AWS: read a folder instead

  Installs the two Python dependencies, writes config.json if it is missing,
  and finishes by running `blueice_sync.py check` so you find out immediately
  whether the bucket is actually reachable.

  This script never asks for an access key and never stores one. boto3 reads
  credentials from the standard chain -- an IAM role, `aws configure`, or the
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY environment variables. Set those up
  yourself; nothing here should ever hold a secret.
#>
param(
  [string]$Bucket = "",
  [string]$Prefix = "logs/",
  [string]$Region = "me-central-1",
  [string]$Profile = "",
  [switch]$Local
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

function Step($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }

Step "Python"
$py = (Get-Command python -ErrorAction SilentlyContinue)
if ($null -eq $py) { $py = (Get-Command py -ErrorAction SilentlyContinue) }
if ($null -eq $py) {
  Write-Host "  Python was not found on PATH. Install Python 3.8+ and re-run." -ForegroundColor Red
  exit 1
}
$ver = & $py.Source -c "import sys;print('.'.join(map(str,sys.version_info[:3])))"
Write-Host "  $($py.Source)  ($ver)"

Step "Dependencies"
# pycryptodome decodes the logs; boto3 talks to S3. Nothing else is needed.
& $py.Source -m pip install --quiet --disable-pip-version-check pycryptodome boto3
if ($LASTEXITCODE -ne 0) {
  Write-Host "  pip install failed. If this machine is behind a proxy, set HTTPS_PROXY and re-run." -ForegroundColor Red
  exit 1
}
& $py.Source -c "import Crypto, boto3; print('  pycryptodome', Crypto.__version__, '| boto3', boto3.__version__)"

Step "Configuration"
if (Test-Path "config.json") {
  Write-Host "  config.json already exists -- left alone."
  Write-Host "  Delete it and re-run if you want it rewritten."
} elseif ($Local) {
  Copy-Item "config.local.example.json" "config.json"
  Write-Host "  Wrote config.json in local mode (reads ..\..\Not mine, no AWS)."
} else {
  $cfg = Get-Content "config.example.json" -Raw | ConvertFrom-Json
  $cfg.PSObject.Properties.Remove("_readme")
  $cfg.source.PSObject.Properties.Remove("_type")
  $cfg.source.bucket = $Bucket
  $cfg.source.prefix = $Prefix
  $cfg.source.region = $Region
  if ($Profile -ne "") { $cfg.source.profile = $Profile }
  # Out-File -Encoding utf8 writes a BOM on PowerShell 5.1. json.load copes
  # (it reads utf-8-sig) but a BOM in a config file is a trap for the next
  # tool that opens it, so write it clean.
  [System.IO.File]::WriteAllText(
    (Join-Path $here "config.json"),
    ($cfg | ConvertTo-Json -Depth 6),
    (New-Object System.Text.UTF8Encoding($false)))
  if ($Bucket -eq "") {
    Write-Host "  Wrote config.json with an EMPTY bucket." -ForegroundColor Yellow
    Write-Host "  Edit source.bucket, or re-run with -Bucket <name>."
  } else {
    Write-Host "  Wrote config.json for s3://$Bucket/$Prefix in $Region"
  }
}

Step "Credentials"
$hasEnv = ($env:AWS_ACCESS_KEY_ID -ne $null) -and ($env:AWS_ACCESS_KEY_ID -ne "")
$hasFile = Test-Path (Join-Path $env:USERPROFILE ".aws\credentials")
if ($hasEnv) { Write-Host "  AWS_ACCESS_KEY_ID is set in the environment." }
elseif ($hasFile) { Write-Host "  Found $env:USERPROFILE\.aws\credentials" }
else {
  Write-Host "  No AWS credentials found." -ForegroundColor Yellow
  Write-Host "  On an EC2 instance an attached IAM role is the right answer and needs nothing here."
  Write-Host "  Otherwise run 'aws configure' yourself, or set AWS_ACCESS_KEY_ID and"
  Write-Host "  AWS_SECRET_ACCESS_KEY. Do not paste a key into this script or config.json."
}

Step "Check"
& $py.Source "blueice_sync.py" "check"
$code = $LASTEXITCODE

Write-Host ""
if ($code -eq 0) {
  Write-Host "Ready. Next:" -ForegroundColor Green
  Write-Host "  python blueice_sync.py update            pull new logs and rebuild the dashboard"
  Write-Host "  .\schedule.ps1 -Minutes 15              run that automatically every 15 minutes"
} else {
  Write-Host "Not ready -- see the FAIL lines above." -ForegroundColor Yellow
}
exit $code
