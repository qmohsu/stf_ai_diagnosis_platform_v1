[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$frontendRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $frontendRoot
$v3Root = Join-Path $repoRoot "stf_v3"
$v3Python = Join-Path $v3Root ".venv\Scripts\python.exe"
$v3EnvironmentRoot = Join-Path `
  ([Environment]::GetFolderPath("LocalApplicationData")) `
  "OBD Expert"
$v3Environment = Join-Path $v3EnvironmentRoot "stf_v3.env"
$portalUrl = "http://127.0.0.1:3001/login"
$portalPort = 3001
$apiHealthUrl = "http://127.0.0.1:8002/health"
$apiOpenApiUrl = "http://127.0.0.1:8002/v3/openapi.json"
$apiPort = 8002
$databasePort = 5432
$startupTimeoutSeconds = 120

function Write-LauncherError {
  param([Parameter(Mandatory = $true)][string]$Message)

  Write-Host "[OBD Expert] ERROR: $Message" -ForegroundColor Red
}

function Read-V3Environment {
  $variables = @{}
  foreach ($line in [System.IO.File]::ReadAllLines($v3Environment)) {
    $trimmed = $line.Trim()
    if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
      continue
    }

    $separator = $line.IndexOf("=")
    if ($separator -lt 1) {
      throw "The V3 environment file contains an invalid line."
    }

    $name = $line.Substring(0, $separator).Trim()
    $value = $line.Substring($separator + 1)
    if ($name -notmatch "^STF_V3_[A-Z0-9_]+$") {
      throw "The V3 environment file contains an invalid variable name."
    }
    $variables[$name] = $value
  }

  foreach ($required in @(
    "STF_V3_DATABASE_URL",
    "STF_V3_OWNER_DATABASE_URL",
    "STF_V3_JWT_SECRET"
  )) {
    if (-not $variables.ContainsKey($required) -or [string]::IsNullOrEmpty($variables[$required])) {
      throw "The V3 environment file is missing a required variable."
    }
  }

  return $variables
}

function Get-PortalProbe {
  param([int]$TimeoutSeconds = 3)

  try {
    $response = Invoke-WebRequest `
      -Uri $portalUrl `
      -UseBasicParsing `
      -TimeoutSec $TimeoutSeconds `
      -Headers @{ "Cache-Control" = "no-cache" }

    $hasBrand = $response.Content -match "Towngas"
    $hasLogin = $response.Content -match "(?:login-card|login-form)"

    if ($response.StatusCode -eq 200 -and $hasBrand -and $hasLogin) {
      return "Portal"
    }

    return "Other"
  }
  catch {
    return "Unavailable"
  }
}

function Test-TcpPort {
  param(
    [Parameter(Mandatory = $true)][string]$HostName,
    [Parameter(Mandatory = $true)][int]$Port,
    [int]$TimeoutMilliseconds = 1000
  )

  $client = New-Object System.Net.Sockets.TcpClient

  try {
    $connectTask = $client.ConnectAsync($HostName, $Port)
    if (-not $connectTask.Wait($TimeoutMilliseconds)) {
      return $false
    }

    return $client.Connected
  }
  catch {
    return $false
  }
  finally {
    $client.Dispose()
  }
}

function Get-PostgresReadyCommand {
  $command = Get-Command -Name "pg_isready.exe" -CommandType Application -ErrorAction SilentlyContinue
  if ($null -ne $command) {
    return $command.Source
  }

  $programFiles = [Environment]::GetFolderPath("ProgramFiles")
  $candidates = @(
    Get-ChildItem `
      -Path (Join-Path $programFiles "PostgreSQL\*\bin\pg_isready.exe") `
      -File `
      -ErrorAction SilentlyContinue |
      Sort-Object -Property FullName -Descending
  )

  if ($candidates.Count -gt 0) {
    return $candidates[0].FullName
  }

  $service = Get-PostgresService
  if ($null -ne $service) {
    $serviceDetails = Get-CimInstance `
      -ClassName Win32_Service `
      -Filter "Name='$($service.Name)'" `
      -ErrorAction SilentlyContinue
    if ($null -ne $serviceDetails -and $serviceDetails.PathName -match '^"?([^\"]*\\pg_ctl\.exe)"?') {
      $serviceBin = Split-Path -Parent $Matches[1]
      $servicePgIsReady = Join-Path $serviceBin "pg_isready.exe"
      if (Test-Path -LiteralPath $servicePgIsReady -PathType Leaf) {
        return $servicePgIsReady
      }
    }
  }

  return $null
}

function Test-PostgresReady {
  param([Parameter(Mandatory = $true)][string]$PgIsReady)

  & $PgIsReady -h "127.0.0.1" -p $databasePort -t 2 2>$null | Out-Null
  return $LASTEXITCODE -eq 0
}

function Get-PostgresService {
  $preferred = Get-Service -Name "postgresql-x64-15" -ErrorAction SilentlyContinue
  if ($null -ne $preferred) {
    return $preferred
  }

  $matches = @(
    Get-Service -ErrorAction SilentlyContinue |
      Where-Object {
        $_.Name -match "^postgresql" -or $_.DisplayName -match "PostgreSQL"
      }
  )

  if ($matches.Count -eq 1) {
    return $matches[0]
  }

  return $null
}

function Ensure-PostgresReady {
  Write-Host "[OBD Expert] Checking PostgreSQL..." -ForegroundColor Cyan

  $pgIsReady = Get-PostgresReadyCommand
  if ($null -eq $pgIsReady) {
    Write-LauncherError "PostgreSQL 15 tools were not found. Complete the one-time setup first."
    return $false
  }

  if (Test-PostgresReady -PgIsReady $pgIsReady) {
    Write-Host "[OBD Expert] PostgreSQL is ready." -ForegroundColor Green
    return $true
  }

  if (Test-TcpPort -HostName "127.0.0.1" -Port $databasePort) {
    Write-LauncherError "Port $databasePort is in use, but PostgreSQL is not accepting connections."
    return $false
  }

  $service = Get-PostgresService
  if ($null -eq $service) {
    Write-LauncherError "A unique PostgreSQL Windows service was not found. Complete the one-time setup first."
    return $false
  }

  Write-Host "[OBD Expert] Starting PostgreSQL service $($service.Name)..." -ForegroundColor Cyan
  try {
    Start-Service -InputObject $service
  }
  catch {
    Write-LauncherError "PostgreSQL could not be started. Start it as an administrator, then try again."
    return $false
  }

  $deadline = [DateTime]::UtcNow.AddSeconds(30)
  while ([DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 750
    if (Test-PostgresReady -PgIsReady $pgIsReady) {
      Write-Host "[OBD Expert] PostgreSQL is ready." -ForegroundColor Green
      return $true
    }
  }

  Write-LauncherError "PostgreSQL did not become ready within 30 seconds."
  return $false
}

function Get-V3Probe {
  param([int]$TimeoutSeconds = 3)

  try {
    $openApi = Invoke-WebRequest `
      -Uri $apiOpenApiUrl `
      -UseBasicParsing `
      -TimeoutSec $TimeoutSeconds `
      -Headers @{ "Cache-Control" = "no-cache" }

    $isV3 = $openApi.StatusCode -eq 200 `
      -and $openApi.Content -match "STF V3 API" `
      -and $openApi.Content -match '"/v3/auth/login"'

    if (-not $isV3) {
      return "Other"
    }
  }
  catch {
    return "Unavailable"
  }

  try {
    $health = Invoke-WebRequest `
      -Uri $apiHealthUrl `
      -UseBasicParsing `
      -TimeoutSec $TimeoutSeconds `
      -Headers @{ "Cache-Control" = "no-cache" }
    $payload = $health.Content | ConvertFrom-Json

    if ($health.StatusCode -eq 200 -and $payload.status -eq "ok" -and $payload.db -eq "ok") {
      return "Healthy"
    }
  }
  catch {
    return "UnhealthyV3"
  }

  return "UnhealthyV3"
}

function Ensure-V3Ready {
  Write-Host "[OBD Expert] Checking the V3 login service..." -ForegroundColor Cyan

  $initialProbe = Get-V3Probe
  if ($initialProbe -eq "Healthy") {
    Write-Host "[OBD Expert] The V3 login service is already running." -ForegroundColor Green
    return $true
  }

  if (Test-TcpPort -HostName "127.0.0.1" -Port $apiPort) {
    if ($initialProbe -eq "UnhealthyV3") {
      Write-LauncherError "The V3 API is running on port $apiPort, but its health check failed."
    }
    else {
      Write-LauncherError "Port $apiPort is in use by another service. The launcher did not stop it."
    }
    return $false
  }

  if (-not (Test-Path -LiteralPath $v3Environment -PathType Leaf)) {
    Write-LauncherError "The V3 environment file is missing. Complete the one-time setup first."
    return $false
  }

  if (-not (Test-Path -LiteralPath $v3Python -PathType Leaf)) {
    Write-LauncherError "The V3 Python environment is missing. Complete the one-time setup first: stf_v3\.venv"
    return $false
  }

  & $v3Python -c "import stf_v3, uvicorn" 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-LauncherError "The V3 Python dependencies are incomplete. Reinstall the stf_v3 package."
    return $false
  }

  Write-Host "[OBD Expert] Starting the V3 login service in a separate window..." -ForegroundColor Cyan
  $apiCommand = '"' + $v3Python + '" -m stf_v3.windows_server'
  try {
    $v3Variables = Read-V3Environment
  }
  catch {
    Write-LauncherError $_.Exception.Message
    return $false
  }

  $savedVariables = @{}
  try {
    foreach ($name in $v3Variables.Keys) {
      $savedVariables[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
      [Environment]::SetEnvironmentVariable($name, $v3Variables[$name], "Process")
    }

    Start-Process `
      -FilePath $env:ComSpec `
      -ArgumentList @("/k", $apiCommand) `
      -WorkingDirectory $v3Root `
      -WindowStyle Normal |
      Out-Null
  }
  finally {
    foreach ($name in $savedVariables.Keys) {
      [Environment]::SetEnvironmentVariable($name, $savedVariables[$name], "Process")
    }
  }

  Write-Host "[OBD Expert] Waiting up to $startupTimeoutSeconds seconds for the V3 login service..."
  $deadline = [DateTime]::UtcNow.AddSeconds($startupTimeoutSeconds)
  while ([DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 750
    if ((Get-V3Probe -TimeoutSeconds 3) -eq "Healthy") {
      Write-Host "[OBD Expert] The V3 login service is ready." -ForegroundColor Green
      return $true
    }
  }

  Write-LauncherError "The V3 login service did not become healthy within $startupTimeoutSeconds seconds."
  Write-Host "Review the V3 API window for details. The launcher has left it open."
  return $false
}

function Ensure-PortalReady {
  Write-Host "[OBD Expert] Checking the frontend..." -ForegroundColor Cyan

  $initialProbe = Get-PortalProbe
  if ($initialProbe -eq "Portal") {
    Write-Host "[OBD Expert] The frontend is already running." -ForegroundColor Green
    return $true
  }

  if (Test-TcpPort -HostName "127.0.0.1" -Port $portalPort) {
    Write-LauncherError "Port $portalPort is in use by another service. The launcher did not stop it."
    return $false
  }

  $nodeCommand = Get-Command -Name "node.exe" -CommandType Application -ErrorAction SilentlyContinue
  if ($null -eq $nodeCommand) {
    Write-LauncherError "Node.js was not found. Install Node.js 24 or later and reopen this launcher."
    return $false
  }

  $npmCommand = Get-Command -Name "npm.cmd" -CommandType Application -ErrorAction SilentlyContinue
  if ($null -eq $npmCommand) {
    Write-LauncherError "npm.cmd was not found. Install npm 11 or later and reopen this launcher."
    return $false
  }

  $nextCommand = Join-Path $frontendRoot "node_modules\.bin\next.cmd"
  if (-not (Test-Path -LiteralPath $nextCommand -PathType Leaf)) {
    Write-LauncherError "Frontend dependencies are missing."
    Write-Host "Open a terminal in this folder and run: npm ci"
    Write-Host "Then double-click OBD Expert.cmd again."
    return $false
  }

  Write-Host "[OBD Expert] Starting the development server in a separate window..." -ForegroundColor Cyan
  Start-Process `
    -FilePath $env:ComSpec `
    -ArgumentList @("/k", "npm.cmd run dev") `
    -WorkingDirectory $frontendRoot `
    -WindowStyle Normal |
    Out-Null

  Write-Host "[OBD Expert] Waiting up to $startupTimeoutSeconds seconds for the login page..."
  $deadline = [DateTime]::UtcNow.AddSeconds($startupTimeoutSeconds)
  while ([DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 750
    if ((Get-PortalProbe -TimeoutSeconds 3) -eq "Portal") {
      Write-Host "[OBD Expert] The frontend is ready." -ForegroundColor Green
      return $true
    }
  }

  Write-LauncherError "The frontend did not become ready within $startupTimeoutSeconds seconds."
  Write-Host "Review the frontend window for details. The launcher has left it open."
  return $false
}

function Open-Portal {
  Write-Host "[OBD Expert] Opening $portalUrl in the default browser..." -ForegroundColor Cyan
  Start-Process -FilePath $portalUrl
}

if (-not (Ensure-PostgresReady)) {
  exit 1
}

if (-not (Ensure-V3Ready)) {
  exit 1
}

if (-not (Ensure-PortalReady)) {
  exit 1
}

Open-Portal
exit 0
