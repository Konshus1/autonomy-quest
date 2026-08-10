#!/bin/sh
set -eu
: "${AQ_LOOP_DB_PASSWORD:?AQ_LOOP_DB_PASSWORD is required}"
: "${AQ_ACTOR_DB_PASSWORD:?AQ_ACTOR_DB_PASSWORD is required}"
: "${AQ_GOVERNANCE_DB_PASSWORD:?AQ_GOVERNANCE_DB_PASSWORD is required}"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=loop_password="$AQ_LOOP_DB_PASSWORD" \
  --set=actor_password="$AQ_ACTOR_DB_PASSWORD" \
  --set=governance_password="$AQ_GOVERNANCE_DB_PASSWORD" <<'SQL'
DO $roles$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_loop') THEN CREATE ROLE aq_loop LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_actor') THEN CREATE ROLE aq_actor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_governance') THEN CREATE ROLE aq_governance LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='aq_control_owner') THEN CREATE ROLE aq_control_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; END IF;
END $roles$;
GRANT aq_control_owner TO aq_owner;
SELECT format('ALTER ROLE aq_loop PASSWORD %L', :'loop_password') \gexec
SELECT format('ALTER ROLE aq_actor PASSWORD %L', :'actor_password') \gexec
SELECT format('ALTER ROLE aq_governance PASSWORD %L', :'governance_password') \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE aq TO aq_loop, aq_actor, aq_governance;
GRANT USAGE ON SCHEMA public TO aq_loop, aq_actor, aq_governance;
SQL
