# testbox — a BARE LINUX BOX you can watch over VNC.
#
# This is NOT the product container. This is the box we pretend a stranger owns, so we can
# watch a coding agent bootstrap autonomy-quest onto it from nothing, in a browser, live.
#
# Why it matters: every claim in setup.md is a claim about what happens on a machine that has
# never seen this system. The only way to know is to sit a fresh box down and watch. Kevin
# watches the install happen rather than reading a report that says it did.
#
# Deliberately NOT pre-installed: postgres, the agent, the repo. The bootstrap installs them.
# A testbox that arrives with the answers already on it proves nothing.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:1

RUN apt-get update && apt-get install -y --no-install-recommends \
      # --- the viewable desktop -------------------------------------------
      xfce4 xfce4-terminal dbus-x11 x11-xserver-utils \
      tigervnc-standalone-server tigervnc-common \
      novnc websockify \
      # --- what a normal dev box has ---------------------------------------
      curl wget git ca-certificates gnupg sudo nano vim less \
      python3 python3-pip python3-venv \
      build-essential \
 && rm -rf /var/lib/apt/lists/*

# node — the TUI agents (claude / codex / copilot) all install via npm
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

# A normal, non-root user with sudo — because the bootstrap has to work as a HUMAN would run
# it, not as root with everything already permitted. If setup.md only works as root, it does
# not work.
RUN useradd -m -s /bin/bash quest && echo "quest ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers
USER quest
WORKDIR /home/quest

COPY --chown=quest:quest testbox/start.sh /home/quest/start.sh
COPY --chown=quest:quest testbox/WATCH_ME.md /home/quest/Desktop/WATCH_ME.md
RUN chmod +x /home/quest/start.sh

EXPOSE 6080 5901
CMD ["/home/quest/start.sh"]
