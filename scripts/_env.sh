# Load .env — sourced by every script that needs it.
#
# I fixed this in aq.py and left every shell script with the identical flaw: verify.sh died with
# "AQ_DB_URL not set" while a perfectly good .env sat right next to it. Fixing one entry point and
# not the others is worse than fixing none, because now the tool is INCONSISTENT: the same config
# works here and not there, and the user cannot form a correct model of why.
#
# EXPORTED VARIABLES WIN. A file must never silently override an explicit choice.
_aq_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$_aq_root/.env" ]; then
  while IFS= read -r _line || [ -n "$_line" ]; do
    case "$_line" in ''|'#'*) continue ;; esac
    case "$_line" in *=*) ;; *) continue ;; esac
    _k="${_line%%=*}"; _v="${_line#*=}"
    _k="$(printf '%s' "$_k" | tr -d '[:space:]')"
    _v="${_v%\"}"; _v="${_v#\"}"; _v="${_v%\'}"; _v="${_v#\'}"
    # only set if not already exported
    if [ -z "$(eval "printf '%s' \"\${$_k:-}\"")" ]; then
      export "$_k=$_v"
    fi
  done < "$_aq_root/.env"
fi
unset _line _k _v _aq_root
