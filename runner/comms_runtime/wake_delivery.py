"""Wake-delivery behind an interface (design §6 step 6, §10 Phase 4).

Delivery WAKES a role worker so it polls its durable inbox sooner. It is strictly an optimization
over the cursor poll — losing a wake loses nothing (the next poll recovers the durable message).
Two implementations:

  * ``SubprocessWakeDelivery`` — the DEFAULT, deterministic. Non-interactive subprocess workers are
    driven by the scheduler directly; "wake" is a synchronous in-process signal (an event / a direct
    poll). No terminal, no tmux, no external process required. This is what the default image runs.

  * ``TmuxWakeDelivery`` — OPTIONAL, only when a workflow selects ``delivery: tmux`` (interactive
    persistent panes). It uses ``tmux send-keys`` to nudge a pane. tmux is a DELIVERY ADAPTER, never
    the store or protocol, and is strictly INTRA-INSTANCE (it cannot cross a container/host
    boundary, design §3). It is constructed ONLY behind the explicit selection flag; the default
    image ships NO tmux (see container/Dockerfile — unchanged by Phase 4), so instantiation checks
    for the binary and FAILS LOUD if selected without tmux present.

The store is the mailbox; delivery is best-effort on top of it. Nothing about delivery grants any
capability — a wake carries no payload and no authority.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from abc import ABC, abstractmethod


class WakeDelivery(ABC):
    """How a worker is nudged to poll its durable inbox. Best-effort; poll is the source of truth."""

    @abstractmethod
    def register(self, session_id: str) -> None:
        """Announce a worker session so it can be woken."""

    @abstractmethod
    def wake(self, session_id: str) -> bool:
        """Nudge the session to poll. Returns True if the nudge was delivered (advisory only)."""

    @abstractmethod
    def wait(self, session_id: str, timeout_s: float) -> bool:
        """Block until woken or timeout. Returns True if woken. (Subprocess/default path.)"""

    def teardown(self, session_id: str) -> None:  # pragma: no cover - default no-op
        """Release any resource associated with the session."""


class SubprocessWakeDelivery(WakeDelivery):
    """Deterministic in-process wake via ``threading.Event``. No external process, no tmux.

    A lost wake is harmless: ``wait`` has a timeout, and the worker polls the durable mailbox on
    wake OR timeout. Delivery here can never lose a durably-accepted message.
    """

    def __init__(self) -> None:
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def register(self, session_id: str) -> None:
        with self._lock:
            self._events.setdefault(session_id, threading.Event())

    def wake(self, session_id: str) -> bool:
        with self._lock:
            ev = self._events.get(session_id)
        if ev is None:
            return False
        ev.set()
        return True

    def wait(self, session_id: str, timeout_s: float) -> bool:
        with self._lock:
            ev = self._events.setdefault(session_id, threading.Event())
        woke = ev.wait(timeout_s)
        if woke:
            ev.clear()
        return woke

    def teardown(self, session_id: str) -> None:
        with self._lock:
            self._events.pop(session_id, None)


class TmuxUnavailable(RuntimeError):
    """tmux delivery was selected but the ``tmux`` binary is not installed (default image ships none)."""


class TmuxWakeDelivery(WakeDelivery):
    """Optional tmux send-keys wake for interactive panes. Adapter only — never the store.

    Constructed ONLY when a workflow selects ``delivery: tmux``. It refuses to instantiate if tmux
    is absent (the default image has no tmux — that is deliberate and unchanged by Phase 4). Panes
    are mapped by ``session_id``; ``wake`` sends a harmless newline to nudge the interactive agent to
    re-poll. It carries NO payload — the message is already durable in the mailbox.
    """

    def __init__(self, socket_name: str = "aq-comms", *, tmux_bin: str | None = None) -> None:
        self._tmux = tmux_bin or shutil.which("tmux")
        if not self._tmux:
            raise TmuxUnavailable(
                "delivery: tmux was selected but no 'tmux' binary is on PATH. The default AQ image "
                "ships NO tmux; install it only behind the explicit interactive-pane selection.")
        self._socket = socket_name
        self._panes: dict[str, str] = {}

    def register(self, session_id: str, pane: str | None = None) -> None:
        # pane target defaults to a window named after the session on the dedicated socket.
        self._panes[session_id] = pane or session_id

    def wake(self, session_id: str) -> bool:
        pane = self._panes.get(session_id)
        if pane is None:
            return False
        try:
            subprocess.run(
                [self._tmux, "-L", self._socket, "send-keys", "-t", pane, "Enter"],
                check=False, capture_output=True, timeout=5,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
            return True
        except (OSError, subprocess.SubprocessError):
            # A failed wake is non-fatal — the worker's next cursor poll recovers the message.
            return False

    def wait(self, session_id: str, timeout_s: float) -> bool:
        # An interactive pane blocks in its own read loop; the scheduler polls the durable mailbox
        # on a timer regardless, so there is nothing to block on here.
        return False

    def teardown(self, session_id: str) -> None:
        self._panes.pop(session_id, None)


def build_wake_delivery(delivery: str) -> WakeDelivery:
    """Pick the delivery adapter for a workflow's declared mode. ``subprocess`` is the default.

    ``tmux`` is only ever reached when a workflow explicitly selects it, and even then this raises
    ``TmuxUnavailable`` (skip loudly) if tmux is not installed — the default image has none.
    """
    if delivery == "tmux":
        return TmuxWakeDelivery()
    return SubprocessWakeDelivery()
