# Copy the user's already-tested, patched WA-AKG source into this unified repo.
# Never copy secrets, local database, uploads, node_modules, or Git metadata.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$src = 'C:\Users\ASUS\Desktop\Projects\WHATSAPP_GATEWAY'
$dst = Join-Path $root 'gateway'
if (!(Test-Path (Join-Path $src 'package.json'))) { throw "Gateway folder not found: $src" }
if (Test-Path (Join-Path $dst 'package.json')) { throw "Gateway already assembled at $dst; back up and remove it manually if replacing." }
New-Item -ItemType Directory -Path $dst -Force | Out-Null
robocopy $src $dst /E /R:1 /W:1 /XD .git node_modules .next .turbo .vercel .backup backups /XF .env .env.local *.sqlite3 *.log *.pem *.key *.dump *.sql | Out-Null
if ($LASTEXITCODE -gt 7) { throw 'robocopy failed. Check disk space and source permissions.' }
if (!(Test-Path (Join-Path $dst 'src\server\index.ts'))) { throw 'Missing Gateway custom server. The source copy is incomplete.' }
if (!(Test-Path (Join-Path $dst 'prisma\schema.prisma'))) { throw 'Missing Gateway Prisma schema.' }
$sessionRoute = Get-Content (Join-Path $dst 'src\app\api\sessions\route.ts') -Raw
if ($sessionRoute -notmatch 'runtimeStatus') { throw 'Gateway runtimeStatus patch is missing. Apply the verified V3.1 repair to WHATSAPP_GATEWAY, then assemble into a fresh folder. Do not deploy an unpatched Gateway.' }
Write-Host 'ASSEMBLED: Gateway source copied into unified repo. No credential file or database was copied.' -ForegroundColor Green
Write-Host 'IMPORTANT: Review git status and do NOT add .env or private export data to GitHub.'
