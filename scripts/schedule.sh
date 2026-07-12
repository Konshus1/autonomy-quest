#!/usr/bin/env bash
# Make the loop actually RUN — across reboots, without a human remembering to start it.
#
# This is the difference between "an autonomous system" and "a script someone runs". Everything
# else in this repo is pointless if the loop only turns while a human has a terminal open: the
# whole promise is that it works while you are asleep, and a promise that depends on you being
# awake is not that promise.
#
# Per-OS, because the honest answer differs:
#   linux / wsl2   -> systemd user service (survives logout with lingering enabled)
#   macos          -> launchd LaunchAgent
#   windows-native -> Task Scheduler (printed for the human; we do not silently touch their box)
#   container      -> the entrypoint already supervises it
#
#   ./scripts/schedule.sh install   set it up and START it
#   ./scripts/schedule.sh status    is it ACTUALLY running? (ground truth, not "did we install it")
#   ./scripts/schedule.sh stop      stop and disable

set -euo pipefail
cd "$(dirname "$0")/.."

say()  { printf '\033[36m[aq]\033[0m %s\n' "$1"; }
warn() { printf '\033[33m[aq]\033[0m %s\n' "$1"; }
die()  { printf '\033[31m[aq] FAILED:\033[0m %s\n' "$1" >&2; exit 1; }

CMD="${1:-status}"
ROOT="$PWD"
OS="$(python3 -c "import yaml;print((yaml.safe_load(open('instance.yaml')) or {}).get('platform',{}).get('os',''))" 2>/dev/null || echo "")"
[ -n "$OS" ] || die "no instance.yaml — run the interview first."

INTERVAL="${AQ_INTERVAL_SECONDS:-300}"

# ---------------------------------------------------------------------------
case "$OS" in

  linux|windows-wsl2)
    UNIT="$HOME/.config/systemd/user/autonomy-quest.service"
    case "$CMD" in
      install)
        mkdir -p "$(dirname "$UNIT")"
        cat > "$UNIT" <<EOF
[Unit]
Description=autonomy-quest — the loop
After=network.target postgresql.service

[Service]
Type=simple
WorkingDirectory=$ROOT
EnvironmentFile=$ROOT/.env
Environment=AQ_INTERVAL_SECONDS=$INTERVAL
ExecStart=$ROOT/.venv/bin/python $ROOT/aq.py forever
# A dead loop must come BACK. Restarting is not optional: an autonomous system that stays down
# after one bad night is not autonomous, it is a cron job with extra steps.
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable --now autonomy-quest.service
        # Without lingering, a user service DIES when they log out — the loop would silently stop
        # the moment they closed their SSH session, and look fine until someone noticed.
        loginctl enable-linger "$(id -un)" 2>/dev/null \
          || warn "could not enable lingering — the loop will stop when you log out. Run: sudo loginctl enable-linger $(id -un)"
        say "installed. The loop now runs on boot and restarts if it dies."
        ;;
      status)  systemctl --user is-active autonomy-quest.service >/dev/null 2>&1 \
                 && say "systemd says: running" || warn "systemd says: NOT running" ;;
      stop)    systemctl --user disable --now autonomy-quest.service; say "stopped and disabled" ;;
    esac
    ;;

  macos)
    PLIST="$HOME/Library/LaunchAgents/cloud.autonomyquest.loop.plist"
    case "$CMD" in
      install)
        mkdir -p "$(dirname "$PLIST")"
        cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>cloud.autonomyquest.loop</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/.venv/bin/python</string><string>$ROOT/aq.py</string><string>forever</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/aq.log</string>
  <key>StandardErrorPath</key><string>$ROOT/aq.log</string>
</dict></plist>
EOF
        launchctl unload "$PLIST" 2>/dev/null || true
        launchctl load -w "$PLIST"
        say "installed. The loop now runs at login and restarts if it dies."
        ;;
      status)  launchctl list | grep -q cloud.autonomyquest.loop \
                 && say "launchd says: loaded" || warn "launchd says: NOT loaded" ;;
      stop)    launchctl unload -w "$PLIST"; say "stopped" ;;
    esac
    ;;

  windows-native)
    # We do NOT silently reach into a Windows box. Print it; let the human run it.
    cat <<EOF
Run this in an ADMIN PowerShell to schedule the loop:

  \$action  = New-ScheduledTaskAction -Execute "$ROOT\\.venv\\Scripts\\python.exe" \`
                                      -Argument "aq.py forever" -WorkingDirectory "$ROOT"
  \$trigger = New-ScheduledTaskTrigger -AtStartup
  \$set     = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName "autonomy-quest" -Action \$action -Trigger \$trigger \`
                         -Settings \$set -RunLevel Highest

Then verify it is ACTUALLY running:  Get-ScheduledTask -TaskName autonomy-quest
EOF
    exit 0
    ;;

  *) die "unknown platform.os '$OS'" ;;
esac

# ---------------------------------------------------------------------------
# GROUND TRUTH. "The scheduler says it is running" is a PROXY — the process can be up while the
# loop is dead, which is the exact failure this whole system exists to refuse. So we check the
# only thing that means the loop is alive: has a cycle COMPLETED — acted, recorded, and LEARNED —
# recently?
# ---------------------------------------------------------------------------
if [ "$CMD" != "stop" ] && [ -f .env ]; then
  set -a; . ./.env; set +a
  LAST="$(psql "$AQ_DB_URL" -tAc "
    select coalesce(extract(epoch from now() - max(r.completed_at))::int, -1)
    from runs r join learnings l on l.run_id = r.id
    where r.completed_at is not null" 2>/dev/null || echo -1)"
  if [ "$LAST" -lt 0 ]; then
    warn "the loop has NEVER completed a cycle. Scheduled is not the same as alive."
  elif [ "$LAST" -gt $((INTERVAL * 4)) ]; then
    warn "last completed cycle was ${LAST}s ago (interval is ${INTERVAL}s). The scheduler may be up while the loop is STALLED."
  else
    say "ground truth: a full cycle completed ${LAST}s ago. The loop is genuinely turning."
  fi
fi
