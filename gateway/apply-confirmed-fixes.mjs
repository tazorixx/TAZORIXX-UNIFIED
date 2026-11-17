#!/usr/bin/env node
// Safe, conservative patch for confirmed UI/socket event contract and desktop sender resume bugs.
// No database connections, no network access, no modification of .env, no messages sent.
import fs from 'node:fs';
import path from 'node:path';

const arg = (name) => { const i=process.argv.indexOf(name); return i>=0 ? process.argv[i+1] : null; };
const dryRun=process.argv.includes('--dry-run');
const projects=path.resolve(arg('--projects') || path.resolve(process.cwd(),'..'));
const gateway=path.resolve(arg('--gateway') || path.join(projects,'WHATSAPP_GATEWAY'));
const bot=path.resolve(arg('--bot') || path.join(projects,'wp script bot'));
const files = new Map();
const edits=[];
function abort(why){ console.error('ABORT:',why); process.exit(1); }
function read(root,relative){
 const target=path.join(root,...relative.split('/'));
 if (!fs.existsSync(target)) abort('Missing file: '+target);
 if (!files.has(target)) files.set(target,{original:fs.readFileSync(target,'utf8'),updated:fs.readFileSync(target,'utf8')});
 return target;
}
function replace(target,before,after,description){
 const item=files.get(target); const current=item.updated;
 if(current.includes(after)) {edits.push('Already applied: '+description);return;}
 const count=current.split(before).length-1;
 if(count!==1) abort(description+' expected 1 source match, found '+count+'. No files modified.');
 item.updated=current.replace(before,after);
 edits.push('Ready: '+description);
}
function regexReplace(target,re,replacement,description,minExpected){
 const item=files.get(target); const matches=[...item.updated.matchAll(re)];
 // On a repeated run, no match remains because sessionId is already present.
 if(matches.length===0 && item.updated.includes('sessionId: this.sessionId, status:')){
   edits.push('Already applied: '+description);return;
 }
 if(matches.length<minExpected) abort(description+' expected at least '+minExpected+' matches, got '+matches.length+'. No files modified.');
 item.updated=item.updated.replace(re,replacement);
 edits.push('Ready: '+description+' ('+matches.length+')');
}
if(!fs.existsSync(path.join(gateway,'prisma','schema.prisma'))) abort('Not a Gateway repo: '+gateway);
if(!fs.existsSync(path.join(bot,'whatsapp_sender.py'))) abort('Not a complete WP Script Bot: '+bot);
const manager=read(gateway,'src/modules/whatsapp/manager.ts');
const instance=read(gateway,'src/modules/whatsapp/instance.ts');
const ui=read(gateway,'src/components/dashboard/session-manager.tsx');
const botUi=read(bot,'whatsapp_ui.py');

// 1. Server emits messages scoped to sessionId; client requires sessionId to match.
regexReplace(instance,/\.emit\("connection\.update", \{ status:/g,'.emit("connection.update", { sessionId: this.sessionId, status:',
  'attach sessionId to all one-line WhatsApp instance status events',4);
replace(instance,'.emit("connection.update", {\n                status: this.status,',
  '.emit("connection.update", {\n                sessionId: this.sessionId,\n                status: this.status,',
  'attach sessionId to the pairing-code event');
replace(manager,'.emit("connection.update", { status: "STOPPED", qr: null });',
 '.emit("connection.update", { sessionId, status: "STOPPED", qr: null });',
 'attach sessionId to manually stopped session event');

// 2. SessionManager was not joining any Socket.IO session rooms.
replace(ui,`        socketInstance.on('connect', () => {
            console.log('Socket connected');
        });`,
`        // Authenticated socket must explicitly join each accessible session room.
        const refreshAndJoin = async () => {
            try {
                const res = await fetch('/api/sessions', { cache: 'no-store' });
                if (!res.ok) return;
                const response = await res.json();
                const list: Session[] = Array.isArray(response?.data) ? response.data : [];
                setSessions(list);
                if (socketInstance.connected) {
                    for (const item of list) if (item.sessionId) socketInstance.emit('join-session', item.sessionId);
                }
            } catch (err) {
                console.error('Unable to refresh session status', err);
            }
        };
        socketInstance.on('connect', () => {
            void refreshAndJoin();
        });`,
 'join Socket.IO rooms after reconnect and retrieve live statuses');
replace(ui,`        setSocket(socketInstance);

        return () => {
            socketInstance.disconnect();
        };`,
`        // Poll as a fallback when Socket.IO is unavailable or events are missed.
        const statusPoll = setInterval(() => { void refreshAndJoin(); }, 15000);
        setSocket(socketInstance);

        return () => {
            clearInterval(statusPoll);
            socketInstance.disconnect();
        };`,
 'fallback Gateway dashboard status refresh every 15 seconds');

// 3. The bot's initial connection status must use runtimeStatus when available.
replace(botUi,`            state=account.get('status') or account.get('state') or 'Disconnected'`,
`            state=account.get('runtimeStatus') or account.get('status') or account.get('state') or 'Disconnected'`,
 'display actual Gateway runtime status');

// 3b. Poll desktop connection health in a background thread. The original desktop
// UI only refreshed the status when the user pressed Refresh session.
replace(botUi,`import os
import sys
`,
`import os
import sys
import time
`,
 'add nonblocking desktop connection-poll timer dependency');
replace(botUi,`        self.accounts={}
        self.website_filter=tk.StringVar(value='All')`,
`        self.accounts={}
        self.health_results=SimpleQueue()
        self.health_thread=None
        self.next_health_check=0.0
        self.website_filter=tk.StringVar(value='All')`,
 'initialize desktop connection-health poll state');
replace(botUi,`    def _tick(self):
        if not self.winfo_exists():
            return
        if not self.worker_result.empty():`,
`    def _health_probe(self, url, key, selected_session):
        try:
            response=sessions(url,key)
            state='CONNECTED' if session_available(response,selected_session) else 'DISCONNECTED'
        except (OSError,RuntimeError,ValueError) as exc:
            text=str(exc)
            state='AUTHENTICATION_FAILED' if ('HTTP 401' in text or 'HTTP 403' in text) else 'GATEWAY_UNREACHABLE'
        self.health_results.put((selected_session,state))

    def _tick(self):
        if not self.winfo_exists():
            return
        if not self.health_results.empty():
            session,state=self.health_results.get_nowait()
            if session==self.session.get() and self.key:
                account=self.accounts.get(session,{})
                label=account.get('name') or session
                names={'CONNECTED':'Connected','DISCONNECTED':'Disconnected',
                       'AUTHENTICATION_FAILED':'Authentication Failed',
                       'GATEWAY_UNREACHABLE':'Gateway unreachable'}
                self.account_status.set(names.get(state,state) + ' · ' + label)
        if (self.key and self.session.get() and time.monotonic() >= self.next_health_check
                and not (self.health_thread and self.health_thread.is_alive())):
            self.next_health_check=time.monotonic()+15.0
            self.health_thread=Thread(target=self._health_probe,
                args=(self.gateway.get(),self.key,self.session.get()),daemon=True)
            self.health_thread.start()
        if not self.worker_result.empty():`,
 'refresh desktop connection label every 15 seconds without blocking the UI');

// 4. Restoring a connection should resume outstanding authorized jobs UNLESS the
// user explicitly paused delivery (persistent 'delivery_paused' setting).
replace(botUi,`            self.session_changed()
            with self.conn:
                self.conn.executemany`,
`            self.session_changed()
            if connected and not delivery_paused(self.conn):
                pending=self.conn.execute("SELECT 1 FROM whatsapp_demo_jobs WHERE status='scheduled' LIMIT 1").fetchone()
                if pending:
                    self.scheduler_on=True
            with self.conn:
                self.conn.executemany`,
 'restore unpaused pending-job processing after reconnect');

// 5. Confirm & Schedule explicitly queues work but did not start its foreground worker.
replace(botUi,`            self.status.set(f'Scheduled {len(jobs)} jobs. Use Background Sender or click Start / Resume and keep this dashboard open.')`,
`            self.status.set(f'Scheduled {len(jobs)} jobs. Keep this dashboard open for local delivery.')
            if not self.background.get():
                self.enable()`,
 'start foreground worker after scheduling a batch');

for(const message of edits) console.log(message);
const changed=[...files.entries()].filter(([_,value])=>value.updated!==value.original);
console.log('Files to change:',changed.length);
if(dryRun){ console.log('DRY RUN — no files modified.');process.exit(0); }
if(!changed.length){ console.log('Nothing to change.');process.exit(0); }
const timestamp=new Date().toISOString().replace(/[:.]/g,'-');
const backup=path.join(projects,`WA_AKG_STATUS_PATCH_BACKUP_${timestamp}`);
// Stage and backup all files before any modification.
fs.mkdirSync(backup,{recursive:true});
for(const [target,item] of changed){
 const prefix=target.startsWith(gateway)?'WHATSAPP_GATEWAY':'wp script bot';
 const relative=path.relative(target.startsWith(gateway)?gateway:bot,target);
 const dest=path.join(backup,prefix,relative);
 fs.mkdirSync(path.dirname(dest),{recursive:true});
 fs.writeFileSync(dest,item.original,'utf8');
}
for(const [target,item] of changed) fs.writeFileSync(target,item.updated,'utf8');
console.log('Applied proven UI/worker fixes.');
console.log('Original file backup:',backup);
console.log('Next: npx --no-install tsc --noEmit ; npm run build ; run offline bot tests');
console.log('NOTE: This does not solve every Baileys disconnection. Collect the disconnect code next.');
