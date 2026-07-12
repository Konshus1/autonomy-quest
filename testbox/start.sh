#!/usr/bin/env bash
# Bring up a viewable desktop, then get out of the way.
set -e
mkdir -p ~/.vnc
# No password: this box is bound to 127.0.0.1 only and is a THROWAWAY. It holds nothing.
vncserver -kill :1 >/dev/null 2>&1 || true
vncserver :1 -geometry 1600x1000 -depth 24 -localhost no -SecurityTypes None --I-KNOW-THIS-IS-INSECURE >/dev/null 2>&1
echo "[testbox] VNC up on :1"
echo "[testbox] open http://localhost:6080/vnc.html  (no password)"
websockify --web=/usr/share/novnc 6080 localhost:5901
