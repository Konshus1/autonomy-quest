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
RUN groupadd --gid 10001 aq && useradd --uid 10001 --gid aq --create-home aq
WORKDIR /app
COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --timeout 120 --retries 5 -r requirements.txt
ENV PATH="/opt/venv/bin:$PATH"

COPY runner/ /app/runner/
COPY ui/ /app/ui/
COPY schema/ /app/schema/
# The seeded agent's curated DB bridge (skill + script, #4834). Copied into the image and put
# on PATH so the act phase can read/write the business DB reliably without MCP. The script reads
# the restricted aq_actor DSN from AQ_DB_URL at runtime; no credential is baked in here.
COPY agent_skills/ /app/agent_skills/
COPY data/ /app/data/
COPY scripts/ /app/scripts/
COPY management/ /app/management/
COPY ralph_portable/ /app/ralph_portable/
COPY workflows/ /app/workflows/
COPY aq.py /app/aq.py
COPY templates/running-a-business/instance.yaml /app/instance.yaml
COPY --from=webbuild /build/web/dist /app/management/web/dist
COPY container/app-entrypoint.sh /app/app-entrypoint.sh
# DEPLOY-HYGIENE (#4834): stamp the built commit into the image so "what is deployed" is
# observable at runtime, not reverse-engineered. deploy.sh passes --build-arg GIT_SHA=$(git
# rev-parse HEAD) via docker-compose build.args. Declared LAST so it invalidates as little
# build cache as possible (it changes every commit). Empty when built without the arg — an
# honest "unknown" beats a fabricated value. Exposed at runtime as AQ_GIT_SHA and /app/GIT_SHA,
# surfaced by the management API /health.
ARG GIT_SHA=""
ENV AQ_GIT_SHA=$GIT_SHA
RUN printf '%s' "$GIT_SHA" > /app/GIT_SHA \
    && chmod +x /app/app-entrypoint.sh /app/scripts/*.sh /app/agent_skills/db/aqdb \
    && ln -s /app/agent_skills/db/aqdb /usr/local/bin/aqdb \
    && mkdir -p /var/lib/aq/codex /var/run/aq /workspace \
    && chown -R root:root /app \
    && chmod -R a-w /app \
    && chown -R aq:aq /var/lib/aq /var/run/aq /workspace
USER aq

EXPOSE 8080 8090
ENTRYPOINT ["/usr/bin/tini", "--", "/app/app-entrypoint.sh"]
