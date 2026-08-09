#!/bin/sh
set -eu
state_dir="${AQ_STATE_DIR:-${HOME}/.local/state/autonomy-quest}"
secret_file="${AQ_COMPOSE_SECRET_FILE:-${state_dir}/compose-secrets}"
mkdir -p "$state_dir"
chmod 700 "$state_dir"
new_secret_state=0
if [ ! -f "$secret_file" ]; then
  new_secret_state=1
  umask 077
  rand() { openssl rand -hex 32; }
  cat >"$secret_file" <<EOF
AQ_DB_OWNER_PASSWORD=$(rand)
AQ_LOOP_DB_PASSWORD=$(rand)
AQ_ACTOR_DB_PASSWORD=$(rand)
AQ_GOVERNANCE_DB_PASSWORD=$(rand)
AQ_GOVERNANCE_EVIDENCE_TOKEN=$(rand)
AQ_GOVERNANCE_LOOP_TOKEN=$(rand)
AQ_GOVERNANCE_TOKEN=$(rand)
AQ_GOVERNANCE_ADJUDICATOR=local-human-adjudicator
AQ_ACT_RPC_TOKEN=$(rand)
EOF
fi
chmod 600 "$secret_file"
if ! grep -q '^AQ_GOVERNANCE_LOOP_TOKEN=' "$secret_file"; then
  umask 077
  printf 'AQ_GOVERNANCE_LOOP_TOKEN=%s\n' "$(openssl rand -hex 32)" >>"$secret_file"
fi
if ! grep -q '^AQ_ACT_RPC_TOKEN=' "$secret_file"; then
  umask 077
  printf 'AQ_ACT_RPC_TOKEN=%s\n' "$(openssl rand -hex 32)" >>"$secret_file"
fi
set -a
# shellcheck disable=SC1090
. "$secret_file"
set +a
rpc_secret_file="${state_dir}/act-rpc-token"
umask 077
printf '%s\n' "$AQ_ACT_RPC_TOKEN" >"$rpc_secret_file"
chmod 600 "$rpc_secret_file"
export AQ_ACT_RPC_TOKEN_HOST_FILE="$rpc_secret_file"
unset AQ_ACT_RPC_TOKEN
if [ "$#" -eq 0 ]; then set -- up -d; fi
project=autonomy-quest
prev=""
for arg in "$@"; do
  if [ "$prev" = "-p" ] || [ "$prev" = "--project-name" ]; then project="$arg"; fi
  prev="$arg"
done
case " $* " in
  *" up "*)
    if [ "$new_secret_state" = 1 ] && docker volume inspect "${project}_aq-postgres-data" >/dev/null 2>&1; then
      echo "[aq] refusing incompatible reuse: ${project}_aq-postgres-data exists but its secret state is missing" >&2
      echo "[aq] restore the matching AQ_STATE_DIR, or explicitly remove the old project volume before a clean start" >&2
      exit 2
    fi
    ;;
esac
exec docker compose "$@"
