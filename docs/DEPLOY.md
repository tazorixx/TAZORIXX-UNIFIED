# DEPLOY — TAZORIXX web + persistent WhatsApp on one cloud host

> **Read before running:** This is a real deployable starter, not a guarantee of live WhatsApp delivery. No cloud server, domain, API key, real account, or production database was available for verification. Use one test account and opted-in recipients first.

## A. Choose a host

Option 1 (zero software hosting charge when accepted/available): **Oracle Cloud Always Free Ampere A1 Ubuntu**. Typical Always Free entitlement covers up to 2 Arm OCPUs / 12 GiB RAM, subject to current provider rules and capacity. A1 ARM compatibility with WA-AKG's native Node modules is **unverified**; the build may fail. Oracle may reclaim idle VMs. AMD Always Free micro (1 GiB RAM) is unlikely to fit the Next+MySQL stack. Only use Always Free marked resources; watch billing.

Option 2: a small always-on Linux VPS with enough RAM. More predictable, not free.

Use two DNS names pointing to the host's public IPv4, e.g.:

- `wa.YOURDOMAIN` → protected WA-AKG Gateway + `/api/studio/*` API
- `studio.YOURDOMAIN` → web dashboard (if self-hosting)

A free DNS subdomain provider can work for a pilot, subject to its availability. Allow inbound SSH (preferably from your IP), TCP 80 and 443; also allow UDP 443 for HTTP/3 if desired. Do **not** open ports 3306, 3030, 8000 or 3000 to the internet.

Install Docker Engine and Docker Compose V2 from the official Ubuntu install guide: https://docs.docker.com/engine/install/ubuntu/ . Use an SSH key and update the system.

## B. Get your source onto the server

On your PC run `scripts/ASSEMBLE_WINDOWS.ps1`, check `git status`, and push the private repo. On Linux clone it through GitHub authentication:

```bash
git clone YOUR_PRIVATE_REPO_SSH_URL tazorixx
cd tazorixx
cp .env.example .env
chmod 600 .env
```

Edit `.env` using `nano .env`. Change every REPLACE value. Generate secrets privately using `openssl rand -hex 32` (run separately for each). Choose an alphanumeric MySQL password to avoid URL-encoding problems in `DATABASE_URL`.

Keep `ENABLE_SENDING=false` during setup. Set both `GATEWAY_DOMAIN` and `STUDIO_DOMAIN` to real DNS names.

For the Caddy Gateway Basic-Auth password hash, use:

```bash
docker run --rm -it caddy:2 caddy hash-password
```

Enter a strong *separate* password into the interactive prompt. Copy the resulting bcrypt hash into `.env` as **one single-quoted literal**:

```
GATEWAY_BASIC_HASH='$2a$...'
```

The single quotes stop Docker Compose from interpreting the `$` signs as environment variables. Do not put the Basic-Auth *plaintext password* in `.env`.

### Important secret rules

- New Gateway database: create new `AUTH_SECRET` and `ENCRYPTION_KEY` before pairing.
- Migrating the existing WA-AKG MySQL/auth-state DB: preserve the **original encryption key AND the original AUTH_SECRET**; otherwise credentials/sessions may become unreadable. Do not export or upload these to GitHub.
- `GATEWAY_API_KEY` and `GATEWAY_SESSION_ID` may initially be blank. Set them after the cloud Gateway is configured.

Check configuration without printing secrets:

```bash
docker compose config --quiet
```

## C. Optional: migrate 317 desktop businesses before first start

On Windows, **close desktop TAZORIXX and its background sender**, then:

```powershell
cd "C:\Users\ASUS\Desktop\Projects\TAZORIXX_UNIFIED_ONLINE_MVP"
.\scripts\MIGRATE_LOCAL_DATA.ps1
```

The script creates a **private ZIP on your Desktop** containing a consistent SQLite snapshot and `Website_Exports`. Transfer this ZIP with SFTP/SCP to the VM; never push it to GitHub or email it to strangers.

On the Linux VM, from the repo root and BEFORE the app first creates `data/.tazorixx/tazorixx.sqlite3`:

```bash
python3 scripts/import_private_data.py /home/ubuntu/TAZORIXX_PRIVATE_DATA_YYYYMMDD-HHMMSS.zip
```

This checks ZIP paths, copies the source database/exports into `./data/.tazorixx` and rewrites Windows HTML paths to **container paths** `/data/.tazorixx/Website_Exports/...`. It refuses to overwrite an existing cloud DB and reports missing/ambiguous files. Check any reported unresolved paths and regenerate those sites.

**MIGRATION DOES NOT MIGRATE THE SEPARATE WA-AKG MySQL database, chats, or Baileys auth credentials.** An advanced migration requires a private consistent MySQL dump/restoration and preservation of encryption keys. For a test-only cloud instance, pair a different test account or a separately reviewed new session. Ensure scheduled jobs point at the correct cloud session. **Never operate the same WhatsApp account from two Baileys hosts simultaneously.**

## D. First backend deployment

```bash
mkdir -p data gateway-uploads
chmod 700 data gateway-uploads
# First build can be resource-heavy, especially on ARM

docker compose build app

docker compose up -d db
# On the initially EMPTY MySQL DB only: initialize the upstream Gateway schema.
docker compose run --rm app sh -lc 'cd /opt/tazorixx/gateway && npx prisma db push'
# Self-host web too (the fully free/commercial-friendly option):
docker compose --profile selfhost-web up -d --build
```

Note: `prisma db push` should **not** be run blindly against a restored or live MySQL database. After initial setup use audited schema migrations and backups. The upstream Gateway can run additional auto-migrations at startup; review before production upgrades.

Inspect logs and the health endpoint:

```bash
docker compose ps
docker compose logs --tail=100 app
curl -i https://wa.YOURDOMAIN/api/studio/health
```

Expected: `{"ok":true,...,"sending_env_enabled":false}`.

The self-hosted dashboard will be at `https://studio.YOURDOMAIN`. Log in using `STUDIO_ADMIN_PASSWORD` from `.env`. Use the Gateway `https://wa.YOURDOMAIN` with your Caddy Basic-Auth credentials and the Gateway's own separate login.

## E. Connect WA-AKG and choose ONE sender

1. Create/sign into the Gateway admin, then pair a consenting test WhatsApp account and verify runtime `CONNECTED`.
2. Generate/get an account API key from `https://wa.YOURDOMAIN/api/user/api-key` **after authenticating to the Gateway** (creating one invalidates an older key). Don't share it in screenshots.
3. Put the key and the exact cloud `sessionId` into `.env` as `GATEWAY_API_KEY`, `GATEWAY_SESSION_ID`. Update the API key only server-side; **never** in Vercel environment variables or browser JavaScript.
4. Recreate the app container to load new environment variables:

```bash
docker compose --profile selfhost-web up -d app
```

5. Leave `ENABLE_SENDING=false`. From Studio import a test Excel row, generate its website, record explicit opt-in, schedule a test job, and inspect the queue.
6. Before enabling: stop the original desktop background sender and all other gateways/worker instances for that account. Ensure there are no stale imported pending jobs likely to cause accidental sending.
7. Change `ENABLE_SENDING=true` only once the migration, queue, consent and session IDs are verified; then run `docker compose up -d app` to restart the worker.
8. Send to a **consenting** test recipient, verify the phone, send a second cycle without refresh, temporarily restart the process and confirm pending jobs are handled without duplicate sending. Inspect any `uncertain` jobs manually.

The worker checks approximately every 2 seconds but **cannot guarantee exact-second delivery**. WhatsApp connectivity, upload latency and server restarts can delay the request. `sent` records Gateway acceptance, not recipient delivery. Avoid high-volume/unsolicited messaging.

## F. Optional Vercel frontend

If Vercel is appropriate for your usage and plan, use the SAME private GitHub repository:

1. Vercel → Add New Project → import the repository.
2. Set **Root Directory:** `apps/web`.
3. Framework: Next.js; Build command `npm run build` (default is fine).
4. Environment variables **ONLY**:
   - `STUDIO_ORIGIN=https://wa.YOURDOMAIN`
   - `NEXT_PUBLIC_GATEWAY_URL=https://wa.YOURDOMAIN`
5. Deploy and open the Vercel URL. The app's `/api/studio/*` calls are routed through a Next.js rewrite to the HTTPS backend origin.
6. Log in through your private Studio password. Verify signed Secure cookies survive the proxy and that all mutation requests have CSRF headers.

**Do not set** `GATEWAY_API_KEY`, MySQL URL, `AUTH_SECRET`, `ENCRYPTION_KEY`, or `STUDIO_ADMIN_PASSWORD` in Vercel; they belong only on the persistent backend.

**Vercel Hobby is for personal, noncommercial projects only.** TAZORIXX sales/business operation would normally require Pro or using the self-hosted frontend from section D.

## G. Production safety, backups, and limits

- Keep Gateway behind Caddy Basic-Auth and Studio private; the upstream has security findings (open admin bootstrap, socket authorization, webhooks, SSRF and dependency audit). Fix and test before broad public launch.
- Set `ENABLE_TELEMETRY=false` (done in Compose).
- Back up `./data/.tazorixx`, `./gateway-uploads`, Caddy certificates and MySQL volume; regularly test restores.
- Deploy exactly ONE worker for the bot queue and ONE WhatsApp socket owner per account. Do not scale the app horizontally without a distributed lease and queue.
- GitHub Actions scheduled jobs and Vercel Functions are NOT a persistent WhatsApp engine.
- For production outbound messaging at scale consider the **official Meta WhatsApp Business Cloud API**, with verified consent/templates and its own cost/terms.
- This is an MVP, not a fully audited production release. No live cloud build/pairing/send test has been completed in this session.
