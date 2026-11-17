#!/usr/bin/env python3
"""Read-only report for TAZORIXX SQLite delivery state. No network or sends.
Use: py -3 read-bot-job.py 3
Never prints recipients, message contents, secrets or API keys.
"""
import argparse
import os
import sqlite3
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('job_id',type=int,nargs='?',default=None)
    parser.add_argument('--db', default=str(Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData'/'Local'))/'TAZORIXX'/'tazorixx.sqlite3'))
    args=parser.parse_args()
    source=Path(args.db).resolve()
    if not source.is_file():
        raise SystemExit('SQLite DB missing: '+str(source))
    conn=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=5)
    conn.row_factory=sqlite3.Row
    try:
        names={r[1] for r in conn.execute('PRAGMA table_info(whatsapp_demo_jobs)')}
        select=['id','status','schedule_utc','updated_at','sent_utc','send_mode','session_id','error']
        cols=[c for c in select if c in names]
        clause='WHERE id=?' if args.job_id is not None else ''
        values=(args.job_id,) if args.job_id is not None else ()
        sql=f"SELECT {', '.join(cols)} FROM whatsapp_demo_jobs {clause} ORDER BY id DESC LIMIT 15"
        rows=conn.execute(sql,values).fetchall()
        print('TAZORIXX delivery jobs: read-only')
        for row in rows:
            print('---')
            for k in cols:
                value=row[k]
                if k == 'error': value=(value or '')[:250]
                print(f'{k}: {value}')
        print('--- Worker settings (no API key or credentials) ---')
        try:
            wanted=('delivery_paused','background_enabled','worker_activity','last_connection','last_session','gateway_url')
            for r in conn.execute('SELECT name,value FROM whatsapp_demo_settings WHERE name IN (' + ','.join('?'*len(wanted))+') ORDER BY name',wanted):
                print(f'{r[0]}: {r[1]}')
        except sqlite3.OperationalError as e:
            print('Settings table unavailable:', e)
    finally:
        conn.close()

if __name__=='__main__': main()
