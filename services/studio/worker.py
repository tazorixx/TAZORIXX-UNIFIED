"""Single-instance, opt-in TAZORIXX dispatch loop. Never run two workers on one DB."""
import logging
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'engine'))
from core import APP_DIR, DB_NAME, db_connect
import whatsapp_sender as wa

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
RUNNING = True

def stop(*_):
    global RUNNING
    RUNNING = False

signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)

def tick():
    with db_connect(APP_DIR / DB_NAME) as conn:
        conn.execute('PRAGMA busy_timeout=15000')
        if wa.delivery_paused(conn):return None
        return wa.process_connected_due(conn,os.environ.get('GATEWAY_INTERNAL_URL','http://localhost:3030'),os.environ.get('GATEWAY_API_KEY',''))

if __name__=='__main__':
    if os.environ.get('ENABLE_SENDING','false').lower()!='true':
        logging.warning('CLOUD SENDING DISABLED. Set ENABLE_SENDING=true only after migration and live gateway verification.')
        while RUNNING:time.sleep(5)
        sys.exit(0)
    if not os.environ.get('GATEWAY_API_KEY') or not os.environ.get('GATEWAY_SESSION_ID'):
        raise RuntimeError('GATEWAY_API_KEY and GATEWAY_SESSION_ID are both required for sending')
    logging.info('TAZORIXX cloud sender started. One worker only; do not run the Windows sender simultaneously.')
    while RUNNING:
        try:
            result=tick()
            if result: logging.info('Delivery state: job=%s status=%s',result[0],result[1])
        except Exception:
            logging.exception('Delivery worker tick failed; jobs retained in database')
        time.sleep(2)
