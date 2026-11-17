# TAZORIXX MVP27.1 + WA-AKG integration setup.
# Does not edit .env, reset MySQL, send WhatsApp messages, or push to GitHub.
$ErrorActionPreference = 'Stop'
$PackageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Projects = Join-Path $env:USERPROFILE 'Desktop\Projects'
$BotSource = Join-Path $PackageRoot 'TAZORIXX_MVP27_REPAIRED'
$BotTarget = Join-Path $Projects 'TAZORIXX_MVP27_REPAIRED'
$Gateway = Join-Path $Projects 'WHATSAPP_GATEWAY'
$Patch = Join-Path $PackageRoot 'WA_GATEWAY_INTEGRATION_PATCH\apply-gateway-integration.mjs'
if (!(Test-Path -LiteralPath $Gateway)) { throw "WhatsApp Gateway repository not found: $Gateway" }
if (!(Test-Path -LiteralPath $BotSource)) { throw "Repair source not found: $BotSource" }
if (Test-Path -LiteralPath $BotTarget) {
    throw "Destination already exists: $BotTarget. Do not overwrite an existing bot without a separate backup."
}
Write-Host 'Close the original TAZORIXX app, disable its previous Windows background task, and stop WA-AKG (Ctrl+C) BEFORE applying this repair.' -ForegroundColor Yellow
Write-Host 'The old background sender and the new bot must NOT process the same SQLite queue at once.' -ForegroundColor Yellow
# Backup persistent TAZORIXX SQLite state. Only safe after the former sender has stopped.
$DataDir = Join-Path $env:LOCALAPPDATA 'TAZORIXX'
$Database = Join-Path $DataDir 'tazorixx.sqlite3'
if (Test-Path -LiteralPath $Database) {
    $Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $Backup = Join-Path $Projects "TAZORIXX_SQLITE_BACKUP_$Stamp"
    New-Item -ItemType Directory -Path $Backup -Force | Out-Null
    foreach ($Suffix in @('', '-wal', '-shm')) {
        $Source = "$Database$Suffix"
        if (Test-Path -LiteralPath $Source) {
            Copy-Item -LiteralPath $Source -Destination $Backup -Force
        }
    }
    Write-Host "SQLite files backed up to: $Backup" -ForegroundColor Green
}
Copy-Item -LiteralPath $BotSource -Destination $BotTarget -Recurse
Write-Host "New standalone bot installed: $BotTarget" -ForegroundColor Green
Push-Location -LiteralPath $Gateway
try {
    & node $Patch
    if ($LASTEXITCODE -ne 0) { throw "Gateway integration patch failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
Write-Host ''
Write-Host 'Setup files complete. Next: run TypeScript/build checks and test an isolated WhatsApp account.' -ForegroundColor Green
Write-Host "Gateway: $Gateway"
Write-Host "Bot: $BotTarget"
