"""Persistent daily delivery for explicitly authorized, validated website demos."""
from datetime import datetime, time, timedelta, timezone
import hashlib
from pathlib import Path
import re
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

import whatsapp_sender as sender

DEFAULTS = dict(enabled='0', limit='50', start='18:00', end='22:00',
                weekends='1', mode='local', media_base='', html_verified='0')
MODES = ('local', 'native-link', 'native-html')


def prepare_daily(conn):
    sender.prepare_schema(conn)
    sender.prepare_auto_schema(conn)
    with conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS demo_daily_skips (
            day TEXT, business_id TEXT, reason TEXT, PRIMARY KEY(day,business_id))""")


def daily_settings(conn):
    sender.prepare_auto_schema(conn)
    saved = dict(conn.execute("SELECT substr(name,7),value FROM whatsapp_demo_settings WHERE name LIKE 'daily_%'"))
    return {**DEFAULTS, **saved}


def save_daily(conn, values):
    settings = {key: str(values[key]).strip() for key in DEFAULTS}
    start, end = window(settings, sender.local_now())
    limit = int(settings['limit'])
    if not 1 <= limit <= 1000 or (end - start).total_seconds() < 60 * limit:
        raise ValueError('Choose 1–1000 deliveries with at least one minute per delivery in the window.')
    if settings['mode'] not in MODES:
        raise ValueError('Choose a supported delivery mode.')
    if settings['mode'] != 'local':
        parts = urlsplit(settings['media_base'])
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.query or parts.fragment:
            raise ValueError('Native scheduling requires an HTTPS directory for the exact demo files.')
    if settings['mode'] == 'native-html' and settings['html_verified'] != '1':
        raise ValueError('Verify a scheduled HTML attachment on a test account before enabling this mode.')
    sender.prepare_auto_schema(conn)
    with conn:
        conn.executemany("""INSERT INTO whatsapp_demo_settings VALUES (?,?)
                            ON CONFLICT(name) DO UPDATE SET value=excluded.value""",
                         [('daily_' + key, value) for key, value in settings.items()])


def window(settings, now):
    if any(not re.fullmatch(r'\d{2}:\d{2}', settings[key]) for key in ('start', 'end')):
        raise ValueError('Enter both times as HH:MM in 24-hour format.')
    start = datetime.combine(now.date(), time.fromisoformat(settings['start']))
    end = datetime.combine(now.date(), time.fromisoformat(settings['end']))
    if end <= start:
        raise ValueError('End time must be after start time on the same day.')
    return start, end


def utc(value):
    return value.replace(tzinfo=sender.KOLKATA).astimezone(timezone.utc).isoformat(timespec='seconds')


def update_job(conn, job, status, detail='', ref=None):
    stamp=sender.utc_now()
    with conn:
        conn.execute("""UPDATE whatsapp_demo_jobs SET status=?,error=?,updated_at=?,
                        sent_utc=CASE WHEN ?='sent' AND sent_utc='' THEN ? ELSE sent_utc END,
                        api_message_ref=COALESCE(?,api_message_ref) WHERE id=?""",
                     (status, detail[:400], stamp, status, stamp, ref, job['id']))
    return job['id'], status, detail


def scheduler_url(url, job):
    return sender.check_base_url(url) + '/api/scheduler/' + quote(job['session_id'], safe='')


def stage_hosted_demos(conn, folder):
    """Copy only currently opted-in, validated demos for the configured HTTPS host."""
    prepare_daily(conn)
    folder = Path(folder)
    count, skipped = 0, []
    for row in conn.execute("""SELECT p.*,b.name FROM whatsapp_demo_permissions p
                              JOIN businesses b ON b.id=p.business_id WHERE p.enabled=1"""):
        if not sender.permission_current(conn, row):
            continue
        try:
            _, path, digest = sender.validated_export(conn, row['business_id'])
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Export changed while staging.')
            (folder / (digest + '.html')).write_bytes(data)
            count += 1
        except (ValueError, OSError) as exc:
            skipped.append(f"{row['name']}: {exc}")
    return count, skipped


def hosted_demo(conn, job, settings):
    _, path, digest = sender.validated_export(conn, job['business_id'])
    if str(path) != job['html_path'] or digest != job['html_sha256']:
        raise ValueError('Generated website differs from the scheduled demo.')
    link = settings['media_base'].rstrip('/') + '/' + digest + '.html'
    with urlopen(link, timeout=8) as response:
        if urlsplit(response.geturl()).scheme != 'https':
            raise ValueError('Hosted demo must remain on HTTPS.')
        data = response.read(path.stat().st_size + 1)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('Hosted demo does not match this business’s generated HTML.')
        if response.headers.get_content_type() != 'text/html':
            raise ValueError('Hosted demo must return text/html.')
    return link


def submit_native(conn, url, key, job, settings, end, *, stopped=None):
    try:
        link = hosted_demo(conn, job, settings)
        when = datetime.fromisoformat(job['schedule_utc'])
        # A slow host or a resumed process must never submit a stale send time.
        if when <= datetime.now(timezone.utc) or when >= end.replace(tzinfo=sender.KOLKATA).astimezone(timezone.utc):
            return update_job(conn, job, 'cancelled', 'Native scheduling window elapsed before submission.')
        payload = {'jid': sender.phone_jid(job['recipient']), 'sendAt': job['schedule_utc']}
        if job['daily_mode'] == 'native-html':
            payload.update(content=job['message'], mediaUrl=link, mediaType='document')
        else:
            payload['content'] = job['message'].replace(
                '📎 Please check your attached website demo.', 'Website demo: ' + link)
    except (OSError, ValueError) as exc:
        return update_job(conn, job, 'failed', str(exc))

    with conn:
        conn.execute('BEGIN IMMEDIATE')
        flags = dict(conn.execute("SELECT name,value FROM whatsapp_demo_settings WHERE name IN ('delivery_paused','daily_enabled')"))
        if (flags.get('delivery_paused') == '1' or flags.get('daily_enabled') != '1'
                or not sender.permission_current(conn, job)):
            return
        if stopped and stopped():
            return None
        claimed = conn.execute("""UPDATE whatsapp_demo_jobs SET status='uncertain',error=?,updated_at=?,
                                 daily_gateway_content=? WHERE id=? AND status='scheduled' AND schedule_utc=? AND daily_mode=?""",
                               ('Native submission started; verify WA-AKG before repeating.', sender.utc_now(),
                                payload['content'], job['id'], job['schedule_utc'], job['daily_mode']))
    if not claimed.rowcount:
        return
    try:
        result = sender._fetch(scheduler_url(url, job), key, 'POST', payload)
        data = result.get('data', result) if isinstance(result, dict) else {}
        ref = data.get('id') if isinstance(data, dict) else None
        if not isinstance(ref, str) or not ref:
            raise ValueError('WA-AKG returned no schedule ID. Check its scheduler before repeating.')
    except (ValueError, OSError, RuntimeError) as exc:
        return update_job(conn, job, 'uncertain', str(exc))
    return update_job(conn, job, 'sending', 'Scheduled in WA-AKG; awaiting gateway result.', ref)


def reconcile(conn, url, key, settings, paused):
    recent = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(timespec='seconds')
    pending = conn.execute("""SELECT * FROM whatsapp_demo_jobs
        WHERE daily_mode IN ('native-link','native-html') AND
        ((status='sending' AND api_message_ref<>'') OR
         (status='uncertain' AND api_message_ref='' AND daily_gateway_content<>'' AND schedule_utc>=?))
        ORDER BY id""", (recent,)).fetchall()
    lists = {}
    for job in pending:
        endpoint = scheduler_url(url, job)
        ref = job['api_message_ref']
        if endpoint not in lists:
            response = sender._fetch(endpoint, key)
            items = response.get('data') if isinstance(response, dict) else response
            if not isinstance(items, list):
                raise ValueError('Unexpected WA-AKG schedule list; check gateway version.')
            lists[endpoint] = {item['id']: item for item in items
                               if isinstance(item, dict) and isinstance(item.get('id'), str)}
        if not ref:
            matches = []
            for candidate in lists[endpoint].values():
                try:
                    same_time = datetime.fromisoformat(candidate.get('sendAt', '')) == datetime.fromisoformat(job['schedule_utc'])
                except (TypeError, ValueError):
                    continue
                if (same_time and candidate.get('jid') == sender.phone_jid(job['recipient'])
                        and candidate.get('content') == job['daily_gateway_content']):
                    matches.append(candidate['id'])
            if len(matches) != 1:
                continue
            ref = matches[0]
            update_job(conn, job, 'sending', 'Recovered WA-AKG schedule after an interrupted submission.', ref)
        item = lists[endpoint].get(ref)
        state = str(item.get('status', '')).upper() if item else ''
        if state in ('SENT', 'COMPLETED'):
            return update_job(conn, job, 'sent', 'WA-AKG reports sent. Confirm receipt in WhatsApp.')
        if state in ('FAILED', 'CANCELLED', 'CANCELED'):
            return update_job(conn, job, 'failed' if state == 'FAILED' else 'cancelled',
                              'WA-AKG reports ' + state.lower() + '.')
        current = conn.execute('SELECT html_hash FROM generation WHERE business_id=?',
                               (job['business_id'],)).fetchone()
        if (paused or settings['enabled'] != '1' or job['daily_mode'] != settings['mode']
                or not sender.permission_current(conn, job)
                or not current or current[0] != job['html_sha256']
                or job['error'] == 'Cancellation requested'):
            try:
                sender._fetch(endpoint + '/' + quote(ref, safe=''), key, 'DELETE')
            except RuntimeError as exc:
                if str(exc) != 'WA-AKG HTTP 404':
                    raise
                return update_job(conn, job, 'uncertain', 'WA-AKG job no longer found during cancellation. Check chat.')
            if job['schedule_utc'] <= sender.utc_now():
                return update_job(conn, job, 'uncertain', 'Cancellation was requested after the send time. Check chat.')
            detail = ('Paused before WA-AKG send.' if paused and settings['enabled'] == '1'
                      and job['error'] != 'Cancellation requested' and sender.permission_current(conn, job)
                      else 'WA-AKG confirmed cancellation.')
            return update_job(conn, job, 'cancelled', detail)
        if not item and job['schedule_utc'] <= sender.utc_now():
            return update_job(conn, job, 'uncertain', 'WA-AKG schedule no longer listed. Check chat.')
    return None


def allocate(conn, settings, now, start, end, connected):
    day = now.date().isoformat()
    limit = int(settings['limit'])
    width = (end - start).total_seconds() / limit
    slot = int((now - start).total_seconds() / width)
    native = settings['mode'] != 'local'
    target = now + timedelta(seconds=30) if native else now
    if target >= end:
        return
    def has_room():
        used = conn.execute("SELECT daily_slot FROM whatsapp_demo_jobs WHERE daily_day=?", (day,)).fetchall()
        return len(used) < limit and str(slot) not in {row[0] for row in used}

    if not has_room():
        return
    sessions = [row[0] for row in conn.execute(
        "SELECT DISTINCT session_id FROM whatsapp_demo_permissions WHERE enabled=1")]
    online = {session for session in sessions if connected(session)}
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if not has_room():
            return
        past = conn.execute("""SELECT business_id,recipient FROM whatsapp_demo_jobs
            WHERE (daily_day<>'' AND NOT (status='cancelled' AND error IN
                  ('Daily sending window elapsed','Native scheduling window elapsed before submission.',
                   'Paused before WA-AKG send.'))) OR
                  status IN ('scheduled','sending','sent','uncertain')""").fetchall()
        businesses = {row['business_id'] for row in past}
        already_skipped = {row[0] for row in conn.execute(
            'SELECT business_id FROM demo_daily_skips WHERE day=?', (day,))}
        candidates = conn.execute("""SELECT p.*,b.name FROM whatsapp_demo_permissions p
            JOIN businesses b ON b.id=p.business_id
            ORDER BY p.updated_at,p.business_id""").fetchall()
        for row in candidates:
            bid = row['business_id']
            if bid in already_skipped:
                continue
            if bid in businesses:
                conn.execute('INSERT OR IGNORE INTO demo_daily_skips VALUES (?,?,?)',
                             (day, bid, 'Business already has delivery history.'))
                continue
            try:
                jid = sender.phone_jid(row['recipient'])
                if not row['enabled'] or sender.is_opted_out(conn, jid):
                    raise ValueError('Recipient opted out.')
                if row['session_id'] not in online:
                    continue
                business, path, digest = sender.validated_export(conn, bid)
            except (ValueError, OSError) as exc:
                conn.execute('INSERT OR IGNORE INTO demo_daily_skips VALUES (?,?,?)', (day, bid, str(exc)[:400]))
                continue
            return sender.insert_job(conn, bid, business, path, digest, row['session_id'], row['recipient'],
                                     utc(target), row['consent_note'], day, str(slot), settings['mode'])


def daily_tick(conn, url, key, *, now=None, allow_new=True, stopped=None):
    """Reconcile remote jobs even while paused, then handle one automatic slot."""
    if stopped and stopped():
        return None
    prepare_daily(conn)
    settings = daily_settings(conn)
    paused = sender.delivery_paused(conn)
    outcome = reconcile(conn, url, key, settings, paused)
    if outcome or paused or not allow_new or settings['enabled'] != '1':
        return outcome
    now = now or sender.local_now()
    start, end = window(settings, now)
    stamp = now.date().isoformat()
    with conn:
        conn.execute("""UPDATE whatsapp_demo_jobs SET status='cancelled',error='Daily sending window elapsed',
                        updated_at=? WHERE daily_day<>'' AND status='scheduled'
                        AND (daily_day<? OR (daily_day=? AND ?))""",
                     (sender.utc_now(), stamp, stamp, now >= end))
    if not start <= now < end or (settings['weekends'] != '1' and now.weekday() >= 5):
        return
    row = conn.execute("""SELECT * FROM whatsapp_demo_jobs WHERE daily_day=? AND status='scheduled'
                          ORDER BY id LIMIT 1""", (stamp,)).fetchone()
    if row and (not sender.permission_current(conn, row) or row['daily_mode'] != settings['mode']):
        return update_job(conn, row, 'cancelled', 'Authorization or delivery mode changed.')
    available = None

    def connected(session):
        nonlocal available
        if available is None:
            available = sender.sessions(url, key)
        return sender.session_available(available, session)

    if row is None:
        job_id = allocate(conn, settings, now, start, end, connected)
        if job_id is None:
            return
        row = conn.execute('SELECT * FROM whatsapp_demo_jobs WHERE id=?', (job_id,)).fetchone()
    if not connected(row['session_id']):
        return
    if row['daily_mode'] == 'local':
        return sender.process_due_one(conn, url, key, daily_id=row['id'], stopped=stopped)
    return submit_native(conn, url, key, row, settings, end, stopped=stopped)

