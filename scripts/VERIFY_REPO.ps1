$root=Split-Path -Parent $PSScriptRoot
$required=@('apps\web\package.json','services\studio\api.py','services\studio\worker.py','gateway\package.json','gateway\src\server\index.ts','docker-compose.yml','infra\Dockerfile','infra\Caddyfile')
foreach($relative in $required){$p=Join-Path $root $relative;if(!(Test-Path $p)){Write-Host "MISSING $relative" -ForegroundColor Red;exit 1}else{Write-Host "OK $relative"}}
Write-Host 'Source files present. Does NOT verify cloud credentials, live WhatsApp sending or platform security.' -ForegroundColor Yellow
