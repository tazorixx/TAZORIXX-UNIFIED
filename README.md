# TAZORIXX Unified Online MVP — One Git repository

**Status: functional integrated source and offline-tested Python web API; not yet live-deployed or production-certified.**

This repository is an incremental cloud port of the working TAZORIXX desktop bot and its WA-AKG WhatsApp Gateway. It uses the actual Python `core.py` and `whatsapp_sender.py` source for generation and dispatch, rather than a placeholder sender.

## Important: which hosting is genuinely possible?

- **GitHub** stores source code and deploy workflows; it is *not* a permanent WhatsApp host.
- **Vercel** can host `apps/web` as a Next.js web frontend, while the Gateway, website generator and queue must run on a persistent host (see `docker-compose.yml`). WebSockets are now available on Vercel, but Function runtime limits and instance affinity still make it unsuitable as the single owner of a permanent Baileys socket/queue.
- **Vercel Hobby is personal/non-commercial only.** For TAZORIXX paid client work, use Vercel Pro or the **self-hosted frontend** (no Vercel needed).
- **Oracle Always Free A1 VM** is a possible $0 backend if available and accepted by the provider. Capacity/idle reclaim, account eligibility and architecture-specific Node native modules can still prevent success. There is no guarantee of free 24x7 uptime.
- **Render Free** sleeps after idle and has ephemeral disks; not recommended for an always-connected WhatsApp sender.

## Repository layout

```
apps/web/                 Next.js browser dashboard (Vercel or self-host)
services/studio/api.py    Authenticated web API (Excel, sites, images, templates, jobs)
services/studio/worker.py Single-instance due-job sender, off by default
services/studio/engine/   Actual TAZORIXX Python generator/sender code and HTML template
infra/                   Dockerfiles, Caddy HTTPS/reverse proxy
scripts/ASSEMBLE_WINDOWS.ps1  Copies your already-tested Gateway source without secrets
scripts/MIGRATE_LOCAL_DATA.ps1 Creates private backup ZIP for data transfer
scripts/import_private_data.py Restores data and remaps Windows website paths
scripts/VERIFY_REPO.ps1 Checks required files
```

**The ZIP intentionally does not include the user's local MySQL database, 317 business records, API key, session credentials or `.env`.** It also does not include Gateway source until you run `ASSEMBLE_WINDOWS.ps1`, because that script uses your latest patched local checkout instead of an out-of-date upstream snapshot.

## What is implemented

1. Private admin login with signed, HttpOnly, Secure-in-production session cookie and CSRF protection.
2. Excel import into the existing TAZORIXX business schema (same accepted workbook sheets).
3. List/search businesses, edit business overrides.
4. Edit industry profile headings/introductions.
5. Upload, list, remove licensed hero/gallery photos, reusing existing image optimizer.
6. Generate a validated website with original HTML template; browser preview and download.
7. Save explicit opt-in consent, schedule by India date and 24-hour IST time.
8. Edit and cancel unsent scheduled jobs with optimistic locking; delivery history retained.
9. Dedicated worker processing the original queue against WA-AKG via one document+caption request, with no blind retry after an ambiguous send.
10. Gateway connection summary, queue pause/resume, and basic dashboard metrics.

**Not yet ported:** all desktop advanced bulk-generation workflows, image/profile history UI, daily-delivery editor, website publishing/domain hosting, message receipt reconciliation, full MySQL/SQLite convergence, multi-tenant admin roles, and all prior WA-AKG security hardening. The original Gateway dashboard still exists separately at the protected Gateway URL. These must be completed before describing the product as fully production-ready.

## Start on Windows: assemble and push GitHub

1. Download and extract this ZIP into `C:\Users\ASUS\Desktop\Projects\TAZORIXX_UNIFIED_ONLINE_MVP`.
2. Keep the original `C:\Users\ASUS\Desktop\Projects\WHATSAPP_GATEWAY` untouched. Your local Gateway repair patches should already be tested there.
3. In PowerShell, run:

```powershell
cd "C:\Users\ASUS\Desktop\Projects\TAZORIXX_UNIFIED_ONLINE_MVP"
.\scripts\ASSEMBLE_WINDOWS.ps1
.\scripts\VERIFY_REPO.ps1
```

4. Inspect changes. Create a **new private GitHub repository** named e.g. `TAZORIXX-UNIFIED` (do not replace your working Gateway repo):

```powershell
git init
git branch -M main
git add .
git status --short
git commit -m "Initial unified TAZORIXX online MVP"
git remote add origin https://github.com/tazorixx/TAZORIXX-UNIFIED.git
git push -u origin main
```

Replace the remote with the actual URL of the private repository you created. Authenticate via the normal Git credential manager; do not paste passwords or API keys into commands.

> Do not commit `data/`, `.env`, `.sqlite3`, `transfer/`, WhatsApp auth keys, API keys or private Excel exports. The `.gitignore` excludes them, but **inspect `git status` before pushing**.

## Deploy

See **[docs/DEPLOY.md](docs/DEPLOY.md)** for Oracle Cloud + Docker + Caddy + WhatsApp onboarding and optional Vercel instructions. Sending is **OFF** by default until migration and testing.

## Testing

For the Python API (with the Python dependencies installed):

```bash
python -m pytest -q tests/test_studio.py
```

Offline test covers login, CSRF, Excel, generation, preview, opt-in, scheduling, rescheduling and cancel. The Docker build, Next.js production build, cloud QR pairing, live WhatsApp sending and system restart recovery must be tested in the intended hosting environment.

## Security and data expectations

- This is single-admin / single-worker, not multi-tenant SaaS.
- Client website HTML preview is CSP-sandboxed. Business images must be rights-confirmed.
- Do not use the gateway to message contacts without explicit consent. STOP/opt-out behavior must remain enforced.
- Do not rotate `AUTH_SECRET` / `ENCRYPTION_KEY` after WA-AKG sessions are migrated or paired.
- “Sent” means the Gateway accepted the WhatsApp API call, **not confirmed recipient delivery**.
- Back up both MySQL and `data/.tazorixx` and test restoration before real clients.
- The WA-AKG app has known security/reconnect findings; keep the Gateway admin behind Caddy Basic Auth and do not open it to unrestricted public traffic.
