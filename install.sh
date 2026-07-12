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
warn() { printf '\033[33m[aq]\033[0m %s\n' "$1"; }
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
#
# `psql` IS A CLIENT. It is NOT a server.
#
# Found on a real Windows/WSL2 box, and it is my own bug one level deeper. The first version of
# this used `command -v psql` as "postgres is installed". I fixed that to detect the VERSION
# honestly — and left the PRESENCE proxy exactly where it was. The box had postgresql-client-14
# and NO SERVER AT ALL: no /etc/postgresql, no cluster, no initdb, no pg_ctlcluster. So the
# installer cheerfully reported "postgres already present: psql 14.23", then tried to START a
# server that did not exist.
#
# A client binary on PATH tells you someone can TALK to a database. It tells you nothing about
# whether there IS one. Ask the question you actually mean.
# ---------------------------------------------------------------------------

# Ground truth: is there a SERVER (not a client) on this box?
pg_server_present() {
  command -v pg_ctlcluster >/dev/null 2>&1 && return 0   # debian/ubuntu cluster tooling
  command -v initdb        >/dev/null 2>&1 && return 0
  command -v postgres      >/dev/null 2>&1 && return 0
  compgen -G "/etc/postgresql/*/main" >/dev/null 2>&1 && return 0
  ls /opt/homebrew/var/postgresql* >/dev/null 2>&1 && return 0
  return 1
}

# The SERVER's major version — from the server, never from the client. They can differ, and on the
# box that found this bug the client existed while the server did not exist at all.
pg_server_major() {
  if compgen -G "/etc/postgresql/*/main" >/dev/null 2>&1; then
    basename "$(dirname "$(dirname "$(compgen -G '/etc/postgresql/*/main' | head -1)")")" 2>/dev/null \
      || ls /etc/postgresql | sort -n | tail -1
  elif command -v postgres >/dev/null 2>&1; then
    postgres --version 2>/dev/null | sed -nE 's/.* ([0-9]+)\..*/\1/p'
  fi
}

if ! pg_server_present; then
  if command -v psql >/dev/null 2>&1; then
    warn "psql is on PATH, but there is NO POSTGRES SERVER on this box."
    warn "A client can talk to a database. It is not a database. Installing the server."
  fi
  say "installing PostgreSQL $PGVER (server)..."
  case "$OS" in
    macos)
      command -v brew >/dev/null || die "Homebrew not found. Install it, or choose container mode."
      brew install "postgresql@$PGVER" || die "brew install postgresql@$PGVER failed"
      brew services start "postgresql@$PGVER"
      ;;
    linux|windows-wsl2)
      sudo apt-get update -qq
      if ! sudo apt-get install -y "postgresql-$PGVER" "postgresql-server-dev-$PGVER"; then
        # THE DISTRO WINS. Ubuntu 22.04 ships PostgreSQL 14; there is no postgresql-16 without the
        # PGDG repo. Do not fail the human here — install what the distro HAS, say so loudly, and
        # record the truth. A config that asks for 16 does not conjure a 16.
        DEFAULT_PG="$(apt-cache depends postgresql 2>/dev/null | sed -nE 's/.*postgresql-([0-9]+).*/\1/p' | head -1)"
        [ -n "$DEFAULT_PG" ] || die "could not install postgresql-$PGVER and could not determine this distro's default.
   Add the PGDG repo for PostgreSQL $PGVER:
     sudo sh -c 'echo \"deb https://apt.postgresql.org/pub/repos/apt \$(lsb_release -cs)-pgdg main\" > /etc/apt/sources.list.d/pgdg.list'
     curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/pgdg.gpg
     sudo apt-get update && ./install.sh"
        warn "PostgreSQL $PGVER is not in this distro's repos (Ubuntu 22.04 ships $DEFAULT_PG)."
        warn "Installing PostgreSQL $DEFAULT_PG instead, and recording that in instance.yaml."
        warn "If you specifically need $PGVER, add the PGDG repo and re-run."
        sudo apt-get install -y "postgresql-$DEFAULT_PG" "postgresql-server-dev-$DEFAULT_PG" \
          || die "could not install postgresql-$DEFAULT_PG either"
        PGVER="$DEFAULT_PG"
      fi
      ;;
    windows-native)
      die "Native Windows: install PostgreSQL from postgresql.org, then re-run.
   NOTE: Apache AGE has no Windows build — you will NOT get the graph layer.
   For the full system on Windows, use WSL2 or container mode."
      ;;
    *) die "unknown platform.os '$OS'" ;;
  esac
fi

# What is ACTUALLY on the box now — asked of the SERVER.
REAL_PG="$(pg_server_major)"
[ -n "$REAL_PG" ] || die "postgres server still not detected after install. Something is wrong; do not proceed."

if [ "$REAL_PG" != "$PGVER" ]; then
  warn "instance.yaml asks for PostgreSQL $PGVER; this box runs $REAL_PG."
  warn "USING $REAL_PG — the machine is ground truth, not the config file. AGE will be built for it."
  PGVER="$REAL_PG"
fi

# Record the TRUTH in instance.yaml, so the file describes the machine rather than a wish.
python3 - "$PGVER" <<'PYV'
import sys, re, pathlib
v = sys.argv[1]
p = pathlib.Path("instance.yaml"); s = p.read_text()
s2 = re.sub(r'(datastore:(?:.|\n)*?version:\s*)"?\d+"?', r'\g<1>"%s"' % v, s, count=1)
p.write_text(s2)
PYV
say "postgres server: $PGVER"

# START IT. "Installed" is not "running" — which is the entire thesis of this project, and was
# missing from its own installer.
if ! pg_isready -q 2>/dev/null; then
  say "server is installed but not accepting connections — starting it"
  case "$OS" in
    macos) brew services start "postgresql@$PGVER" >/dev/null 2>&1 || true ;;
    linux|windows-wsl2)
      sudo pg_ctlcluster "$PGVER" main start >/dev/null 2>&1 \
        || sudo service postgresql start >/dev/null 2>&1 \
        || sudo systemctl start postgresql >/dev/null 2>&1 || true
      ;;
  esac
fi

for _ in $(seq 1 30); do pg_isready -q && break; sleep 1; done
pg_isready -q || die "postgres $PGVER is installed but will NOT accept connections.
   Try:  sudo pg_ctlcluster $PGVER main start
   Then: pg_isready
   (WSL2 has no systemd by default — 'service postgresql start' may report a unit that does not exist.)"

say "postgres $PGVER is running and accepting connections"

# ---------------------------------------------------------------------------
# 2. Apache AGE — only if the interview asked for it
# ---------------------------------------------------------------------------
if [ "$GRAPH" = "age" ]; then
  if psql -d postgres -tAc "select 1 from pg_available_extensions where name='age'" | grep -q 1; then
    say "AGE already available"
  else
    say "building Apache AGE from source for PG$PGVER (this takes a few minutes)..."
    case "$OS" in
      windows-native) die "AGE cannot be built on native Windows. Use WSL2 or container mode, or set datastore.graph: none." ;;
      linux|windows-wsl2)
        # AGE compiles against pg_config. If postgres was ALREADY on the box we never installed the
        # dev headers, so the build would fail with a missing pg_config or missing postgres.h — an
        # error that says nothing about the real cause. And they must match the ACTUAL server major:
        # dev headers for the wrong version produce an extension the server cannot load.
        if ! command -v pg_config >/dev/null 2>&1; then
          say "installing postgresql-server-dev-$PGVER (needed to build AGE against THIS server)"
          sudo apt-get install -y "postgresql-server-dev-$PGVER" build-essential flex bison \
            || die "could not install postgresql-server-dev-$PGVER — AGE cannot be built.
   Either install it, or set datastore.graph: none (you lose the relationship layer; say so out loud)."
        fi
        ;;
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
AQ_DB_URL="postgresql:///$DB_NAME" AQ_GRAPH="$GRAPH" ./scripts/apply_schema.sh || die "schema failed"
[ "$GRAPH" = "age" ] || say "graph:none — relational history only, no relationship layer"

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
# 6. Wire the blackboard into the resident agent — otherwise the instance's memory is private
#    to its own loop and no other agent can see a thing it has learned.
# ---------------------------------------------------------------------------
RESIDENT="$(read_yaml engine.resident_agent)"
if [ -n "$RESIDENT" ] && command -v "$RESIDENT" >/dev/null 2>&1; then
  ./scripts/register_mcp.sh "$RESIDENT" || say "could not wire MCP into $RESIDENT — do it by hand: ./scripts/register_mcp.sh"
fi

# ---------------------------------------------------------------------------
say ""
say "Installed. THIS IS NOT DONE."
say "A pile of installed packages is not an autonomous system — a TURNING LOOP is."
say ""
say "Next:  ./scripts/verify.sh"
say "It will fail until the loop has completed a full cycle: acted, recorded, and LEARNED."
say "Do not report success to the human until it passes."
