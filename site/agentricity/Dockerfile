# Agentricity marketing site + signup capture — ONE isolated container for Azure ACA.
# Serves the static one-pager AND accepts POST /api/signup. This image is intentionally NOT the big
# autonomy-quest app image: it is a tiny, dependency-light, self-contained deploy with no loop, no
# agent, no MCP — just the site and the append-only signup endpoint.
#
# BUILD CONTEXT = repo root (so the shared migration schema/033_signups.sql can be baked in):
#   docker build -f site/agentricity/Dockerfile -t agentricity-site:latest .
#
# The DB credential is NEVER baked in. The deploy provides SIGNUPS_DB_DSN at runtime (see
# site/agentricity/DEPLOY.md) pointing at the INSERT-only aq_signups_writer role.
FROM python:3.12-slim

RUN groupadd --gid 10001 site && useradd --uid 10001 --gid site --create-home site
WORKDIR /app

COPY site/agentricity/server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# The static site (index.html, js/, assets/, and the EXACT legal paths the app store requires:
# /privacy.html, /terms.html, /refund.html, /legal/*, /app-ads.txt) + the service code.
COPY site/agentricity/ /app/site/
# The append-only migrations, baked in so the deploy can apply them against the shared DB.
COPY schema/033_signups.sql /app/schema/033_signups.sql
COPY schema/034_purchases.sql /app/schema/034_purchases.sql

ENV PYTHONUNBUFFERED=1 \
    PORT=8080
EXPOSE 8080
USER site

# server.app:app resolves its static root as the parent of server/ (i.e. /app/site), so index.html
# and the legal pages serve at their exact paths. Bind to $PORT for ACA.
WORKDIR /app/site
CMD ["sh", "-c", "uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
