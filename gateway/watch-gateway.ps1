# Read-only WA-AKG runtime status watcher for Windows PowerShell 5.1.
# Does not send WhatsApp messages or change any database, key, or source file.
# API key is prompted securely, kept only in process memory and NEVER saved.
param(
    [string]$GatewayUrl = 'http://localhost:3030',
    [string]$SessionId = '',
    [int]$Seconds = 180,
    [int]$IntervalSeconds = 5
)
$ErrorActionPreference = 'Stop'
if ($GatewayUrl -notmatch '^http://(localhost|127\.0\.0\.1):\d+$') {
    throw 'This local diagnostic only accepts http://localhost:PORT or http://127.0.0.1:PORT'
}
if ($Seconds -lt 5 -or $Seconds -gt 3600 -or $IntervalSeconds -lt 2) { throw 'Invalid polling interval/duration' }
$secret = Read-Host 'Paste the Gateway API key (input hidden)' -AsSecureString
$pointer = [IntPtr]::Zero
$key = $null
try {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ([string]::IsNullOrWhiteSpace($key)) { throw 'API key cannot be blank' }
} finally {
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
$headers = @{ 'X-API-Key' = $key }
$folder = Join-Path ([Environment]::GetFolderPath('Desktop')) 'WA_AKG_DIAGNOSTICS'
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$filename = 'gateway-status-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.csv'
$csv = Join-Path $folder $filename
Write-Host "Reading $GatewayUrl/api/sessions for $Seconds seconds..." -ForegroundColor Cyan
Write-Host 'Only session IDs and statuses will be written to the CSV. Your API key is never logged.'
$deadline = (Get-Date).AddSeconds($Seconds)
try {
    while ((Get-Date) -lt $deadline) {
        $stamp = (Get-Date).ToString('o')
        $statusCode = 200
        $rows = @()
        try {
            $response = Invoke-RestMethod -Uri ($GatewayUrl + '/api/sessions') -Headers $headers -Method Get -TimeoutSec 10 -ErrorAction Stop
            if ($response.status -ne $true) { throw 'Gateway returned status=false' }
            foreach ($entry in @($response.data)) {
                if ($null -eq $entry) { continue }
                $sid = [string]$entry.sessionId
                if ($SessionId -and $sid -ne $SessionId) { continue }
                $runtime = [string]$entry.runtimeStatus
                if (-not $runtime) { $runtime = 'UNAVAILABLE (integration patch missing)' }
                $rows += [pscustomobject]@{
                    timestamp = $stamp; http_status='200'; session_id=$sid;
                    db_status=[string]$entry.status; runtime_status=$runtime; error=''
                }
            }
            if ($rows.Count -eq 0) {
                $rows = @([pscustomobject]@{ timestamp=$stamp; http_status='200'; session_id=$SessionId;
                    db_status=''; runtime_status='NO_MATCHING_SESSION'; error='' })
            }
        } catch {
            try { $statusCode = [int]$_.Exception.Response.StatusCode } catch { $statusCode = 0 }
            # Never include full exception text, raw response JSON, headers or credentials.
            $category = if ($statusCode -eq 401) {'UNAUTHORIZED'} elseif ($statusCode -eq 403) {'FORBIDDEN'}
                elseif ($statusCode -ge 500) {'GATEWAY_SERVER_ERROR'} else {'UNREACHABLE_OR_INVALID_RESPONSE'}
            $rows = @([pscustomobject]@{timestamp=$stamp; http_status=[string]$statusCode;
                session_id=$SessionId; db_status=''; runtime_status=$category; error=$category })
        }
        foreach ($r in $rows) {
            $r | Export-Csv -LiteralPath $csv -Append -NoTypeInformation -Encoding UTF8
            Write-Host ($r.timestamp + ' | ' + $r.session_id + ' | ' + $r.runtime_status + ' | HTTP ' + $r.http_status)
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
} finally {
    $headers['X-API-Key'] = ''
    $key = $null
}
Write-Host "Saved sanitized status timeline: $csv" -ForegroundColor Green
