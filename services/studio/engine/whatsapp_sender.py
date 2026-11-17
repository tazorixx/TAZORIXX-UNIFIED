"""Opt-in WA-AKG website-demo delivery, using automatically validated website exports (no manual preview approval).

No network work happens on import. Credentials are never written to SQLite.
Scheduled deliveries are processed while the sender dashboard is open.
"""
import hashlib
import http.client
import json
import mimetypes
import shutil
import tempfile
import zipfile
from html.parser import HTMLParser
import os
import re
import secrets
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

PRICE = '₹9,999'
CONTACT = '9225344854'
EMAIL = 'tazorixx.services@gmail.com'
KOLKATA = timezone(timedelta(hours=5, minutes=30), 'Asia/Kolkata')
ATTEMPT_IN_PROGRESS = 'Attempt begun; verify chat before retrying'
DEFAULT_URL = 'http://localhost:3030'

class GatewayNotReady(RuntimeError):
    """Definitive pre-send rejection: the gateway never submitted this attempt."""
    pass

# Persistent, app-managed exports; never depend on temporary generation paths.
def website_store():
    from core import APP_DIR
    folder=Path(APP_DIR)/'Website_Exports'
    folder.mkdir(parents=True,exist_ok=True)
    return folder



def prepare_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS whatsapp_demo_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        session_id TEXT NOT NULL,
        recipient TEXT NOT NULL,
        html_path TEXT NOT NULL,
        html_sha256 TEXT NOT NULL,
        message TEXT NOT NULL,
        consent_note TEXT NOT NULL,
        approved_at TEXT NOT NULL,
        schedule_utc TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'scheduled'
          CHECK(status IN ('scheduled','sending','sent','failed','uncertain','cancelled')),
        error TEXT NOT NULL DEFAULT '',
        api_message_ref TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )''')
    conn.commit()
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        cols={r[1] for r in conn.execute('PRAGMA table_info(whatsapp_demo_jobs)')}
        if 'attachment_path' not in cols:
            conn.execute("ALTER TABLE whatsapp_demo_jobs ADD COLUMN attachment_path TEXT NOT NULL DEFAULT ''")
        if 'attachment_sha256' not in cols:
            conn.execute("ALTER TABLE whatsapp_demo_jobs ADD COLUMN attachment_sha256 TEXT NOT NULL DEFAULT ''")
        for column in ('daily_day', 'daily_slot', 'daily_mode', 'daily_gateway_content', 'batch_deadline_utc', 'sent_utc', 'direct_recipient', 'queue_day', 'send_group', 'gap_seconds', 'gap_min', 'gap_max', 'retry_of', 'original_utc', 'send_mode', 'next_attempt_utc', 'attempt_started_utc'):
            if column not in cols:
                conn.execute(f"ALTER TABLE whatsapp_demo_jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_whatsapp_daily_slot
                        ON whatsapp_demo_jobs(daily_day, daily_slot) WHERE daily_day <> ''""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_whatsapp_due ON whatsapp_demo_jobs(status, schedule_utc)")


def local_now():
    return datetime.now(KOLKATA).replace(tzinfo=None)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def phone_jid(phone):
    """Normalize India-local mobiles while preserving international E.164 numbers.

    Ten-digit numbers without a prefix are interpreted as India mobile numbers
    only when they start in 6..9. There is no guessing for other 10-digit numbers.
    """
    raw = str(phone or '').strip()
    if not raw or re.search(r'[A-Za-z@]', raw):
        raise ValueError('Enter a phone number, not a WhatsApp JID or contact name')
    if re.search(r'[^0-9+()\s.\-]', raw):
        raise ValueError('Phone contains unsupported characters')
    digits = re.sub(r'[^0-9]', '', raw)
    if digits.startswith('00'):
        digits = digits[2:]
    if len(digits) == 10 and digits[0] in '6789':
        digits = '91' + digits
    if not 10 <= len(digits) <= 15 or digits.startswith('0'):
        raise ValueError('Enter a valid international number, e.g. 919876543210 or +91 9876543210')
    if digits.startswith('91') and (len(digits) != 12 or digits[2] not in '6789'):
        raise ValueError('An Indian mobile number must contain 10 digits after +91')
    return digits + '@s.whatsapp.net'


def message_for(name):
    name = re.sub(r'[\r\n\t]+',' ',str(name)).strip()[:100]
    return (f'Hello {name} Team 👋\n\n'
            'We have created a professional website demo for your business.\n\n'
            f'💻 Complete Website Price: {PRICE}\n\n'
            '📎 Please check your attached website demo.\n\n'
            f'📞 If you are interested, contact us at {CONTACT}.\n'
            f'✉️ Email: {EMAIL}\n\n'
            'Thank you!\nTeam TAZORIXX')


def parse_local_schedule(value):
    """YYYY-MM-DD HH:MM interpreted explicitly in India Standard Time (IST) to UTC."""
    local = datetime.strptime(value.strip(), '%Y-%m-%d %H:%M').replace(tzinfo=KOLKATA)
    instant = local.astimezone(timezone.utc)
    if instant <= datetime.now(timezone.utc):
        raise ValueError('Schedule must be in the future')
    return instant.isoformat(timespec='seconds')


def validated_export(conn, business_id):
    """Validate the CURRENT generated HTML without relying on client review state.

    A changed or missing file is never delivered. No approvals are created or
    changed in the old Client Preview Manager.
    """
    from core import website_quality_report
    b=conn.execute('SELECT * FROM businesses WHERE id=?',(business_id,)).fetchone()
    if not b: raise ValueError('Business not found')
    gen=conn.execute('SELECT * FROM generation WHERE business_id=?',(business_id,)).fetchone()
    if not gen: raise ValueError('Generate this website first')
    quality=website_quality_report(conn,business_id)
    if quality['errors']:
        raise ValueError('Automatic website validation failed: '+ '; '.join(x['detail'] for x in quality['errors'][:4]))
    html=Path(gen['output_path'])
    if not html.is_file() or html.suffix.lower()!='.html':
        raise ValueError('Website HTML is missing')
    digest=hashlib.sha256(html.read_bytes()).hexdigest()
    if digest != gen['html_hash'] or digest != quality['actual_sha256']:
        raise ValueError('Website HTML changed since generation; regenerate before scheduling')
    return b,html,digest

# Backwards-compatible API name for integrations. It no longer checks approval.
approved_export=validated_export

class _LocalAssets(HTMLParser):
    def __init__(self):
        super().__init__(); self.refs=set()
    def handle_starttag(self,tag,attrs):
        for key,val in attrs:
            if key in ('src','href','poster') and val:
                self.refs.add(val)

def _website_assets(html):
    """Return safe local assets required by the page. Remote/data URLs are not packaged."""
    from urllib.parse import unquote, urlsplit
    page=html.read_text(encoding='utf-8')
    parsed=_LocalAssets();parsed.feed(page)
    # Look for static CSS background images as well.
    parsed.refs.update(re.findall(r'url\(\s*[\"\']?([^\)\"\']+)',page,flags=re.I))
    files=[]
    root=html.parent.resolve()
    for ref in parsed.refs:
        ref=ref.strip()
        if not ref or ref.startswith(('#','//','data:','mailto:','tel:','javascript:')):continue
        parts=urlsplit(ref)
        if parts.scheme or parts.netloc:continue
        relative=Path(unquote(parts.path).replace('\\','/'))
        if not str(relative) or str(relative)=='.':continue
        if relative.is_absolute():
            raise ValueError('Website contains a local absolute asset path: '+str(relative))
        resolved=(root/relative).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError('Website contains an unsafe asset path: '+str(relative))
        if not resolved.is_file():
            # Some hrefs represent navigation routes, not required local assets.
            if relative.suffix.lower() in ('.css','.js','.png','.jpg','.jpeg','.webp','.gif','.svg','.ico','.woff','.woff2','.ttf','.json'):
                raise ValueError('Missing local website asset: '+str(relative))
            continue
        files.append((resolved,relative.as_posix()))
    return sorted(set(files),key=lambda x:x[1])

def package_website(business_id, html, digest):
    """Snapshot the current validated website into our app-managed folder and ZIP it."""
    directory=website_store()/hashlib.sha256(str(business_id).encode()).hexdigest()[:20]
    directory.mkdir(parents=True,exist_ok=True)
    if hashlib.sha256(html.read_bytes()).hexdigest()!=digest:
        raise ValueError('Generated HTML changed before packaging')
    assets=_website_assets(html)
    archive=directory/'Website_Demo.zip'
    # Atomic archive replacement: jobs referring to a changed snapshot are blocked by hash.
    fd,temp=tempfile.mkstemp(prefix='.website-',suffix='.zip',dir=directory)
    os.close(fd)
    try:
        with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED) as zipout:
            zipout.write(html,'index.html')
            for asset,relative in assets:
                zipout.write(asset,relative)
        with zipfile.ZipFile(temp) as z:
            if z.testzip() is not None or 'index.html' not in z.namelist():
                raise ValueError('Website package could not be validated')
        os.replace(temp,archive)
    finally:
        Path(temp).unlink(missing_ok=True)
    # Store a plainly accessible standalone HTML copy too.
    target=directory/'index.html'
    staged=directory/'.index.tmp'
    staged.write_bytes(html.read_bytes())
    os.replace(staged,target)
    return archive,hashlib.sha256(archive.read_bytes()).hexdigest()


def prepare_auto_schema(conn):
    """Persist opt-in recipient and scheduling preferences, never the API key."""
    conn.execute("""CREATE TABLE IF NOT EXISTS whatsapp_demo_permissions (
        business_id TEXT PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
        recipient TEXT NOT NULL, session_id TEXT NOT NULL,
        consent_note TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS whatsapp_demo_settings (
        name TEXT PRIMARY KEY, value TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS whatsapp_demo_opt_outs (
        jid TEXT PRIMARY KEY, updated_at TEXT NOT NULL)""")
    conn.commit()


def save_auto_settings(conn, enabled, delay_minutes=10):
    prepare_auto_schema(conn)
    delay=int(delay_minutes)
    if not 1 <= delay <= 1440:
        raise ValueError('Schedule delay must be between 1 and 1440 minutes')
    with conn:
        for name,value in [('enabled','1' if enabled else '0'),('delay_minutes',str(delay))]:
            conn.execute('INSERT INTO whatsapp_demo_settings(name,value) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value',(name,value))


def auto_settings(conn):
    prepare_auto_schema(conn)
    rows=dict(conn.execute('SELECT name,value FROM whatsapp_demo_settings'))
    return rows.get('enabled')=='1',int(rows.get('delay_minutes','10'))


def delivery_paused(conn):
    prepare_auto_schema(conn)
    row = conn.execute("SELECT value FROM whatsapp_demo_settings WHERE name='delivery_paused'").fetchone()
    return bool(row and row[0] == '1')


def set_delivery_paused(conn, paused):
    prepare_auto_schema(conn)
    with conn:
        conn.execute("INSERT INTO whatsapp_demo_settings(name,value) VALUES('delivery_paused',?) "
                     "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                     ('1' if paused else '0',))


def save_permission(conn,business_id,session_id,recipient,note):
    prepare_auto_schema(conn)
    if not conn.execute('SELECT 1 FROM businesses WHERE id=?',(business_id,)).fetchone():
        raise ValueError('Unknown business')
    session_id=session_id.strip()
    if not session_id or '/' in session_id or '\\' in session_id:
        raise ValueError('Enter a valid WA-AKG session ID')
    jid=phone_jid(recipient)
    if len(note.strip())<10:
        raise ValueError('Record how and when the recipient requested/agreed to receive the demo')
    with conn:
        conn.execute('DELETE FROM whatsapp_demo_opt_outs WHERE jid=?', (jid,))
        conn.execute("""INSERT INTO whatsapp_demo_permissions
            (business_id,recipient,session_id,consent_note,enabled,updated_at)
            VALUES (?,?,?,?,1,?) ON CONFLICT(business_id) DO UPDATE SET
            recipient=excluded.recipient,session_id=excluded.session_id,
            consent_note=excluded.consent_note,enabled=1,updated_at=excluded.updated_at""",
            (business_id,recipient,session_id,note.strip(),utc_now()))


def is_opted_out(conn, jid):
    return conn.execute('SELECT 1 FROM whatsapp_demo_opt_outs WHERE jid=?', (jid,)).fetchone() is not None


def record_opt_out(conn, jid, businesses):
    stamp = utc_now()
    conn.execute('INSERT OR REPLACE INTO whatsapp_demo_opt_outs VALUES (?,?)', (jid, stamp))
    for bid in businesses:
        conn.execute('UPDATE whatsapp_demo_permissions SET enabled=0,updated_at=? WHERE business_id=?',
                     (stamp, bid))
    jobs = conn.execute("SELECT id,recipient FROM whatsapp_demo_jobs WHERE status='scheduled'").fetchall()
    for job in jobs:
        if phone_jid(job['recipient']) == jid:
            conn.execute("UPDATE whatsapp_demo_jobs SET status='cancelled',error='Recipient opted out',updated_at=? WHERE id=?",
                         (stamp, job['id']))
    return True


def disable_permission(conn,business_id):
    prepare_schema(conn)
    prepare_auto_schema(conn)
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT recipient FROM whatsapp_demo_permissions WHERE business_id=?', (business_id,)).fetchone()
        if row:
            jid = phone_jid(row['recipient'])
            businesses = [p['business_id'] for p in conn.execute('SELECT * FROM whatsapp_demo_permissions')
                          if phone_jid(p['recipient']) == jid]
            record_opt_out(conn, jid, businesses)
        conn.execute("UPDATE whatsapp_demo_jobs SET status='cancelled',updated_at=? WHERE business_id=? AND status='scheduled'",
                     (utc_now(), business_id))


def auto_schedule_eligible(conn):
    """Schedule only already-authorized business recipients with validated HTML."""
    prepare_schema(conn);prepare_auto_schema(conn)
    enabled,delay=auto_settings(conn)
    counts={'scheduled':0,'skipped':0,'errors':[]}
    if not enabled or conn.execute("SELECT 1 FROM whatsapp_demo_settings WHERE name='daily_enabled' AND value='1'").fetchone():
        return counts
    from datetime import timedelta
    rows=conn.execute("""SELECT p.* FROM whatsapp_demo_permissions p
        JOIN generation g ON g.business_id=p.business_id WHERE p.enabled=1
        ORDER BY p.business_id""").fetchall()
    for row in rows:
        try:
            # Background automation never starts a repeat delivery merely because
            # a website was generated again. Repeat sends require an explicit UI action.
            previous=conn.execute("SELECT id FROM whatsapp_demo_jobs WHERE business_id=? AND recipient=? AND status IN ('scheduled','sending','sent','uncertain') LIMIT 1",
                                  (row['business_id'],row['recipient'])).fetchone()
            if previous:
                counts['skipped']+=1
                continue
            from datetime import datetime
            when=(local_now()+timedelta(minutes=delay+counts['scheduled'])).strftime('%Y-%m-%d %H:%M')
            schedule_job(conn,row['business_id'],row['session_id'],row['recipient'],when,row['consent_note'],legacy_auto=True)
            counts['scheduled']+=1
        except ValueError as exc:
            if 'pending or uncertain' in str(exc) or 'already exists' in str(exc):counts['skipped']+=1
            else:counts['errors'].append(f"{row['business_id']}: {exc}")
    return counts


def schedule_job(conn, business_id, session_id, recipient, when_local, consent_note, *, explicit_repeat=False, legacy_auto=False):
    prepare_schema(conn)
    prepare_auto_schema(conn)
    session_id=session_id.strip()
    if not session_id or '/' in session_id or '\\' in session_id:
        raise ValueError('Select a valid WA-AKG session ID')
    phone_jid(recipient)
    note=consent_note.strip()
    if len(note)<10:
        raise ValueError('Record when and how this recipient agreed to receive the demo (at least 10 characters)')
    b,html,digest=approved_export(conn,business_id)
    scheduled=parse_local_schedule(when_local)
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if legacy_auto and conn.execute("SELECT 1 FROM whatsapp_demo_settings WHERE name='daily_enabled' AND value='1'").fetchone():
            raise ValueError('Daily delivery already exists for automatic selection')
        if is_opted_out(conn, phone_jid(recipient)):
            raise ValueError('Recipient opted out. Record fresh authorization before scheduling.')
        existing=conn.execute("SELECT id FROM whatsapp_demo_jobs WHERE business_id=? AND recipient=? AND status IN ('scheduled','sending','uncertain')",(business_id,recipient)).fetchone()
        if existing and not explicit_repeat:
            raise ValueError('Another pending or uncertain delivery exists for this business and recipient. Check its status before scheduling again')
        return insert_job(conn, business_id, b, html, digest, session_id, recipient, scheduled, note)


def recipient_block(conn, recipient):
    jid = phone_jid(recipient)
    if is_opted_out(conn, jid):
        return 'Recipient opted out'
    return ''


def saved_recipient(conn, business):
    row = conn.execute('SELECT value FROM whatsapp_demo_settings WHERE name=?',
                       ('recipient:' + business['id'],)).fetchone()
    if row:
        return row[0]
    old = conn.execute('SELECT recipient FROM whatsapp_demo_permissions WHERE business_id=?',
                       (business['id'],)).fetchone()
    return old[0] if old else (business['whatsapp'] or business['phone'] or '')


def save_recipient_number(conn, business_id, recipient):
    phone_jid(recipient)
    with conn:
        conn.execute("""INSERT INTO whatsapp_demo_settings VALUES (?,?)
            ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
            ('recipient:' + business_id, recipient))


def job_can_send(conn, job):
    if job['direct_recipient']:
        return not recipient_block(conn, job['recipient'])
    return permission_current(conn, job)


def pending_for_recipient(conn, business_id, recipient):
    jid = phone_jid(recipient)
    return [job for job in conn.execute("""SELECT * FROM whatsapp_demo_jobs
        WHERE business_id=? AND status IN ('scheduled','sending','uncertain')""",
        (business_id,)) if phone_jid(job['recipient']) == jid]


def queue_immediate_job(conn, business_id, session, recipient, *, direct=False, when=None, repeat=False, retry_of=None):
    """Bring an existing individual job forward, or enqueue one new due job."""
    business, path, digest = validated_export(conn, business_id)
    if not session or '/' in session or chr(92) in session:
        raise ValueError('Select a valid WhatsApp account.')
    stamp = utc_now()
    scheduled = parse_local_schedule(when) if when else stamp
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        permission = conn.execute("""SELECT * FROM whatsapp_demo_permissions
            WHERE business_id=? AND recipient=? AND session_id=? AND enabled=1""",
            (business_id, recipient, session)).fetchone()
        if direct and recipient_block(conn, recipient):
            raise ValueError('This recipient opted out.')
        if not direct and (not permission or not permission_current(conn, permission)):
            raise ValueError('Record authorization for this recipient and WhatsApp account first.')
        pending = pending_for_recipient(conn, business_id, recipient)
        if pending and not repeat:
            if when:
                raise ValueError('A delivery is already pending or uncertain. Check Delivery History.')
            job = pending[0]
            if (len(pending) != 1 or job['status'] != 'scheduled' or job['daily_day']
                    or job['session_id'] != session or job['recipient'] != recipient):
                raise ValueError('Check the existing delivery in History before sending another copy.')
            if job['html_sha256'] != digest or job['html_path'] != str(path):
                raise ValueError('The website changed. Cancel the old job in History before sending this version.')
            conn.execute("""UPDATE whatsapp_demo_jobs SET schedule_utc=?,updated_at=?,error='',
                batch_deadline_utc='',queue_day='',send_group='',gap_seconds='',direct_recipient=? WHERE id=?""", (stamp, stamp, '1' if direct else job['direct_recipient'], job['id']))
            return job['id']
        if when and conn.execute("""SELECT 1 FROM whatsapp_demo_jobs
            WHERE session_id=? AND schedule_utc=? AND status IN ('scheduled','sending','uncertain')""",
            (session,scheduled)).fetchone():
            raise ValueError('Another job already uses that time for this WhatsApp account.')
        job_id=insert_job(conn, business_id, business, path, digest, session, recipient,
                          scheduled, '' if direct else permission['consent_note'], direct=direct)
        conn.execute('UPDATE whatsapp_demo_jobs SET retry_of=?,send_mode=? WHERE id=?',
                     (str(retry_of or ''), 'Scheduled' if when else 'Immediate', job_id))
        return job_id


def batch_times(day, start, gap, count):
    first = datetime.strptime(f'{day.strip()} {start.strip()}', '%Y-%m-%d %H:%M')
    if not gap.strip().isdecimal() or not 1 <= int(gap) <= 1440:
        raise ValueError('Enter a whole-minute gap from 1 to 1440.')
    step = timedelta(minutes=int(gap))
    return first, step, first + step * max(count - 1, 0)


@dataclass(frozen=True)
class BatchItem:
    business_id: str
    name: str
    recipient: str
    when: str = ''
    reason: str = ''
    path: str = ''
    digest: str = ''
    note: str = ''


def batch_recipient(conn, business, session, *, direct=False, repeat=False):
    if direct:
        recipient = saved_recipient(conn, business)
        try:
            reason = recipient_block(conn, recipient)
        except ValueError:
            reason = 'Enter a valid WhatsApp number with country code'
        if not reason and not repeat and pending_for_recipient(conn,business['id'],recipient):
            reason = 'An existing job is pending or uncertain'
        return recipient, reason, ''

    bid = business['id']
    permission = conn.execute("SELECT * FROM whatsapp_demo_permissions WHERE business_id=?", (bid,)).fetchone()
    recipient = permission['recipient'] if permission else business['whatsapp'] or business['phone']
    if not permission:
        return recipient, 'Record recipient authorization', ''
    jid = phone_jid(recipient)
    if not permission['enabled'] or is_opted_out(conn, jid):
        return recipient, 'Recipient opted out', ''
    if permission['session_id'] != session:
        return recipient, 'Authorization belongs to another WhatsApp account', ''
    if conn.execute("""SELECT 1 FROM whatsapp_demo_jobs WHERE business_id=?
            AND status IN ('scheduled','sending','uncertain')""", (bid,)).fetchone():
        return recipient, 'An existing job is pending or uncertain', ''
    return recipient, '', permission['consent_note']


def batch_preview(conn, businesses, session, day, start, end, gap, *, direct=False, per_day=None, check_slots=True, repeat=False):
    if not businesses:
        raise ValueError('Select at least one website.')
    if len({business['id'] for business in businesses})!=len(businesses):
        raise ValueError('Select each website once.')
    if not session or '/' in session or chr(92) in session:
        raise ValueError('Select a WhatsApp account.')
    start_at, step, calculated_end = batch_times(day, start, gap, len(businesses))
    end_at = (datetime.strptime(f'{day.strip()} {end.strip()}', '%Y-%m-%d %H:%M')
              if end is not None else calculated_end)
    parse_local_schedule(start_at.strftime('%Y-%m-%d %H:%M'))
    if end_at < start_at:
        raise ValueError('End time must be on or after start time.')
    if per_day is not None and (per_day < 1 or per_day > 1000
            or (start_at + step * (per_day - 1)).date() != start_at.date()):
        raise ValueError('Choose a daily count and gap that fit on the same day as the start time.')
    slot = start_at
    queue_index = 0
    reservations = {}
    if per_day:
        reservations = dict(conn.execute("""SELECT queue_day, COUNT(*) FROM whatsapp_demo_jobs
            WHERE session_id=? AND queue_day>=? AND status IN ('scheduled','sending','sent','uncertain')
            GROUP BY queue_day""", (session, start_at.date().isoformat())))
    occupied = {row[0] for row in conn.execute("""SELECT schedule_utc FROM whatsapp_demo_jobs
        WHERE session_id=? AND status IN ('scheduled','sending','uncertain')""", (session,))}
    items = []
    for business in businesses:
        bid, name = business['id'], business['name']
        recipient, reason, note = batch_recipient(conn, business, session, direct=direct, repeat=repeat)
        if per_day and not reason and conn.execute(
                "SELECT 1 FROM whatsapp_demo_jobs WHERE business_id=? AND status='sent'", (bid,)).fetchone():
            reason = 'Already sent'
        path = digest = when = ''
        if not reason:
            try:
                phone_jid(recipient)
                if not per_day and slot > end_at:
                    raise ValueError('Outside sending window')
                _, export_path, digest = validated_export(conn, bid)
                if per_day:
                    while True:
                        days, offset = divmod(queue_index, per_day)
                        slot = start_at + timedelta(days=days) + step * offset
                        queue_index += 1
                        queue_date = slot.date().isoformat()
                        slot_utc = slot.replace(tzinfo=KOLKATA).astimezone(timezone.utc).isoformat(timespec='seconds')
                        if reservations.get(queue_date, 0) < per_day and slot_utc not in occupied:
                            reservations[queue_date] = reservations.get(queue_date, 0) + 1
                            break
                local = slot.strftime('%Y-%m-%d %H:%M')
                when_utc = parse_local_schedule(local)
                if check_slots and when_utc in occupied:
                    raise ValueError('Another job occupies this time for this account')
                path, when = str(export_path), local
            except (ValueError, OSError) as exc:
                reason = str(exc)
            slot += step
        items.append(BatchItem(bid, name, recipient, when, reason, path, digest, note))
    return tuple(items)


def schedule_batch(conn, businesses, session, day, start, end, gap, preview, *, direct=False, per_day=None, repeat=False):
    # Recheck under a write lock so a changed file, permission or queue cannot
    # turn a confirmed preview into a different batch or a partial insert.
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        current = batch_preview(conn, businesses, session, day, start, end, gap, direct=direct, per_day=per_day, repeat=repeat)
        if current != preview or (not per_day and any(item.reason for item in current)):
            raise ValueError('The schedule changed or has conflicts. Generate a fresh preview.')
        deadline = ''
        if end is not None:
            cutoff = datetime.strptime(f'{day.strip()} {end.strip()}', '%Y-%m-%d %H:%M') + timedelta(minutes=1)
            deadline = cutoff.replace(tzinfo=KOLKATA).astimezone(timezone.utc).isoformat(timespec='seconds')
        ids = []
        for business, item in zip(businesses, current):
            if item.reason:
                continue
            job = insert_job(conn, item.business_id, business, item.path, item.digest, session,
                             item.recipient, parse_local_schedule(item.when), item.note, direct=direct)
            queue_day = item.when[:10] if per_day else ''
            cutoff = deadline
            if queue_day:
                next_day = datetime.strptime(queue_day, '%Y-%m-%d') + timedelta(days=1)
                cutoff = next_day.replace(tzinfo=KOLKATA).astimezone(timezone.utc).isoformat(timespec='seconds')
            conn.execute('UPDATE whatsapp_demo_jobs SET batch_deadline_utc=?,queue_day=? WHERE id=?',
                         (cutoff, queue_day, job))
            ids.append(job)
        return ids


def immediate_bounds(minimum, maximum):
    minimum, maximum = minimum.strip(), maximum.strip()
    if not minimum.isdecimal() or not maximum.isdecimal() or not 0 <= int(minimum) <= int(maximum) <= 1440:
        raise ValueError('Enter whole minutes with 0 ≤ minimum ≤ maximum ≤ 1440.')
    # Zero means no additional scheduled gap. Sending remains strictly sequential;
    # the worker, network and WhatsApp connection still impose their own pacing.
    return int(minimum), int(maximum)


def stop_immediate_batch(conn, job_id):
    with conn:
        job=conn.execute('SELECT send_group FROM whatsapp_demo_jobs WHERE id=?',(job_id,)).fetchone()
        if not job or not job[0]:
            return 0
        return conn.execute("""UPDATE whatsapp_demo_jobs SET status='cancelled',
            error='Stopped by operator',updated_at=? WHERE send_group=? AND status='scheduled'""",
            (utc_now(),job[0])).rowcount


def queue_send_all(conn, businesses, session, gap, *, repeat=False, maximum_gap=None):
    """Persist the immediate batch before attempting any network delivery."""
    minimum, maximum = immediate_bounds(gap, maximum_gap if maximum_gap is not None else gap)
    start = local_now() + timedelta(minutes=1)
    args = (businesses, session, start.strftime('%Y-%m-%d'), start.strftime('%H:%M'),
            None, str(max(1, minimum)))
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        preview = batch_preview(conn, *args, direct=True, check_slots=False, repeat=repeat)
        blocked = [f'{item.name}: {item.reason}' for item in preview if item.reason]
        if blocked:
            raise ValueError('Review the selected websites:\n' + '\n'.join(blocked))
        group = secrets.token_hex(12)
        when = datetime.now(timezone.utc)
        ids = []
        for index, (business, item) in enumerate(zip(businesses, preview)):
            seconds = random.randint(minimum, maximum) * 60 if index else 0
            when += timedelta(seconds=seconds)
            job = insert_job(conn, item.business_id, business, item.path, item.digest, session,
                             item.recipient, when.isoformat(timespec='seconds'), '', direct=True)
            conn.execute("""UPDATE whatsapp_demo_jobs SET send_group=?,gap_seconds=?,
                gap_min=?,gap_max=?,send_mode='Immediate' WHERE id=?""",
                (group,str(seconds),str(minimum),str(maximum),job))
            ids.append(job)
        return ids


def attempt_in_progress(job, now):
    return (job['status']=='uncertain' and job['error']==ATTEMPT_IN_PROGRESS
            and datetime.fromisoformat(job['updated_at']) + timedelta(minutes=3) > now)


def immediate_next_time(conn, group):
    waiting=conn.execute("""SELECT * FROM whatsapp_demo_jobs WHERE send_group=? AND status='scheduled'
                           ORDER BY id LIMIT 1""",(group,)).fetchone()
    if not waiting:
        return None
    planned=datetime.fromisoformat(waiting['schedule_utc'])
    previous=conn.execute("""SELECT updated_at,status FROM whatsapp_demo_jobs WHERE send_group=? AND id<?
        AND status<>'cancelled' ORDER BY id DESC LIMIT 1""",(group,waiting['id'])).fetchone()
    if previous and previous['status']!='scheduled':
        planned=max(planned,datetime.fromisoformat(previous['updated_at']) +
                    timedelta(seconds=int(waiting['gap_seconds'])))
    return planned


def group_ready(conn, job):
    if not job['send_group']:
        return True
    previous = conn.execute("""SELECT * FROM whatsapp_demo_jobs WHERE send_group=? AND id<?
                            AND status<>'cancelled' ORDER BY id DESC LIMIT 1""",
                            (job['send_group'], job['id'])).fetchone()
    if not previous:
        return True
    now = datetime.now(timezone.utc)
    if previous['status'] == 'scheduled' or attempt_in_progress(previous, now):
        return False
    return datetime.fromisoformat(previous['updated_at']) + timedelta(seconds=int(job['gap_seconds'])) <= now


def insert_job(conn, business_id, business, html, digest, session, recipient, scheduled, note,
               daily_day='', daily_slot='', daily_mode='', *, direct=False):
    stamp = utc_now()
    cursor = conn.execute("""INSERT INTO whatsapp_demo_jobs
        (business_id,session_id,recipient,html_path,html_sha256,message,consent_note,
         approved_at,schedule_utc,created_at,updated_at,attachment_path,attachment_sha256,
         daily_day,daily_slot,daily_mode,direct_recipient)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (business_id,session,recipient,str(html),digest,message_for(business['name']),
         note,stamp,scheduled,stamp,stamp,str(html),digest,daily_day,daily_slot,daily_mode,'1' if direct else ''))
    return cursor.lastrowid


def list_jobs(conn):
    prepare_schema(conn)
    return conn.execute('''SELECT j.*, b.name FROM whatsapp_demo_jobs j
        JOIN businesses b ON j.business_id=b.id ORDER BY j.id DESC''').fetchall()


HISTORY_STATES = ('Scheduled', 'Ready', 'Blocked', 'WA-AKG scheduled', 'Sending',
                  'Sent', 'Failed', 'Uncertain', 'Cancelled')


def history_rows(conn):
    prepare_auto_schema(conn)
    paused = delivery_paused(conn)
    now = utc_now()
    for job in list_jobs(conn):
        state = job['status'].capitalize()
        detail = job['error']
        if state == 'Scheduled':
            if not job_can_send(conn, job):
                state, detail = 'Blocked', 'Recipient authorization is missing, changed, or withdrawn.'
            elif paused:
                state, detail = 'Blocked', 'Sending is paused.'
            elif detail.startswith('WA-AKG unavailable:'):
                state = 'Blocked'
            elif job['next_attempt_utc'] and job['next_attempt_utc'] > now:
                state, detail = 'Blocked', (detail or 'Gateway reconnecting') + ' · retry is queued'
            elif job['schedule_utc'] <= now:
                state, detail = 'Ready', 'Due. Keep the sender running and WhatsApp connected.'
        elif attempt_in_progress(job, datetime.fromisoformat(now)):
            state, detail = 'Sending', 'The sender is submitting the demo to WhatsApp.'
        elif state == 'Sending' and job['daily_mode'].startswith('native-'):
            state = 'WA-AKG scheduled'
        yield job, state, detail


def reschedule_job(conn, job_id, when_local, *, expected_utc):
    scheduled = parse_local_schedule(when_local)
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        job=conn.execute("SELECT * FROM whatsapp_demo_jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job['status']!='scheduled' or job['schedule_utc']!=expected_utc:
            raise ValueError('This job changed. Refresh Delivery History.')
        if recipient_block(conn, job['recipient']):
            raise ValueError('This recipient opted out.')
        if conn.execute("""SELECT 1 FROM whatsapp_demo_jobs WHERE id<>? AND session_id=?
            AND schedule_utc=? AND status IN ('scheduled','sending','uncertain')""",
            (job_id,job['session_id'],scheduled)).fetchone():
            raise ValueError('Another job already uses that time for this WhatsApp account.')
        conn.execute("""UPDATE whatsapp_demo_jobs SET schedule_utc=?,updated_at=?,error='',
            batch_deadline_utc='',queue_day='',daily_day='',daily_slot='',daily_mode='',
            send_group='',gap_seconds='',send_mode='Scheduled',
            original_utc=CASE WHEN original_utc='' THEN schedule_utc ELSE original_utc END,
            direct_recipient='1' WHERE id=?""",
            (scheduled,utc_now(),job_id))


def cancel_job(conn, job_id):
    with conn:
        cursor=conn.execute("UPDATE whatsapp_demo_jobs SET status='cancelled',error='Cancelled from Delivery History',updated_at=? WHERE id=? AND status='scheduled'",(utc_now(),job_id))
        if not cursor.rowcount:
            cursor=conn.execute("UPDATE whatsapp_demo_jobs SET error='Cancellation requested' WHERE id=? AND daily_mode LIKE 'native-%' AND status='sending'", (job_id,))
    if not cursor.rowcount: raise ValueError('Only pending messages can be cancelled')


def check_base_url(base_url):
    parsed=urlsplit(base_url.strip())
    if parsed.scheme!='http' or parsed.hostname not in ('localhost','127.0.0.1') or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('For safety, Version 1 accepts only http://localhost:3000 or another localhost HTTP port')
    return f'http://{parsed.hostname}:{parsed.port or 80}'


def _fetch(url, key, method='GET', payload=None):
    if not key or any(ord(c)<32 or ord(c)==127 for c in key):
        raise ValueError('API key is missing or malformed')
    headers={'X-API-Key':key}
    body=None
    if payload is not None:
        headers['Content-Type']='application/json'
        body=json.dumps(payload,ensure_ascii=False).encode('utf-8')
    request=Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request,timeout=15) as r:
            body = r.read(1048576)
            data = json.loads(body.decode('utf-8')) if body else None
    except HTTPError as ex:
        # HTTP errors include a useful JSON message; never print raw HTML or API keys.
        try:
            parsed = json.loads(ex.read(4096).decode('utf-8', errors='replace'))
            detail = str(parsed.get('message') or parsed.get('error') or '')[:200] if isinstance(parsed, dict) else ''
        except (ValueError, UnicodeError):
            detail = ''
        if ex.code == 503 and detail == 'WA_GATEWAY_SESSION_NOT_READY':
            raise GatewayNotReady('WhatsApp session is reconnecting; send deferred') from ex
        raise RuntimeError(f'WA-AKG HTTP {ex.code}' + (f': {detail}' if detail else '')) from ex
    except (URLError,TimeoutError) as ex:
        raise ConnectionError('WA-AKG connection failed or timed out') from ex
    if isinstance(data,dict) and data.get('status') is False:
        raise RuntimeError('WA-AKG rejected the request: '+str(data.get('message') or data.get('error') or 'unknown error'))
    return data


def sessions(base_url,api_key):
    return _fetch(check_base_url(base_url)+'/api/sessions',api_key)


def _multipart_document(base_url,api_key,session,jid,attachment_path, *, caption='TAZORIXX website demo'):
    # WA-AKG /api/messages/[sessionId]/[jid]/media accepts multipart: file,type,caption.
    path=Path(attachment_path)
    if path.suffix.lower() not in ('.html','.zip') or not path.is_file():
        raise ValueError('Website must be a valid HTML or ZIP attachment')
    data=path.read_bytes()
    boundary='----TAZORIXX'+secrets.token_hex(12)
    def field(name,value):
        return (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode('utf-8')
    parts=[field('type','document'),field('caption',caption)]
    content_type='text/html' if path.suffix.lower()=='.html' else 'application/zip'
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\nContent-Type: {content_type}\r\n\r\n').encode('utf-8')+data+b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode('utf-8'))
    body=b''.join(parts)
    parsed=urlsplit(check_base_url(base_url))
    urlpath=f'/api/messages/{quote(session,safe="")}/{quote(jid,safe="")}/media'
    connection=http.client.HTTPConnection(parsed.hostname,parsed.port,timeout=25)
    try:
        connection.request('POST',urlpath,body=body,headers={
            'X-API-Key':api_key,'Content-Type':'multipart/form-data; boundary='+boundary,
            'Content-Length':str(len(body))})
        reply=connection.getresponse()
        raw=reply.read(1024*1024)
        if reply.status>=400:
            try:
                error = json.loads(raw.decode('utf-8', errors='replace'))
                detail = str(error.get('message') or error.get('error') or '')[:200] if isinstance(error,dict) else ''
            except ValueError:
                detail = ''
            if reply.status == 503 and detail == 'WA_GATEWAY_SESSION_NOT_READY':
                raise GatewayNotReady('WhatsApp session is reconnecting; send deferred')
            raise RuntimeError(f'WA-AKG media HTTP {reply.status}' + (f': {detail}' if detail else ''))
        result=json.loads(raw.decode('utf-8'))
        if not isinstance(result, dict) or result.get('status') is not True:
            raise RuntimeError('WA-AKG media request rejected or returned an invalid result')
        return result
    finally:
        connection.close()


def process_due_one(conn,base_url,api_key, *, daily_id=None, require_permission=False, job_id=None, immediate=False, stopped=None):
    """One eligible job per call. Mark 'uncertain' before network to prevent blind resends."""
    if immediate and (job_id is None or not require_permission or daily_id is not None):
        raise ValueError('Immediate sending requires one authorized individual job.')
    prepare_schema(conn)
    prepare_auto_schema(conn)
    job=conn.execute("""SELECT * FROM whatsapp_demo_jobs WHERE status='scheduled' AND schedule_utc<=?
        AND (next_attempt_utc='' OR next_attempt_utc<=?)
        AND ((? IS NULL AND daily_day='') OR id=?) AND (? IS NULL OR id=?) ORDER BY schedule_utc,id LIMIT 1""",
        (utc_now(),utc_now(),daily_id,daily_id,job_id,job_id)).fetchone()
    if job is None: return None
    job_id=job['id']
    # Revalidate content and exact scheduled digest before any network send.
    try:
        b,html,digest=approved_export(conn,job['business_id'])
        if str(html)!=job['html_path'] or digest!=job['html_sha256']:
            raise ValueError('Generated website differs from scheduled attachment')
        # Check the saved attachment (HTML for new jobs, ZIP for older jobs).
        if job['attachment_path']:
            attachment=Path(job['attachment_path'])
            if not attachment.is_file() or hashlib.sha256(attachment.read_bytes()).hexdigest()!=job['attachment_sha256']:
                raise ValueError('Website attachment missing or changed after scheduling')
        else:
            # Upgrade previously scheduled MVP20 jobs without resending uncertain jobs.
            attachment,attachment_digest=package_website(job['business_id'],html,digest)
            with conn:conn.execute('UPDATE whatsapp_demo_jobs SET attachment_path=?,attachment_sha256=? WHERE id=?',(str(attachment),attachment_digest,job_id))
        jid=phone_jid(job['recipient'])
        url=check_base_url(base_url)
        if not api_key: raise ValueError('API key missing: enter it again in WhatsApp Automation')
    except Exception as exc:
        with conn:conn.execute("UPDATE whatsapp_demo_jobs SET status='failed',error=?,updated_at=? WHERE id=? AND status='scheduled' AND schedule_utc=?",(str(exc)[:400],utc_now(),job_id,job['schedule_utc']))
        return (job_id,'failed',str(exc))
    # Deliver the HTML first. Never send a text-only price offer if the file fails.
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if daily_id is not None or require_permission:
            flags = dict(conn.execute("SELECT name,value FROM whatsapp_demo_settings WHERE name IN ('delivery_paused','daily_enabled')"))
            if (flags.get('delivery_paused') == '1' and not immediate) or (daily_id is not None and flags.get('daily_enabled') != '1'):
                return None
        if is_opted_out(conn, jid) or ((daily_id is not None or require_permission) and not job_can_send(conn, job)):
            conn.execute("UPDATE whatsapp_demo_jobs SET status='cancelled',error='Authorization withdrawn' WHERE id=? AND status='scheduled'", (job_id,))
            return (job_id, 'cancelled', 'Authorization withdrawn')
        if job['batch_deadline_utc'] and utc_now() >= job['batch_deadline_utc']:
            conn.execute("""UPDATE whatsapp_demo_jobs SET status='failed',error='Batch sending window expired',
                updated_at=? WHERE id=? AND status='scheduled' AND schedule_utc=?""",
                (utc_now(), job_id, job['schedule_utc']))
            return (job_id, 'failed', 'Batch sending window expired')
        if stopped and stopped():
            return None
        if not group_ready(conn, job):
            return None
        claimed = conn.execute("UPDATE whatsapp_demo_jobs SET status='uncertain',error='Attempt begun; verify chat before retrying',attempt_started_utc=?,updated_at=? WHERE id=? AND status='scheduled' AND schedule_utc=?",(utc_now(),utc_now(),job_id,job['schedule_utc']))
    if not claimed.rowcount:
        return None  # Claimed or cancelled by another sender; never duplicate.
    try:
        # Document + complete offer/call-to-action as ONE WhatsApp message.
        # This removes the old partial-success window between file and text requests.
        media=_multipart_document(url,api_key,job['session_id'],jid,attachment,
                                  caption=job['message'])
        result=media.get('data',{}) if isinstance(media,dict) else {}
        ref=str(result)[:250]
        stamp=utc_now()
        with conn:conn.execute("UPDATE whatsapp_demo_jobs SET status='sent',error='',next_attempt_utc='',api_message_ref=?,updated_at=?,sent_utc=? WHERE id=?",(ref,stamp,stamp,job_id))
        return (job_id,'sent','Gateway accepted document and caption; verify receipt in WhatsApp')
    except GatewayNotReady as exc:
        # The patched gateway returns this error BEFORE invoking Baileys send.
        # No delivery attempt occurred; keep the same job pending for later.
        with conn:
            conn.execute("""UPDATE whatsapp_demo_jobs SET status='scheduled',error=?,next_attempt_utc=?,updated_at=?
                 WHERE id=? AND status='uncertain'""",
                 (str(exc)[:400],(datetime.now(timezone.utc)+timedelta(seconds=15)).isoformat(timespec='seconds'),utc_now(),job_id))
        return (job_id,'scheduled',str(exc))
    except Exception as exc:
        # A transport failure might occur AFTER acceptance. Never blindly resend.
        with conn:conn.execute("UPDATE whatsapp_demo_jobs SET status='uncertain',error=?,updated_at=? WHERE id=?",(str(exc)[:400],utc_now(),job_id))
        return (job_id,'uncertain',str(exc))


def permission_current(conn, job):
    return not is_opted_out(conn, phone_jid(job['recipient'])) and conn.execute("""SELECT 1 FROM whatsapp_demo_permissions
        WHERE business_id=? AND recipient=? AND session_id=? AND enabled=1""",
        (job['business_id'], job['recipient'], job['session_id'])).fetchone() is not None


def session_available(resp, requested):
    """Fail closed: only the requested session with an explicit live CONNECTED state.

    New gateway exposes runtimeStatus; older gateway data can contain a stale
    database CONNECTED flag, so its actual socket readiness must be verified
    by the gateway patch before live use.
    """
    data = resp.get('data', resp.get('sessions', [])) if isinstance(resp, dict) else resp
    if isinstance(data, dict):
        data = data.get('sessions', [])
    if not isinstance(data, list):
        return False
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if requested not in (entry.get('sessionId'), entry.get('id')):
            continue
        state = str(entry.get('runtimeStatus', entry.get('status', entry.get('state', '')))).upper()
        return state == 'CONNECTED'
    return False


def process_connected_due(conn, url, key, *, stopped=None):
    """Record gateway holds and let connected accounts continue independently."""
    if (stopped and stopped()) or delivery_paused(conn):
        return None
    stamp = utc_now()
    with conn:
        conn.execute("""UPDATE whatsapp_demo_jobs SET status='failed',error='Batch sending window expired',
            updated_at=? WHERE status='scheduled' AND batch_deadline_utc<>'' AND batch_deadline_utc<=?""",
            (stamp, stamp))
    rows = conn.execute("""SELECT * FROM whatsapp_demo_jobs WHERE status='scheduled'
                           AND daily_day='' AND schedule_utc<=?
                           AND (next_attempt_utc='' OR next_attempt_utc<=?)
                           ORDER BY (send_group=''),schedule_utc,id""",
                        (utc_now(),utc_now())).fetchall()
    if not rows:
        return None

    def mark(row, status, detail):
        retry_after = ((datetime.now(timezone.utc)+timedelta(seconds=10)).isoformat(timespec='seconds')
                       if status=='scheduled' else '')
        with conn:
            conn.execute("""UPDATE whatsapp_demo_jobs SET status=?,error=?,next_attempt_utc=?,updated_at=?
                WHERE id=? AND status='scheduled' AND schedule_utc=?""",
                (status, detail, retry_after, utc_now(), row['id'], row['schedule_utc']))

    available = None
    for row in rows:
        if not job_can_send(conn, row):
            mark(row, 'cancelled', 'Recipient no longer authorized for this account')
            continue
        if not group_ready(conn,row):
            continue
        if available is None:
            try:
                available = sessions(url, key)
            except (OSError, RuntimeError, ValueError) as exc:
                for pending in rows:
                    if job_can_send(conn, pending):
                        mark(pending, 'scheduled', 'WA-AKG unavailable: ' + str(exc)[:200])
                return None
        if session_available(available, row['session_id']):
            return process_due_one(conn, url, key, require_permission=True, job_id=row['id'], stopped=stopped)
        mark(row, 'scheduled', 'WA-AKG unavailable: WhatsApp account is disconnected or missing.')
    return None
