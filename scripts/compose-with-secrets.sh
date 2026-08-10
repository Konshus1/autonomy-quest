#!/bin/sh
set -eu
state_dir="${AQ_STATE_DIR:-${HOME}/.local/state/autonomy-quest}"
secret_file="${AQ_COMPOSE_SECRET_FILE:-${state_dir}/compose-secrets}"
mkdir -p "$state_dir"
chmod 700 "$state_dir"
rand() { openssl rand -hex 32; }
if [ ! -f "$secret_file" ]; then
  umask 077
  cat >"$secret_file" <<EOF
AQ_DB_OWNER_PASSWORD=$(rand)
AQ_LOOP_DB_PASSWORD=$(rand)
AQ_ACTOR_DB_PASSWORD=$(rand)
AQ_GOVERNANCE_DB_PASSWORD=$(rand)
AQ_EVALUATOR_DB_PASSWORD=$(rand)
AQ_GOVERNANCE_EVIDENCE_TOKEN=$(rand)
AQ_EVALUATOR_TOKEN=$(rand)
AQ_GOVERNANCE_TOKEN=$(rand)
AQ_GOVERNANCE_ADJUDICATOR=local-human-adjudicator
EOF
fi
# Upgrade existing secret files without rotating established credentials.
if ! grep -q '^AQ_EVALUATOR_DB_PASSWORD=' "$secret_file"; then
  umask 077
  printf 'AQ_EVALUATOR_DB_PASSWORD=%s\n' "$(rand)" >>"$secret_file"
fi
if ! grep -q '^AQ_EVALUATOR_TOKEN=' "$secret_file"; then
  umask 077
  printf 'AQ_EVALUATOR_TOKEN=%s\n' "$(rand)" >>"$secret_file"
fi
chmod 600 "$secret_file"
set -a
# shellcheck disable=SC1090
. "$secret_file"
set +a
if [ "$#" -eq 0 ]; then set -- up -d; fi
exec docker compose "$@"
