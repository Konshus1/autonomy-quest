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
# GROUND TRUTH, NOT A PROXY. `command -v psql` was the check here, and it is a proxy for "the
# postgres this instance asked for is installed and running". It is neither.
#
# Found on a real Windows/WSL2 box: PostgreSQL 14 was already present but STOPPED. The old check
# saw psql, skipped the install branch, never started the service, and then died at the readiness
# gate. Worse — had it started, we would have bootstrapped onto PG14 while instance.yaml said 16,
# and then built AGE from the PG16 branch against a PG14 server.
#
# So: find out what is ACTUALLY there, what version it ACTUALLY is, and whether it is ACTUALLY
# running. Then say so out loud.
# ---------------------------------------------------------------------------
pg_major() { psql --version 2>/dev/null | sed -nE 's/.* ([0-9]+)\..*/\1/p'; }

INSTALLED_PG="$(pg_major)"

if [ -z "$INSTALLED_PG" ]; then
  say "installing postgres $PGVER..."
  case "$OS" in
    macos)
      command -v brew >/dev/null || die "Homebrew not found. Install it, or choose container mode."
      brew install "postgresql@$PGVER"
      brew services start "postgresql@$PGVER"
      ;;
    linux|windows-wsl2)
      sudo apt-get update -qq
      sudo apt-get install -y "postgresql-$PGVER" "postgresql-server-dev-$PGVER" postgresql-client \
        || die "could not install postgresql-$PGVER.
   Ubuntu 22.04 ships PostgreSQL 14; 16 needs the PGDG repo:
     sudo sh -c 'echo \"deb https://apt.postgresql.org/pub/repos/apt \$(lsb_release -cs)-pgdg main\" > /etc/apt/sources.list.d/pgdg.list'
     curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/pgdg.gpg
     sudo apt-get update
   ...or set datastore.version to the one your distro ships."
      ;;
    windows-native)
      die "Native Windows: install PostgreSQL from postgresql.org, then re-run.
   NOTE: Apache AGE has no Windows build — you will NOT get the graph layer.
   For the full system on Windows, use WSL2 or container mode."
      ;;
    *) die "unknown platform.os '$OS'" ;;
  esac
  INSTALLED_PG="$(pg_major)"
else
  say "postgres already present: $(psql --version)"
fi

# THE VERSION ACTUALLY ON THE BOX WINS — and we say so rather than quietly pretending.
# A config that claims 16 while the machine runs 14 is a lie the whole system would then build on:
# AGE compiled for the wrong major, and a human who believes something untrue about their own box.
if [ -n "$INSTALLED_PG" ] && [ "$INSTALLED_PG" != "$PGVER" ]; then
  warn "instance.yaml asks for PostgreSQL $PGVER, but this box has $INSTALLED_PG."
  warn "USING $INSTALLED_PG — the machine is the ground truth, not the config file."
  warn "AGE will be built for PG$INSTALLED_PG. Recording the real version in instance.yaml."
  python3 - "$INSTALLED_PG" <<'PYV'
import sys, re, pathlib
v = sys.argv[1]
p = pathlib.Path("instance.yaml"); s = p.read_text()
s2 = re.sub(r'(datastore:(?:.|\n)*?version:\s*)"?\d+"?', r'\g<1>"%s"' % v, s, count=1)
p.write_text(s2 if s2 != s else s + f'\n# NOTE: actual postgres major on this box is {v}\n')
PYV
  PGVER="$INSTALLED_PG"
fi

# START IT. "Installed" is not "running" — that distinction is the entire point of this project,
# and the old code only ever started postgres inside the install branch.
if ! pg_isready -q 2>/dev/null; then
  say "postgres is installed but not running — starting it"
  case "$OS" in
    macos) brew services start "postgresql@$PGVER" >/dev/null 2>&1 || true ;;
    linux|windows-wsl2)
      sudo service postgresql start >/dev/null 2>&1 \
        || sudo systemctl start postgresql >/dev/null 2>&1 \
        || sudo pg_ctlcluster "$PGVER" main start >/dev/null 2>&1 || true
      ;;
  esac
fi

for _ in $(seq 1 30); do pg_isready -q && break; sleep 1; done
pg_isready -q || die "postgres is installed but will not accept connections.
   Try:  sudo service postgresql start   (or: sudo pg_ctlcluster $PGVER main start)
   Then check:  pg_isready"

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
