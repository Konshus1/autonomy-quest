# autonomy-quest — the infra substrate in one Linux container.
#
# Postgres + Apache AGE (records AND relationships, one database), initialized with the public
# schema. One image. One `docker run`.
#
# NOTE: the web UI files are copied in below (COPY ui/). Launching the UI alongside the loop
# from entrypoint.sh is owned by task #3018 — this image ships the files, the entrypoint
# decides when to start them. A UI crash must never stop the loop.

FROM postgres:16-bookworm

# --- Apache AGE: openCypher graph queries inside Postgres --------------------
# This is the choice that makes the single-container goal work. Without AGE we would need a
# second graph database: a second service, a second backup, and a second way to be half-up.
ARG AGE_BRANCH=release/PG16/1.5.0
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential git postgresql-server-dev-16 \
      libreadline-dev zlib1g-dev flex bison \
      python3 python3-pip python3-venv \
      curl ca-certificates tini \
 && git clone --depth 1 --branch "${AGE_BRANCH}" https://github.com/apache/age.git /tmp/age \
 && make -C /tmp/age install \
 && rm -rf /tmp/age \
 && apt-get purge -y build-essential git postgresql-server-dev-16 flex bison \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# --- The system ---------------------------------------------------------------
WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
ENV PATH="/opt/venv/bin:$PATH"

COPY runner/   /app/runner/
COPY ui/       /app/ui/
COPY schema/   /app/schema/
COPY data/     /app/data/
COPY scripts/  /app/scripts/
COPY aq.py     /app/aq.py
COPY container/entrypoint.sh /app/entrypoint.sh
COPY container/initdb.sh /docker-entrypoint-initdb.d/10-autonomy-quest-schema.sh
COPY container/healthcheck.sh /app/container-healthcheck.sh
RUN chmod +x /app/entrypoint.sh /app/container-healthcheck.sh \
 && chmod +x /docker-entrypoint-initdb.d/10-autonomy-quest-schema.sh \
 && chmod +x /app/scripts/*.sh

# instance.yaml is MOUNTED at run time, not baked in. The instance's identity comes from the
# human's interview — an image that shipped with a mission baked in would be an image aimed
# at someone else's goal.
VOLUME ["/var/lib/postgresql/data"]
EXPOSE 5432 8080

ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
CMD ["postgres"]

# HEALTHY = the substrate is initialized and queryable. The loop starts only after the coding agent
# has aimed this instance with the interview.
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
  CMD /app/container-healthcheck.sh
