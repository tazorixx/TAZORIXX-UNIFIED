"""Contact controls, optional click counts, and signed WA-AKG enquiry capture."""
import hashlib
import hmac
import json
import os
import re
from contextlib import closing
from urllib.parse import quote, urlsplit

from whatsapp_sender import phone_jid, utc_now

def demo_ref(business_id):
    return hashlib.sha256(business_id.encode()).hexdigest()[:20]


def click_url():
    url = os.environ.get('TAZORIXX_CLICK_URL', '').strip()
    try:
        parts = urlsplit(url)
        if parts.scheme == 'https' and parts.hostname and not parts.username and not parts.fragment:
            return url
    except ValueError:
        pass
    return ''


def add_contact_panel(soup, business_id, name):
    panel = soup.select_one('#tz-demo-contact')
    if panel is None:
        return
    ref = demo_ref(business_id)
    panel['data-ref'] = ref
    panel['data-collector'] = click_url()
    context = f' after viewing the website demo for {name}' if name else ''
    enquiry = f'Hello Tazorixx, I would like to discuss a website or app for my business{context}. [TZ:{ref}]'
    link = panel.select_one('[data-demo-action="whatsapp"]')
    link['href'] = link['href'].split('?', 1)[0] + '?text=' + quote(enquiry, safe='')


def prepare_tracking(conn):
    with conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS demo_enquiries (
            session TEXT, message_id TEXT, business_id TEXT, received_at TEXT,
            PRIMARY KEY (session, message_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS demo_clicks (
            business_id TEXT, action TEXT, count INTEGER NOT NULL,
            PRIMARY KEY (business_id, action))""")


def business_for_ref(conn, ref):
    if not isinstance(ref, str) or not re.fullmatch('[a-f0-9]{20}', ref):
        return None
    return next((row[0] for row in conn.execute('SELECT id FROM businesses')
                 if demo_ref(row[0]) == ref), None)


def capture_enquiry(conn, payload):
    if payload.get('event') != 'message.received':
        return False
    session = payload.get('sessionId')
    data = payload.get('data') or {}
    key = data.get('key') or {}
    jid = key.get('remoteJid') or data.get('from', '')
    message_id = key.get('id')
    if (not isinstance(session, str) or not session or not isinstance(message_id, str)
            or not message_id or key.get('fromMe') or data.get('isGroup')
            or not isinstance(jid, str) or not re.fullmatch(r'[0-9]+@s\.whatsapp\.net|[0-9]+@lid', jid)):
        return False
    alternate = key.get('remoteJidAlt')
    if jid.endswith('@lid') and isinstance(alternate, str) and re.fullmatch(r'[0-9]+@s\.whatsapp\.net', alternate):
        jid = alternate
    raw = data.get('message') or {}
    text = data.get('content') or raw.get('conversation') or (raw.get('extendedTextMessage') or {}).get('text') or ''
    if not isinstance(text, str):
        return False
    from whatsapp_sender import prepare_schema, prepare_auto_schema
    prepare_schema(conn)
    prepare_auto_schema(conn)
    prepare_tracking(conn)
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        matched = [row for row in conn.execute('SELECT * FROM whatsapp_demo_permissions')
                   if phone_jid(row['recipient']) == jid]
        if text.strip().upper() in ('STOP', 'UNSUBSCRIBE', 'OPT OUT'):
            from whatsapp_sender import record_opt_out
            if not conn.execute("INSERT OR IGNORE INTO demo_enquiries VALUES (?,?,NULL,?)",
                                (session, message_id, utc_now())).rowcount:
                return False
            return record_opt_out(conn, jid, [row['business_id'] for row in matched])
        ref = re.search(r'\[TZ:([a-f0-9]{20})\]', text)
        business = business_for_ref(conn, ref[1]) if ref else None
        candidates = {row['business_id'] for row in matched if row['session_id'] == session
                      and conn.execute("""SELECT 1 FROM whatsapp_demo_jobs
                          WHERE business_id=? AND session_id=? AND status='sent' LIMIT 1""",
                          (row['business_id'], session)).fetchone()}
        if not business and len(candidates) == 1:
            business = candidates.pop()
        if not business or not text.strip():
            return False
        return bool(conn.execute("""INSERT OR IGNORE INTO demo_enquiries VALUES (?,?,?,?)""",
                                 (session, message_id, business, utc_now())).rowcount)


def capture_click(conn, payload):
    action = payload.get('action')
    business = business_for_ref(conn, payload.get('ref'))
    if not business or action not in ('call', 'whatsapp', 'email'):
        return False
    prepare_tracking(conn)
    with conn:
        conn.execute("""INSERT INTO demo_clicks VALUES (?,?,1)
                     ON CONFLICT(business_id,action) DO UPDATE SET count=count+1""", (business, action))
    return True


def serve_tracking(port, secret):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from core import APP_DIR, DB_NAME, db_connect
    if len(secret) < 24:
        raise ValueError('Set TAZORIXX_WEBHOOK_SECRET to a random secret of at least 24 characters')

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def log_message(self, *_):
            pass

        def do_POST(self):
            status = 400
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if self.path not in ('/wa-akg', '/click'):
                    status = 404
                elif 0 < size <= (65536 if self.path == '/wa-akg' else 256):
                    raw = self.rfile.read(size)
                    signature = 'sha256=' + hmac.new(secret.encode(), raw, 'sha256').hexdigest()
                    if self.path == '/wa-akg' and not hmac.compare_digest(
                            self.headers.get('X-Webhook-Signature', '').encode(), signature.encode()):
                        status = 401
                    else:
                        payload = json.loads(raw)
                        with closing(db_connect(APP_DIR / DB_NAME)) as conn:
                            if self.path == '/wa-akg':
                                capture_enquiry(conn, payload)
                            else:
                                capture_click(conn, payload)
                        status = 204
            except (ValueError, AttributeError, TypeError):
                pass
            self.send_response(status)
            self.end_headers()

    with HTTPServer(('127.0.0.1', port), Handler) as server:
        print(f'TAZORIXX tracking listener on 127.0.0.1:{port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='TAZORIXX enquiry and click receiver')
    parser.add_argument('--port', type=int, default=8765)
    serve_tracking(parser.parse_args().port, os.environ.get('TAZORIXX_WEBHOOK_SECRET', ''))

