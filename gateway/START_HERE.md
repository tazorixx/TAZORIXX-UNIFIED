# WA-AKG + WP Script Bot: Connection audit & safe status fix V3

**Goal:** fix two source-proven defects (the stale UI status and unsent due jobs when local worker wasn't started), then capture exact evidence for the separate Baileys disconnection issue. No installer should touch credentials, `.env`, MySQL, SQLite or user messages.

## 1. Existing folders

```
C:\Users\ASUS\Desktop\Projects\
  WHATSAPP_GATEWAY\              existing local, repaired Gateway
  wp script bot\                 existing MVP27.1 desktop bot
  WA_AKG_CONNECTION_AUDIT_V3\    extract this ZIP here
```

**Back up both projects first** (and the persistent SQLite database at `%LOCALAPPDATA%\TAZORIXX\tazorixx.sqlite3`). Before running the patch, **stop the Gateway and close the bot and old Windows background task**, so no sends occur during code changes. Keep the original archive or folders available. Never use the same WhatsApp linked account in two server processes.

## 2. Apply confirmed bug fixes (optional, conservative)

Open PowerShell:

```powershell
cd 'C:\Users\ASUS\Desktop\Projects\WA_AKG_CONNECTION_AUDIT_V3'
node .\apply-confirmed-fixes.mjs --projects 'C:\Users\ASUS\Desktop\Projects' --dry-run
node .\apply-confirmed-fixes.mjs --projects 'C:\Users\ASUS\Desktop\Projects'
```

The installer checks exact source fragments, prepares sibling backups of the 4 modified files and refuses to edit if the expected code is missing. A repeated run is safe. The patches:

- Gateway instance events include `sessionId`;
- Gateway session dashboard joins Socket.IO rooms and refreshes statuses every 15 seconds;
- Desktop bot checks runtimeStatus, refreshes statuses every 15 seconds asynchronously;
- On a newly connected session, a previous **unpaused** pending job can resume;
- Confirming a future batch starts its foreground worker when the background worker isn't active.

**This is not a general Baileys reconnect repair.** A `STOPPED` server-side session, a `401` logout or `440` replacement must be diagnosed individually.

## 3. Validate before sending

```powershell
cd 'C:\Users\ASUS\Desktop\Projects\WHATSAPP_GATEWAY'
npx --no-install tsc --noEmit
npm run build

cd 'C:\Users\ASUS\Desktop\Projects\wp script bot'
py -3 -m py_compile .\whatsapp_ui.py
py -3 -m unittest -q test_gateway_integration_repair test_whatsapp_sender test_sender_controls test_daily_delivery test_website_zip_sender test_repeat_delivery_mvp23 test_simple_ui_mvp22
```

The offline test suite is timezone-dependent. If tests fail around scheduling dates, run from a PowerShell process with `$env:TZ='Asia/Kolkata'` set for that test process; don't change the system timezone globally. **Do not send messages until TypeScript/build and local bot tests pass.**

## 4. Capture the Gateway's exact disconnect reason

Open Gateway terminal and start recording logs (make sure Gateway is not already running in another terminal):

```powershell
$folder = Join-Path ([Environment]::GetFolderPath('Desktop')) 'WA_AKG_DIAGNOSTICS'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
cd 'C:\Users\ASUS\Desktop\Projects\WHATSAPP_GATEWAY'
npm run dev *>&1 | Tee-Object -FilePath (Join-Path $folder 'gateway-console.log')
```

Open a SECOND PowerShell window (do not stop Gateway):

```powershell
cd 'C:\Users\ASUS\Desktop\Projects\WA_AKG_CONNECTION_AUDIT_V3'
powershell -ExecutionPolicy Bypass -File '.\watch-gateway.ps1' -SessionId 'd464ph' -Seconds 180
```

Enter the Gateway API key in the secure prompt. The key is used in RAM only; it is never saved by the watcher. If your current Session ID is different, replace `d464ph`.

Then run two small batches to **consenting test contacts only**, without manually refreshing between batches. Capture the moment the status first changes. The gateway console may contain personal numbers and other sensitive data; review/redact those lines before sharing. The watcher CSV records statuses and IDs only.

## 5. Check whether Job #3 was ever sent

```powershell
cd 'C:\Users\ASUS\Desktop\Projects\WA_AKG_CONNECTION_AUDIT_V3'
py -3 .\read-bot-job.py 3
```

`status=scheduled` means no accepted send is recorded; `status=uncertain` means an attempt may have happened; `status=sent` means the Gateway returned an accepted message result, not necessarily that the recipient received it.

## 6. Do not push to GitHub or deploy yet

Until the actual close code and two successful live test cycles have been verified, **do not** expose this Gateway on the public Internet. It still has separate security issues outlined in `AUDIT_REPORT.md`. It can later be one repo with a Vercel dashboard plus a persistent always-on WhatsApp worker (not solely Vercel serverless functions).

Use `git status` and `git diff` in `WHATSAPP_GATEWAY` to review code changes. Keep the generated timestamped backup folder until you verify everything.
