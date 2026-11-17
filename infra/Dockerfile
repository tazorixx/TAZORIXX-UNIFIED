# Unified persistent backend: official Node runtime + Python generator + one cloud worker.
# Source is assembled locally from the user-owned WA-AKG checkout; never copy .env.
FROM node:24-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-pip curl ca-certificates openssl && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/tazorixx
RUN python3 -m venv /opt/tazorixx/venv
COPY services/studio/requirements.txt /opt/tazorixx/studio/requirements.txt
COPY services/studio/engine/requirements.txt /opt/tazorixx/studio/engine/requirements.txt
RUN /opt/tazorixx/venv/bin/pip install --no-cache-dir -r /opt/tazorixx/studio/requirements.txt
COPY services/studio /opt/tazorixx/studio
COPY gateway /opt/tazorixx/gateway
WORKDIR /opt/tazorixx/gateway
RUN test -f package.json || (echo 'Gateway source missing: run ASSEMBLE_WINDOWS.ps1 before build' && exit 1)
RUN grep -q 'runtimeStatus' src/app/api/sessions/route.ts || (echo 'Gateway live runtime-status patch missing: do not deploy' && exit 1)
# Build-time environment is not a live credential; runtime secrets come only from Docker Compose.
RUN npm ci --no-audit --no-fund && DATABASE_URL='mysql://build:build@localhost:3306/build' npx prisma generate \
    && AUTH_SECRET='build-time-only-secret-00000000000000000000000' DATABASE_URL='mysql://build:build@localhost:3306/build' NEXT_TELEMETRY_DISABLED=1 npm run build
WORKDIR /opt/tazorixx
COPY infra/start.sh /opt/tazorixx/start.sh
RUN chmod +x /opt/tazorixx/start.sh && mkdir -p /data
EXPOSE 3030 8000
CMD ["/opt/tazorixx/start.sh"]
