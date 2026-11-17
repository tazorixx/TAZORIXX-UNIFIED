#!/usr/bin/env bash
set -euo pipefail
cd /opt/tazorixx/gateway
npm run start &
GW=$!
cd /opt/tazorixx/studio
/opt/tazorixx/venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000 --workers 1 &
API=$!
/opt/tazorixx/venv/bin/python worker.py &
WORKER=$!
terminate(){ kill "$GW" "$API" "$WORKER" 2>/dev/null || true; wait 2>/dev/null || true; }
trap terminate TERM INT EXIT
wait -n "$GW" "$API" "$WORKER" || true
exit 1
