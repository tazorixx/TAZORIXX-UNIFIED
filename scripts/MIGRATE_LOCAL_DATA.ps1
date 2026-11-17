# Run on Windows with TAZORIXX desktop closed. Creates a private ZIP for SECURE copy to the VM.
$ErrorActionPreference='Stop'
$src = Join-Path $env:LOCALAPPDATA 'TAZORIXX'
$db = Join-Path $src 'tazorixx.sqlite3'
if (!(Test-Path $db)) { throw "Desktop database missing: $db" }
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$temp=Join-Path $env:TEMP "TAZORIXX_MIGRATE_$stamp"
New-Item -ItemType Directory -Path $temp -Force | Out-Null
try {
    $env:TAZORIXX_MIGRATION_SOURCE=$db
    $env:TAZORIXX_MIGRATION_DEST=(Join-Path $temp 'tazorixx.sqlite3')
    py -3 -c 'import sqlite3,os; a=sqlite3.connect(os.environ["TAZORIXX_MIGRATION_SOURCE"]); b=sqlite3.connect(os.environ["TAZORIXX_MIGRATION_DEST"]); a.backup(b); b.close(); a.close()'
    if ($LASTEXITCODE -ne 0) { throw 'SQLite snapshot failed. No ZIP created.' }
    $exports=Join-Path $src 'Website_Exports'
    if (Test-Path $exports) { Copy-Item $exports -Destination $temp -Recurse -Force }
    $out=Join-Path $env:USERPROFILE "Desktop\TAZORIXX_PRIVATE_DATA_$stamp.zip"
    Compress-Archive -Path "$temp\*" -DestinationPath $out -CompressionLevel Optimal
    Write-Host "PRIVATE DATA ZIP: $out" -ForegroundColor Green
    Write-Warning 'Never commit this ZIP to GitHub. Use SCP/SFTP directly to the server.'
} finally { Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue }
