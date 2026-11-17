#!/usr/bin/env node
// Applies two small API-level integrations to the existing WA-AKG checkout.
// Does not read .env, change the DB or modify auth credentials.
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
const root=process.cwd();
const changes=[
  {
    file:'src/app/api/sessions/route.ts',
    before:`        const sessions = await getAccessibleSessions(user.id, user.role);
        return NextResponse.json({ status: true, message: "Sessions retrieved successfully", data: sessions });`,
    after:`        const accessibleSessions = await getAccessibleSessions(user.id, user.role);
        // The persisted session status can say CONNECTED after its Baileys socket died.
        // Surface current runtime readiness instead of trusting the stale DB snapshot.
        const sessions = accessibleSessions.map(session => {
            const instance = waManager.getInstance(session.sessionId);
            const ready = Boolean(instance?.socket && instance.status === "CONNECTED");
            const runtimeStatus = ready ? "CONNECTED" : (
                instance?.status === "CONNECTING" || instance?.status === "SCAN_QR" ||
                instance?.status === "STOPPED" || instance?.status === "LOGGED_OUT"
                    ? instance.status
                    : session.status === "LOGGED_OUT" || session.status === "STOPPED"
                        ? session.status
                        : "DISCONNECTED"
            );
            return { ...session, status: runtimeStatus, runtimeStatus };
        });
        return NextResponse.json({ status: true, message: "Sessions retrieved successfully", data: sessions });`
  },
  {
    file:'src/app/api/messages/[sessionId]/[jid]/media/route.ts',
    before:`import { ChatService } from "@/modules/whatsapp/chat.service";`,
    after:`import { ChatService } from "@/modules/whatsapp/chat.service";
import { waManager } from "@/modules/whatsapp/manager";`
  },
  {
    file:'src/app/api/messages/[sessionId]/[jid]/media/route.ts',
    before:`        const decodedJid = decodeURIComponent(jid);`,
    after:`        // Definitive pre-send rejection. The client may retry this specific
        // 503 after reconnection: Baileys sendMessage has NOT been called yet.
        const active = waManager.getInstance(sessionId);
        if (!active?.socket || active.status !== "CONNECTED") {
            return NextResponse.json({ status: false,
                message: "WA_GATEWAY_SESSION_NOT_READY",
                error: "WA_GATEWAY_SESSION_NOT_READY" }, { status: 503 });
        }

        const decodedJid = decodeURIComponent(jid);`
  },
  {
    file:'src/app/api/messages/[sessionId]/[jid]/media/route.ts',
    before:`        return NextResponse.json({ status: true, message: "Operation successful", data: sent });`,
    after:`        // Only report accepted when Baileys supplies a message ID. A missing
        // ID is ambiguous; it must NOT silently be recorded as delivered.
        if (!sent?.key?.id) {
            return NextResponse.json({ status: false,
                message: "WA_GATEWAY_SEND_UNCONFIRMED", error: "WA_GATEWAY_SEND_UNCONFIRMED" },
                { status: 502 });
        }
        return NextResponse.json({ status: true, message: "Operation successful", data: sent });`
  }
];
function fail(msg) { console.error('ABORT: '+msg); process.exit(1); }
if (!fs.existsSync(path.join(root,'package.json')) || !fs.existsSync(path.join(root,'prisma','schema.prisma')))
    fail('Run from the WHATSAPP_GATEWAY repository root.');
const originals=new Map();
const changed=new Map();
for(const {file,before,after} of changes) {
    const filename=path.join(root,...file.split('/'));
    if (!fs.existsSync(filename)) fail(`Missing file: ${file}`);
    const content=changed.has(file)?changed.get(file):fs.readFileSync(filename,'utf8').replace(/\r\n/g,'\n');
    if(!originals.has(file)) originals.set(file,content);
    if(content.includes(after)) continue; // A repeated run should be safe.
    if(content.split(before).length!==2) fail(`Expected source fragment missing or ambiguous: ${file}. No source was changed.`);
    changed.set(file,content.replace(before,after));
}
const timestamp=new Date().toISOString().replace(/[:.]/g,'-');
const backup=path.resolve(root,'..',`WHATSAPP_GATEWAY_INTEGRATION_BACKUP_${timestamp}`);
fs.mkdirSync(backup,{recursive:true});
for(const [file,content] of changed) {
    if(content===originals.get(file)) continue;
    const dest=path.join(backup,...file.split('/'));
    fs.mkdirSync(path.dirname(dest),{recursive:true});
    fs.writeFileSync(dest,originals.get(file),'utf8');
}
for(const [file,content] of changed) {
    const dest=path.join(root,...file.split('/'));
    fs.writeFileSync(dest,content,'utf8');
    console.log('Updated:',file);
}
if (!changed.size) console.log('Already applied; no changes needed.');
else console.log('Original source backup:',backup);
console.log('Next: npx --no-install tsc --noEmit && npm run build');
