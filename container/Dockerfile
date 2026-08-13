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

# Local embedding model for the `aqmem` agent-memory skill (#4834). Operator decision: embeddings
# come from a model BAKED into the image — no API key, no runtime network, self-contained, survives
# replication. We DOWNLOAD BAAI/bge-small-en-v1.5 (384-dim) into a baked cache dir at BUILD time and
# ASSERT the cache is populated and the dimension is 384, so a network hiccup fails the BUILD rather
# than surfacing at runtime. FASTEMBED_CACHE_PATH points aqmem at this cache at runtime.
ENV FASTEMBED_CACHE_PATH=/opt/aq/models
RUN mkdir -p /opt/aq/models \
    && python3 - <<'PY' \
    && chmod -R a+rX /opt/aq/models
import os
from fastembed import TextEmbedding
cache = os.environ["FASTEMBED_CACHE_PATH"]
model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=cache)
vec = list(model.embed(["docker build warm-up"]))[0]
assert len(vec) == 384, "unexpected embedding dim: %d" % len(vec)
files = [f for _r, _d, fs in os.walk(cache) for f in fs]
assert files, "model cache is empty after download — the bake did not persist any files"
print("aqmem: baked %d model file(s) into %s (dim=%d)" % (len(files), cache, len(vec)))
PY

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
    && chmod +x /app/app-entrypoint.sh /app/scripts/*.sh /app/agent_skills/db/aqdb /app/agent_skills/memory/aqmem \
    && ln -s /app/agent_skills/db/aqdb /usr/local/bin/aqdb \
    && ln -s /app/agent_skills/memory/aqmem /usr/local/bin/aqmem \
    && mkdir -p /var/lib/aq/codex /var/run/aq /workspace \
    && chown -R root:root /app \
    && chmod -R a-w /app \
    && chown -R aq:aq /var/lib/aq /var/run/aq /workspace /opt/aq
USER aq

EXPOSE 8080 8090
ENTRYPOINT ["/usr/bin/tini", "--", "/app/app-entrypoint.sh"]
