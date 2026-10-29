[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$v3Root = Split-Path -Parent $PSScriptRoot
$dataRoot = Join-Path $v3Root "data"
$statusPath = Join-Path $dataRoot "setup-local-status.json"
$environmentRoot = Join-Path `
  ([Environment]::GetFolderPath("LocalApplicationData")) `
  "OBD Expert"
$environmentPath = Join-Path $environmentRoot "stf_v3.env"
$venvRoot = Join-Path $v3Root ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$alembic = Join-Path $venvRoot "Scripts\alembic.exe"

function Write-SetupStatus {
  param(
    [Parameter(Mandatory = $true)][string]$State,
    [Parameter(Mandatory = $true)][string]$Message
  )

  New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
  $payload = [ordered]@{
    state = $State
    message = $Message
    updated_at = [DateTime]::UtcNow.ToString("o")
  } | ConvertTo-Json
  [System.IO.File]::WriteAllText(
    $statusPath,
    $payload,
    (New-Object System.Text.UTF8Encoding($false))
  )
}

function New-HexSecret {
  param([Parameter(Mandatory = $true)][int]$ByteCount)

  $bytes = New-Object byte[] $ByteCount
  $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $generator.GetBytes($bytes)
  }
  finally {
    $generator.Dispose()
  }
  return ([BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
}

function Get-PostgresBin {
  $service = Get-CimInstance `
    -ClassName Win32_Service `
    -Filter "Name='postgresql-x64-15'" `
    -ErrorAction SilentlyContinue
  if ($null -eq $service -or $service.PathName -notmatch '^"?([^\"]*\\pg_ctl\.exe)"?') {
    throw "PostgreSQL 15 Windows service was not found."
  }

  $bin = Split-Path -Parent $Matches[1]
  if (-not (Test-Path -LiteralPath (Join-Path $bin "psql.exe") -PathType Leaf)) {
    throw "psql.exe was not found beside the PostgreSQL service executable."
  }
  return $bin
}

function Protect-EnvironmentFile {
  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $identity = New-Object System.Security.Principal.SecurityIdentifier($currentSid)
  $acl = New-Object System.Security.AccessControl.FileSecurity
  $acl.SetAccessRuleProtection($true, $false)
  $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    $identity,
    "FullControl",
    "Allow"
  )
  $acl.SetAccessRule($rule)
  [System.IO.File]::SetAccessControl($environmentPath, $acl)

  $verifiedAcl = [System.IO.File]::GetAccessControl($environmentPath)
  $verifiedRules = @(
    $verifiedAcl.GetAccessRules(
      $true,
      $true,
      [System.Security.Principal.SecurityIdentifier]
    )
  )
  $currentUserRules = @(
    $verifiedRules | Where-Object {
      $_.IdentityReference.Value -eq $currentSid -and
      $_.AccessControlType -eq "Allow"
    }
  )
  if (
    -not $verifiedAcl.AreAccessRulesProtected -or
    $verifiedRules.Count -ne 1 -or
    $currentUserRules.Count -ne 1
  ) {
    throw "Could not verify access restrictions on the local V3 environment file."
  }
}

Write-SetupStatus -State "running" -Message "Waiting for local database setup"
Write-Host "OBD Expert V3 local setup" -ForegroundColor Cyan
Write-Host "The PostgreSQL administrator password stays in this process only."

try {
  $py = Get-Command -Name "py.exe" -CommandType Application -ErrorAction SilentlyContinue
  if ($null -eq $py) {
    throw "Python launcher py.exe was not found. Install Python 3.12 first."
  }

  & $py.Source -3.12 -c "import sys; assert sys.version_info >= (3, 12)"
  if ($LASTEXITCODE -ne 0) {
    throw "Python 3.12 is not available through py.exe."
  }

  $postgresBin = Get-PostgresBin
  $psql = Join-Path $postgresBin "psql.exe"
  $pgIsReady = Join-Path $postgresBin "pg_isready.exe"
  & $pgIsReady -h "127.0.0.1" -p 5432 -t 3 | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL is not accepting connections on 127.0.0.1:5432."
  }

  $secureAdminPassword = Read-Host `
    "Enter the password for the local PostgreSQL superuser postgres" `
    -AsSecureString
  $credential = New-Object System.Management.Automation.PSCredential(
    "postgres",
    $secureAdminPassword
  )
  $adminPassword = $credential.GetNetworkCredential().Password
  if ([string]::IsNullOrEmpty($adminPassword)) {
    throw "The PostgreSQL administrator password cannot be empty."
  }

  $ownerPassword = New-HexSecret -ByteCount 24
  $appPassword = New-HexSecret -ByteCount 24
  $jwtSecret = New-HexSecret -ByteCount 32
  $ownerUrl = "postgresql+psycopg://stf_v3:${ownerPassword}@127.0.0.1:5432/stf_v3"
  $appUrl = "postgresql+psycopg://stf_v3_app:${appPassword}@127.0.0.1:5432/stf_v3"

  $roleSql = @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stf_v3') THEN
    CREATE ROLE stf_v3 LOGIN PASSWORD '${ownerPassword}';
  ELSE
    ALTER ROLE stf_v3 WITH LOGIN PASSWORD '${ownerPassword}';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stf_v3_app') THEN
    CREATE ROLE stf_v3_app LOGIN PASSWORD '${appPassword}';
  ELSE
    ALTER ROLE stf_v3_app WITH LOGIN PASSWORD '${appPassword}';
  END IF;
END
`$`$;
SELECT 'CREATE DATABASE stf_v3 OWNER stf_v3'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'stf_v3')
\gexec
ALTER DATABASE stf_v3 OWNER TO stf_v3;
"@

  $savedPgPassword = $env:PGPASSWORD
  try {
    $env:PGPASSWORD = $adminPassword
    $roleSql | & $psql `
      -h "127.0.0.1" `
      -p 5432 `
      -U "postgres" `
      -d "postgres" `
      -v "ON_ERROR_STOP=1"
    if ($LASTEXITCODE -ne 0) {
      throw "PostgreSQL role or database creation failed."
    }

    "ALTER SCHEMA public OWNER TO stf_v3;" | & $psql `
      -h "127.0.0.1" `
      -p 5432 `
      -U "postgres" `
      -d "stf_v3" `
      -v "ON_ERROR_STOP=1"
    if ($LASTEXITCODE -ne 0) {
      throw "PostgreSQL schema ownership setup failed."
    }
  }
  finally {
    $env:PGPASSWORD = $savedPgPassword
    $adminPassword = $null
    $credential = $null
    $secureAdminPassword = $null
  }

  $environmentLines = @(
    "STF_V3_DATABASE_URL=${appUrl}",
    "STF_V3_OWNER_DATABASE_URL=${ownerUrl}",
    "STF_V3_JWT_SECRET=${jwtSecret}",
    "STF_V3_ENVIRONMENT=dev",
    "STF_V3_OBD_LOG_STORAGE_PATH=./data/obd_logs",
    "STF_V3_MANUAL_STORAGE_PATH=./data/manuals",
    "STF_V3_LLM_AUTOSTART=false"
  )
  New-Item -ItemType Directory -Path $environmentRoot -Force | Out-Null
  [System.IO.File]::WriteAllLines(
    $environmentPath,
    $environmentLines,
    (New-Object System.Text.UTF8Encoding($false))
  )
  Protect-EnvironmentFile

  Set-Location -LiteralPath $v3Root
  if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Host "Creating Python 3.12 virtual environment..." -ForegroundColor Cyan
    & $py.Source -3.12 -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) {
      throw "Python virtual environment creation failed."
    }
  }

  Write-Host "Installing STF V3 dependencies..." -ForegroundColor Cyan
  $savedPythonUtf8 = $env:PYTHONUTF8
  try {
    $env:PYTHONUTF8 = "1"
    & $venvPython -m pip install --disable-pip-version-check -e $v3Root
    if ($LASTEXITCODE -ne 0) {
      throw "STF V3 dependency installation failed."
    }
  }
  finally {
    $env:PYTHONUTF8 = $savedPythonUtf8
  }

  Write-Host "Applying STF V3 database migrations..." -ForegroundColor Cyan
  $savedDatabaseUrl = $env:STF_V3_DATABASE_URL
  try {
    $env:STF_V3_DATABASE_URL = $ownerUrl
    & $alembic -c (Join-Path $v3Root "alembic.ini") upgrade head
    if ($LASTEXITCODE -ne 0) {
      throw "Alembic migration failed."
    }
  }
  finally {
    $env:STF_V3_DATABASE_URL = $savedDatabaseUrl
  }

  Write-SetupStatus -State "success" -Message "Local V3 database setup completed"
  Write-Host "Local V3 database setup completed successfully." -ForegroundColor Green
}
catch {
  Write-SetupStatus -State "failed" -Message $_.Exception.Message
  Write-Host "SETUP FAILED: $($_.Exception.Message)" -ForegroundColor Red
  exit 1
}
