param(
  [switch]$UpdateSnapshots
)

$ErrorActionPreference = "Stop"
Remove-Item Env:OPENSSL_CONF -ErrorAction SilentlyContinue
Remove-Item Env:OPENSSL_MODULES -ErrorAction SilentlyContinue

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$arguments = @("playwright", "test")
if ($UpdateSnapshots) {
  $arguments += "--update-snapshots"
}

& npm.cmd exec -- @arguments
exit $LASTEXITCODE
