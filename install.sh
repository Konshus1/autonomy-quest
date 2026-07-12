#!/usr/bin/env bash
# install.sh — stand the system up from what the interview decided.
#
# Reads instance.yaml. Installs ONLY what the answers called for. Idempotent: running it twice
# on a live instance must not destroy it.
#
# This script does the MECHANICAL part (packages, database, schema, deps). It does NOT decide
# anything — every decision was made in the interview and is recorded in instance.yaml. If you
# find yourself wanting to make a choice here, the interview missed a question.
#
# Anything the MISSION needs that this script doesn't create is the agent's job. This file knows
# about the loop; it does not know about your mission.

set -euo pipefail

cd "$(dirname "$0")"
PGUSER_NAME="$(id -un)"   # $PGUSER_NAME is unset in a non-login shell, and set -u kills us

say()  { printf '\033[36m[aq]\033[0m %s\n' "$1"; }
die()  { printf '\033[31m[aq] FAILED:\033[0m %s\n' "$1" >&2; exit 1; }

[ -f instance.yaml ] || die "no instance.yaml — the interview has not been done. See setup.md."

# ---------------------------------------------------------------------------
# Read the decisions. We do not guess any of these.
# ---------------------------------------------------------------------------
read_yaml() { python3 -c "
import yaml,sys
d = yaml.safe_load(open('instance.yaml')) or {}
for k in '$1'.split('.'):
    d = (d or {}).get(k) if isinstance(d, dict) else None
print(d if d is not None else '')
"; }

OS="$(read_yaml platform.os)"
MODE="$(read_yaml platform.mode)"
GRAPH="$(read_yaml datastore.graph)"
PGVER="$(read_yaml datastore.version)"; PGVER="${PGVER:-16}"
MISSION="$(read_yaml mission.objective)"
DB_NAME="${AQ_DB_NAME:-autonomy_quest}"

[ -n "$MISSION" ] || die "instance.yaml has no mission. An unaimed instance accomplishes nothing."

say "mission:   $MISSION"
say "platform:  $OS / $MODE"
say "datastore: postgres $PGVER, graph=$GRAPH"

if [ "$MODE" = "container" ]; then
  say "container mode — building the image instead"
  docker build -f container/Dockerfile -t autonomy-quest:latest . || die "image build failed"
  say "built. Run it:  docker run -d --name autonomy-quest --env-file .env -p 8080:8080 \\"
  say "                  -v aq-data:/var/lib/postgresql/data autonomy-quest:latest"
  exit 0
fi

# ---------------------------------------------------------------------------
# 1. Postgres — native
# ---------------------------------------------------------------------------
if command -v psql >/dev/null 2>&1; then
  say "postgres already installed: $(psql --version)"
else
  say "installing postgres $PGVER..."
  case "$OS" in
    macos)
      command -v brew >/dev/null || die "Homebrew not found. Install it, or choose container mode."
      brew install "postgresql@$PGVER"
      brew services start "postgresql@$PGVER"
      ;;
    linux|windows-wsl2)
      sudo apt-get update -qq
      sudo apt-get install -y "postgresql-$PGVER" "postgresql-server-dev-$PGVER" postgresql-client
      sudo service postgresql start || sudo systemctl start postgresql || true
      ;;
    windows-native)
      die "Native Windows: install PostgreSQL from postgresql.org, then re-run.
   NOTE: Apache AGE has no Windows build — you will NOT get the graph layer.
   For the full system on Windows, use WSL2 or container mode."
      ;;
    *) die "unknown platform.os '$OS'" ;;
  esac
fi

# wait for it to actually accept connections — 'installed' is not 'running'
for _ in $(seq 1 30); do pg_isready -q && break; sleep 1; done
pg_isready -q || die "postgres installed but not accepting connections"

# A ROLE for the human running this.
#
# apt's postgres ships with exactly one role: 'postgres'. Homebrew's creates one named after
# you. So on Linux every command after this dies with 'role "you" does not exist' — postgres is
# installed, running, and completely unusable. Create the role, idempotently.
if ! psql -d postgres -tAc "select 1" >/dev/null 2>&1; then
  say "creating postgres role for '$PGUSER_NAME'"
  case "$OS" in
    linux|windows-wsl2)
      sudo -u postgres psql -tAc "select 1 from pg_roles where rolname='$PGUSER_NAME'" | grep -q 1 \
        || sudo -u postgres createuser -s "$PGUSER_NAME" \
        || die "could not create a postgres role for '$PGUSER_NAME'"
      ;;
  esac
  psql -d postgres -tAc "select 1" >/dev/null 2>&1 \
    || die "postgres is running but '$PGUSER_NAME' still cannot connect"
fi

# ---------------------------------------------------------------------------
# 2. Apache AGE — only if the interview asked for it
# ---------------------------------------------------------------------------
if [ "$GRAPH" = "age" ]; then
  if psql -d postgres -tAc "select 1 from pg_available_extensions where name='age'" | grep -q 1; then
    say "AGE already available"
  else
    say "building Apache AGE from source (this takes a few minutes)..."
    case "$OS" in
      windows-native) die "AGE cannot be built on native Windows. Use WSL2 or container mode, or set datastore.graph: none." ;;
    esac
    TMP="$(mktemp -d)"
    git clone --depth 1 --branch "release/PG${PGVER}/1.5.0" https://github.com/apache/age.git "$TMP/age" \
      || die "could not fetch AGE source"
    make -C "$TMP/age" install || die "AGE build failed — install build tools, or set datastore.graph: none"
    rm -rf "$TMP"
    say "AGE installed"
  fi
fi

# ---------------------------------------------------------------------------
# 3. Database + schema — idempotent
# ---------------------------------------------------------------------------
if psql -lqt | cut -d\| -f1 | grep -qw "$DB_NAME"; then
  say "database '$DB_NAME' already exists — leaving it alone"
else
  say "creating database '$DB_NAME'"
  createdb "$DB_NAME"
fi

say "applying schema (idempotent)"
if [ "$GRAPH" = "age" ]; then
  psql -d "$DB_NAME" -v ON_ERROR_STOP=1 -f schema/001_init.sql || die "schema failed"
else
  # graph:none — strip the AGE section rather than failing on it
  grep -v -E "^(CREATE EXTENSION IF NOT EXISTS age|LOAD 'age'|SET search_path = ag_catalog|SELECT create_graph)" \
    schema/001_init.sql | psql -d "$DB_NAME" -v ON_ERROR_STOP=1 || die "schema failed"
  say "graph:none — relational history only, no relationship layer"
fi

# ---------------------------------------------------------------------------
# 4. Python deps
# ---------------------------------------------------------------------------
say "installing python deps"
python3 -m venv .venv 2>/dev/null || true
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt || die "pip install failed"

# ---------------------------------------------------------------------------
# 5. Wire up .env
# ---------------------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  say "created .env — the HUMAN fills in any keys. Do not paste keys into the chat."
fi
# These are DERIVED from the install, not chosen by the human — so we OVERWRITE them.
# Appending-only was a bug: .env.example's container defaults (aq:aq@localhost) silently won
# over the database we actually just created, and every command failed auth against a database
# that did not exist. A stale value that outranks reality is worse than no value.
sed -i.bak '/^AQ_DB_URL=/d; /^AQ_GRAPH=/d' .env && rm -f .env.bak
{
  echo "AQ_DB_URL=postgresql:///$DB_NAME"
  echo "AQ_GRAPH=$GRAPH"
} >> .env

# ---------------------------------------------------------------------------
say ""
say "Installed. THIS IS NOT DONE."
say "A pile of installed packages is not an autonomous system — a TURNING LOOP is."
say ""
say "Next:  ./scripts/verify.sh"
say "It will fail until the loop has completed a full cycle: acted, recorded, and LEARNED."
say "Do not report success to the human until it passes."
