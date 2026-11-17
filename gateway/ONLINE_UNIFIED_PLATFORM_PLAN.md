# Future TAZORIXX Unified Cloud Platform — build plan (not implemented in this ZIP)

## Business goal

One logged-in web admin for: Excel import and lead review; business records; website generation with previews/edits/publishing; approved WhatsApp accounts and consent; opted-in campaigns; immediate/scheduled/daily batches; dashboards; recipient-by-recipient delivery state; manual review of ambiguous sends; backups and audit logs.

## Recommended project organization (one Git repo, one ZIP)

```text
TAZORIXX_UNIFIED/
  apps/web/            # Next.js dashboard, public website preview
  apps/gateway/        # Long-lived WA-AKG/Baileys connection process
  services/generator/  # Python Excel ingestion + static demo HTML generator
  services/worker/     # One queue owner for campaigns/demos/delivery
  packages/contracts/  # Shared API schemas, event names, validation
  infra/               # Docker Compose, environment examples, health checks
  docs/                # Onboarding, operations, migration, recovery
```

**Do NOT deploy `app.py`/Tkinter unchanged to Vercel.** It is a Windows GUI application that reads the local filesystem and SQLite. Extract its generation/validation functions into a reusable Python service with authenticated HTTP jobs. Do not scrape local personal files from the browser. Use a secure upload and import workflow.

## MVP online architecture

1. **Web UI**: Next.js. Optionally Vercel for frontend; or serve the entire web UI on the same VPS for the simplest deployment.
2. **Gateway**: one persistent Node.js process running Baileys with reconnect state machine, encrypted persisted auth, one active owner per session. Support one test account first. Implement graceful restart and owner-only control API.
3. **Database**: one managed/dedicated MySQL 8 service or shared MySQL container on the VPS with backup; migrate business, preview, opt-in and job data from the Windows TAZORIXX SQLite database using an audited one-time migration, never by naïvely copying SQLite into MySQL.
4. **Worker**: owns message queue, reservations, bounded concurrency, connection-aware pause/resume, backoff, dead-letter/uncertain states, and checkpoint after every recipient. Explicit job states: draft, queued, waiting-for-session, sending, accepted, delivered (only with verified receipt evidence), uncertain, rejected, cancelled.
5. **Storage**: persist generated HTML, ZIPs, images and WhatsApp media separately from ephemeral application containers (e.g. S3-compatible object storage). Do not expose session keys, `.env`, webhook secrets or private uploaded content publicly.
6. **Security**: fix prior WA-AKG P0 findings before public hosting—public SUPERADMIN bootstrap, leaking webhook secrets/logs, insufficient SSRF defenses, shared-user destructive permissions and stale token roles. Rate-limit, audit and encrypt secrets and WhatsApp auth. HTTPS on public URLs, role-based access, data deletion and backup/restore tests.
7. **WhatsApp interface**: recommend a single document+caption send for website demos to avoid two-part partial-success. Every selected recipient becomes a durable job. Record WhatsApp message ID separately from actual receipt. Retain opt-in and opt-out checks. Never automatically retry an ambiguous send without reconciliation.

## Delivery strategy

**Phase A — repair existing local apps (in current repair ZIP)**: local runtime contract, strict status checks, one media call per demo, faster queue configuration, pre-send recoverability. Execute two consecutive test batches. Do not publicly deploy on the basis of unit tests alone.

**Phase B — unify the local data and UI**: create new private monorepo, import Python generator as service, integrate leads and previews with the existing Next.js dashboard, centralize permissions and delivery jobs. Leave legacy desktop repo as recovery until data parity verified. Validate 100% of imported businesses, files, permissions, and message-history rows.

**Phase C — make it persistent online**: deploy Docker Compose to one small always-on VPS (web + gateway + Python generator + queue + MySQL) or use managed DB/storage. Continuous WhatsApp workers require durable state and cannot depend on the user's desktop. Add TLS, domain, monitoring, backup, restart and logs; test stopping/restarting the process mid-batch.

**Phase D — optional Vercel UI and official WhatsApp alternative**: frontend can move to Vercel while the gateway stays on always-on infrastructure. Vercel Functions now support WebSockets (as of October 2026), but their invocation duration is finite and instances scale down; WebSocket support does not remove the need for a continuously owned, stateful WhatsApp session process. For broader production outreach, evaluate the official Meta WhatsApp Cloud API (consent, templates, business account approval, platform costs). It changes the gateway design and is a distinct migration.

## Cost reality and limitations

GitHub hosts source and runs CI workflows, not a permanent backend server. GitHub Actions runners have job execution limits, so a scheduled action is not a suitable permanent Baileys connection host. Free Vercel can host a lightweight dashboard in its published limits; it is not a replacement for a durable 24×7 queue, database and session connection. A small paid VPS is the straightforward initial always-on option. Free provider tiers can be used for limited experiments, but they may suspend, recycle, or reset processes. Verify current pricing and terms before choosing providers.

A single ZIP is possible for source/distribution; a **single giant source file** is not advisable. Keeping the code in modules allows reliable tests, private credentials, safe upgrades, and independent recovery while still being one integrated product.

## Acceptance gates before going online

- Local 2→10 recipient tests across two consecutive campaigns without refresh.
- Simulated transient disconnect: pending jobs resume once, unknown/uncertain never resend blindly.
- DB restart / app restart: no duplicate deliveries, old jobs recover correctly, no orphan auth records.
- Permission/opt-out check enforced at scheduling and immediately before submit.
- Secure API and admin access, no secret exposures, invalid recipient/file/oversize rejection.
- Full data backup/restoration plus preview parity after TAZORIXX SQLite migration.
- Deployment uses one worker per WhatsApp account, audited migration and security review.
