# autonomy-quest application image: FastAPI + React + status UI + loop.
# PostgreSQL/AGE/pgvector live in the separate prebuilt Compose service.  This image never
# compiles or packages a database extension.
FROM node:20-bookworm-slim AS webbuild
WORKDIR /build/frontend
COPY management/frontend/package.json management/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY management/frontend/ ./
RUN npm run build

FROM node:20-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git openssl tini python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*
RUN npm install --global --no-audit --no-fund @openai/codex@0.144.0 \
    && codex --version
WORKDIR /app
COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --timeout 120 --retries 5 -r requirements.txt
ENV PATH="/opt/venv/bin:$PATH"

COPY runner/ /app/runner/
COPY ui/ /app/ui/
COPY schema/ /app/schema/
COPY data/ /app/data/
COPY scripts/ /app/scripts/
COPY management/ /app/management/
COPY ralph_portable/ /app/ralph_portable/
COPY workflows/ /app/workflows/
COPY aq.py /app/aq.py
COPY --from=webbuild /build/web/dist /app/management/web/dist
COPY container/app-entrypoint.sh /app/app-entrypoint.sh
RUN chmod +x /app/app-entrypoint.sh /app/scripts/*.sh

EXPOSE 8080 8090
ENTRYPOINT ["/usr/bin/tini", "--", "/app/app-entrypoint.sh"]
