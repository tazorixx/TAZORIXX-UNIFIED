# TAZORIXX MVP27.1 + WA-AKG v1.7 — Source-Code Connection Audit

**Reviewed:** the public GitHub repository `tazorixx/Whatsapp-gateway` at the previously inspected `main` revision `672426dcbbb1f2e70cd911d3891c3192de71badf`, the previously provided WA-AKG broadcast repair kit, its integration patch, and the user-uploaded `WP_SCRIPT_BOT_COMPLETE_READY_v2.zip`. The live Windows working tree and WhatsApp server-side connection reasons were *not* available to inspect remotely. **Code conclusions are not claims about the exact runtime disconnect event.**

## The exact path through both applications

1. TAZORIXX UI in `whatsapp_ui.py` obtains selected websites and intended recipients (`send_now`, `send_multiple_now`, `schedule_bulk`).
2. TAZORIXX writes durable SQLite jobs (`whatsapp_sender.py:353-635`) with status `scheduled`.
3. Its foreground tick (`whatsapp_ui.py:1064-1089`) or optional Windows Task Scheduler worker (`background_sender.py:173-230`) decides when to process jobs.
4. `process_connected_due` (`whatsapp_sender.py:932-971`) calls the Gateway `GET /api/sessions` and requires `session_available(...)`.
5. `process_due_one` (`whatsapp_sender.py:827-901`) marks the attempt `uncertain` before the HTTP call and submits a single multipart document+caption to `POST /api/messages/{sessionId}/{jid}/media`.
6. The Gateway API authenticates the API key, checks session authorization, and invokes `ChatService.sendMediaMessage` (`src/app/api/messages/[sessionId]/[jid]/media/route.ts`).
7. `ChatService` obtains `waManager.getInstance(sessionId)` and calls Baileys `socket.sendMessage` (`src/modules/whatsapp/chat.service.ts:377-449`).
8. The socket's `connection.update` is processed by `WhatsAppInstance` (`src/modules/whatsapp/instance.ts:109-255`).
9. The bot records either `sent` (provider returned a message key), `scheduled` (gateway's explicit pre-send 503) or `uncertain` (other errors/timeouts). `sent` is **not** guaranteed phone delivery.

## Proof-based findings

### F1. The supplied “Job #3 — Ready” popup is not the Gateway's disconnect message (confirmed)

In the screenshot, job #3 is **scheduled** for 2026-11-16 21:54 IST, while the last update was 21:53. The popup contains no HTTP error or WhatsApp disconnect code. `whatsapp_sender.py:696-714` displays due scheduled jobs as `Ready`. `whatsapp_ui.py:560-570` displays `Attempts: #3 scheduled` based on history entry IDs; it does not mean 3 transport retries. A message showing `Ready` with blank `Sent / recorded` has no recorded acceptance.

**Action:** inspect `read-bot-job.py 3` *after the due time* to distinguish `scheduled`, `uncertain`, and `sent` in the saved database.

### F2. Due jobs may have no active sender (confirmed P0)

- `whatsapp_ui.py:71` initializes `scheduler_on=False`.
- `whatsapp_ui.py:1064-1070` calls `process_connected_due` only if `allow_new` (the `scheduler_on` flag) is true and delivery is not paused.
- `whatsapp_ui.py:1076-1089` periodically starts a foreground tick but passes the potentially **false** flag.
- `whatsapp_ui.py:1138-1142` stops foreground scheduling when the window closes.
- `background_sender.py:193-204` requires `background_enabled`, which was **Off** in an earlier user screenshot.
- `whatsapp_ui.py:887-900` confirms a future batch but does **not** call `enable()`; its status message asks the user to start or enable the worker. Conversely, other sending paths call `enable()` explicitly.

**Impact:** a valid queued job can remain `Ready` after its due time, unrelated to whether the Gateway is online. **Confirmed fix candidate included:** foreground sender starts when a future batch is confirmed, and pending authorized work resumes when the Gateway is reconnected, unless the user explicitly paused delivery. This still requires the desktop window to remain running or the Windows background worker to be enabled.

### F3. Gateway session dashboard cannot receive/process its own room events reliably (confirmed P0)

- `src/components/dashboard/session-manager.tsx:45-77`: creates Socket.IO connection and listens for `connection.update`, but never emits `join-session`.
- `src/server/socket.ts:115-140`: the server only calls `socket.join(sessionId)` after receiving authenticated `join-session`.
- `src/modules/whatsapp/instance.ts:125,142,168,188,206,229` emits to `this.io.to(this.sessionId)` but the event object lacks `sessionId`.
- `src/components/dashboard/session-manager.tsx:58-63` identifies the record using `data.sessionId`; without it the status update can't match a session.

**Impact:** the dashboard can show old state until it fetches data again on refresh. **Included fix:** add sessionId to event payloads, join rooms and refresh on Socket.IO reconnect, with a 15-second fallback poll. This repairs status visibility, not the underlying Baileys transport.

### F4. TAZORIXX “Connected/Disconnected” label can become stale (confirmed P1)

- `whatsapp_ui.py:606-625` formats an in-memory `self.accounts` snapshot.
- `whatsapp_ui.py:627-677` updates that snapshot on Connect/Refresh Session.
- `whatsapp_ui.py:1076-1089` refreshes job history but **not** the connection snapshot periodically.

**Impact:** a manual Refresh Session appears to fix an offline label even without reconnecting Baileys. **Included fix:** nonblocking status polling every 15 seconds, with `CONNECTED`, `DISCONNECTED`, `AUTHENTICATION_FAILED`, or `GATEWAY_UNREACHABLE` displayed separately.

### F5. The Gateway's reconnect limit eventually auto-stops transient failures (confirmed P0)

- Original `src/modules/whatsapp/instance.ts:21` allowed 3 attempts.
- Original `instance.ts:182-217` increments `reconnectCount`, and after the configured limit sets `isStopped=true`, `status=STOPPED`, `socket=null`, and removes the instance from the manager.
- The earlier local repair kit increased the limit to 8 and added exponential backoff but **retained the auto-stop behavior**.
- The earlier manager repair excludes `STOPPED` sessions during load, so restarting the app does not automatically restore auto-stopped sessions.

**Impact:** a temporary outage long enough to exhaust retries can become an outage requiring manual intervention. **Needs a reason-aware reconnection redesign**, not merely another increase to the attempt count. `401/loggedOut` and other invalid-session states must not be retried as ordinary transient network faults. See Baileys troubleshooting documentation.

### F6. Missing single-flight initialization lock in Baileys manager (confirmed P0 race)

- `src/modules/whatsapp/instance.ts:46-61` asynchronously reads DB credentials and awaits a Baileys version lookup before setting `this.socket`.
- Original `src/modules/whatsapp/manager.ts:131-153` can invoke `instance.init()` for an existing instance.
- The earlier local repair guards initialization only when `this.socket` already exists in one of several statuses; during the async window before `makeWASocket`, `socket` remains null and a second call can begin initialization.
- Original `instance.ts:109-111` binds callbacks to mutable `this`, allowing overlapping connection updates from old/new sockets; the earlier local repair adds a partial socket-identity check.

**Impact:** multiple sockets can compete for one session or write conflicting statuses. The required fix is a serialized/single-flight initialization Promise, socket-generation guards, and cancellation of stale reconnect timers. **Not changed by the conservative V3 patch** because its interaction with the user's actual connection codes still requires testing.

### F7. Silent Prisma auth-state failures (confirmed P0)

- `src/modules/whatsapp/auth/usePrismaAuthState.ts:9-24` logs a read exception and returns `null`.
- `usePrismaAuthState.ts:56` treats `null` as a reason to create fresh credentials.
- `usePrismaAuthState.ts:28-41` swallows failures to store credentials.

**Impact:** a temporary DB error or incorrect encryption key can be confused with a missing session and cause avoidable reconnection/login problems. Fix by distinguishing a genuinely absent credential record from a DB/decryption failure, making writes fail loudly, and backing up auth state before any migration. **Do not delete the auth DB or rotate AUTH_SECRET/ENCRYPTION_KEY as a shortcut.**

### F8. Gateway's two schedulers conflict (confirmed P1)

- `src/modules/whatsapp/manager.ts:12-14` starts cron in its constructor (also executed during Next.js build imports).
- `src/lib/cron.ts:6-103` scans scheduled messages every minute using the external session slug via `msg.session.sessionId`.
- `src/modules/whatsapp/scheduler.ts:5-80` runs a second 30-second loop, but at line 22 passes `msg.sessionId` (the database CUID) to a manager keyed by the external slug.
- Both query `PENDING`; fixing the CUID problem without atomic claiming can create duplicate sends.

**Impact:** misconfigured native scheduled messages and job conflicts. Not the same path as TAZORIXX's local SQLite scheduler.

### F9. Gateway broadcast repair is not used by TAZORIXX (confirmed P1)

TAZORIXX `whatsapp_sender.py:787-825` calls `/api/messages/[sessionId]/[jid]/media`. The earlier Gateway broadcast repair lives at `/api/messages/[sessionId]/broadcast`, a different endpoint. The earlier **integration** repair added a definitive pre-send `503 WA_GATEWAY_SESSION_NOT_READY` and an acceptance ID check to `/media`, but does not create a durable Gateway-side queue for every desktop job.

### F10. Ambiguous network results are intentionally preserved, but cannot auto-retry safely (confirmed P1)

`whatsapp_sender.py:827-901` marks an attempt `uncertain` before calling the network. Only a definitive pre-send 503 moves the same job back to `scheduled`; all other failures remain `uncertain`, because the Gateway might have accepted the message before the response was lost. This is appropriate duplicate prevention, but an operator or a Gateway idempotency/acknowledgement mechanism is needed for automatic reconciliation.

### F11. API `sent` is not the same as recipient `delivered` (confirmed P1)

`ChatService.sendMediaMessage` awaits `socket.sendMessage` and returns a Baileys result; the integration patch looks for `key.id`. Neither code checks the recipient's `delivered` or `read` receipt. Logs/UI must distinguish `queued`, `submitted/accepted`, `server acknowledged`, `delivered`, `read`, `failed`, and `uncertain` when those events are available. Some delivery receipts may never be obtainable; no implementation can guarantee 100% recipient delivery.

### F12. Security blockers before a public deployment (previously confirmed P0)

- Public first-account `SUPERADMIN` promotion in `src/app/api/auth/register/route.ts`.
- Webhook API responses expose webhook secrets, including to users with shared session access (`src/app/api/webhooks/[sessionId]/route.ts` and related routes).
- URL validator's DNS check and subsequent outbound fetch are separate operations (SSRF rebinding risk in `src/lib/security.ts` and `src/lib/webhook.ts`).
- Other defects include shared-user destructive actions, stale socket role claims, and unbounded media uploads. Audit and repair before publishing to the Internet. Do not public-expose this Gateway just because it builds successfully.

## What is proven, and what is not

**Proven by source:** a due job can be unsent because the worker is disabled; dashboard and desktop status views can be stale; reconnection can auto-stop; concurrent initialization is not prevented; auth DB read failures can be suppressed; there are duplicate schedulers; desktop uses the media endpoint.

**Not yet proven by screenshots/source alone:** whether a particular WhatsApp Web disconnect was caused by `401`, `408`, `440`, `515`, Wi-Fi interruptions, another server linked to the same account, or auth database failures. We need live `connection.update` disconnect-code logs. A source-code audit cannot infer the actual remote event that the program never logged.

## Priority repair order

1. **P0 worker + UI (V3 included):** start foreground sender when scheduling, honor explicit pause, join session rooms, include `sessionId` in socket events, poll status without UI blocking.
2. **P0 Baileys recovery (further work required):** log disconnect code/reason; single-flight `init()`, stale-event guard, reason-specific reconnection loop/backoff, preserve credentials and stop only when reauthentication or manual attention is truly required.
3. **P0 credential durability:** fail closed on DB/decryption errors; test rolling update of auth data, database outage, app reboot, and key rotations.
4. **P1 queue correctness:** one durable worker, transactional claims, idempotency by business+recipient+campaign, per-stage statuses and receipt reconciliation; never replay uncertain sends blindly.
5. **P1 operational tests:** two batches back to back, forced outage between recipients, 30-second outage and 3-minute outage, session restart, browser refresh, Windows process restart, media upload timeouts.
6. **P0 public deployment gate:** fix the audit's security issues before publishing; use always-on backend with persistent database and durable file storage; Vercel can host UI, but do not rely on a short-lived function alone to own a Baileys socket.

## Audit verification

- Parsed the *actual user-uploaded* `WP_SCRIPT_BOT_COMPLETE_READY_v2.zip` and compared its send path to the GitHub Gateway code and earlier patch installers.
- Ran 44 focused offline bot tests with `TZ=Asia/Kolkata`: **44/44 passed**. These tests do not simulate actual Baileys socket closes.
- Tested the V3 conservative patch on a **disposable fixture**: dry run, apply, second dry run (idempotence), Python syntax. The true Windows app build after V3 **has not been run**.
- Did **not** access the user's local SQLite/MySQL databases, Windows logs, WhatsApp app, or live Baileys events. No GitHub commit, push, or deployment was made.

## Read-only runtime evidence to capture

1. Start `npm run dev` under `Tee-Object` to record Gateway `Instance` and `Manager` logs, including the disconnect code printed by the earlier repair kit.
2. Run `watch-gateway.ps1` during both test sending cycles, giving API credentials only through its secure prompt. It logs only statuses, no API key.
3. Run `read-bot-job.py 3` to see if job #3 stayed queued, had its attempt begin, was accepted, or became uncertain.
4. Share those **sanitized** outputs, not `.env`, private keys, auth-state records, full payloads, or contact lists.

This report is a source-level audit; it should not be represented as a final confirmed fix of every WhatsApp disconnection.
