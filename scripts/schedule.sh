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

# shellcheck source=scripts/_env.sh
. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

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
        # ---------------------------------------------------------------------
        # WSL2: THREE THINGS MUST BE TRUE, AND NONE OF THEM IS THE DEFAULT.
        #
        # A LINUX SCHEDULER CANNOT SURVIVE A WINDOWS REBOOT. systemd inside WSL only helps once
        # WSL IS RUNNING — and Windows does not start a Linux VM nobody asked for. So:
        #
        #   1. systemd=true in /etc/wsl.conf   (WSL2 has NO systemd by default — this is why
        #                                       'service postgresql start' reports a unit that
        #                                       does not exist on a fresh box)
        #   2. loginctl enable-linger          (a --user service dies at logout without it)
        #   3. a WINDOWS Task Scheduler entry that STARTS THE DISTRO AT BOOT
        #
        # We got 1 and 2 for free on the first box we tested, because it was already configured
        # that way. That is exactly the trap: a box that already works tells you nothing about a
        # fresh one. Do all three, every time.
        # ---------------------------------------------------------------------
        if [ "$OS" = "windows-wsl2" ]; then
          if ! grep -qs 'systemd *= *true' /etc/wsl.conf 2>/dev/null; then
            say "enabling systemd in /etc/wsl.conf (WSL2 has none by default)"
            printf '[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf >/dev/null
            warn "systemd was just enabled. It does NOT take effect until the distro restarts:"
            warn "    (from Windows PowerShell)  wsl --shutdown"
            warn "Then re-run ./scripts/schedule.sh install."
          fi
        fi

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

        if [ "$OS" = "windows-wsl2" ]; then
          DISTRO="${WSL_DISTRO_NAME:-Ubuntu}"
          say ""
          say "systemd will keep the loop alive INSIDE WSL — but only while WSL IS RUNNING."
          say "A Windows reboot does not start WSL. So run this ONCE, in an ADMIN PowerShell:"
          say ""
          say "  \$a = New-ScheduledTaskAction -Execute 'wsl.exe' \`"
          say "         -Argument '-d $DISTRO -u $(id -un) -e /bin/true'"
          say "  \$t = New-ScheduledTaskTrigger -AtStartup"
          say "  \$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries \`"
          say "         -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2)"
          say "  Register-ScheduledTask -TaskName 'autonomy-quest-wsl-boot' -Action \$a \`"
          say "         -Trigger \$t -Settings \$s -RunLevel Highest"
          say ""
          say "That single command STARTS the distro at boot; systemd inside it then starts the loop."
          say "WE DO NOT RUN IT FOR YOU — it touches Windows, and that is your machine's business."
          say ""
          say "THEN PROVE IT: reboot, DO NOT open a terminal, wait 5 minutes, and from PowerShell run"
          say "  wsl --list --running        <- lists WITHOUT starting. Ubuntu must appear."
          say "and only then check for a heartbeat timestamped AFTER the reboot. 'The service says it"
          say "is enabled' is a PROXY — it can be enabled inside a distro that never booted."
        fi
        say "installed."
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
