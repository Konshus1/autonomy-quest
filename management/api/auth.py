"""Codex subscription authentication for the public device-code flow.

Only the provider URL, one-time user code, and coarse state cross the API. OAuth tokens remain
inside Codex's runtime credential volume and are never read by this application. The one-time
code is authorization material: the public Compose stack binds this API to loopback by default.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_URL = re.compile(r"https://[^\s]+/codex/device")
_CODE = re.compile(r"\b[A-Z0-9]{4,5}-[A-Z0-9]{4,5}\b")
_STATUS_LOCK = threading.Lock()
_STATUS_CACHE: tuple[float, bool, str] = (0.0, False, "disconnected")


def _login_status(*, fresh: bool = False) -> tuple[bool, str]:
    """Return true only for ChatGPT/OAuth login, never API-key (metered) login."""
    global _STATUS_CACHE
    now = time.monotonic()
    with _STATUS_LOCK:
        if not fresh and now - _STATUS_CACHE[0] < 1.0:
            return _STATUS_CACHE[1], _STATUS_CACHE[2]
        try:
            proc = subprocess.run(
                ["codex", "login", "status"],
                capture_output=True,
                text=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            result = (False, "unavailable")
        else:
            text = _ANSI.sub("", (proc.stderr or "") + "\n" + (proc.stdout or ""))
            if proc.returncode == 0 and "logged in using chatgpt" in text.lower():
                result = (True, "connected")
            elif proc.returncode == 0:
                result = (False, "unsupported_auth_method")
            else:
                result = (False, "disconnected")
        _STATUS_CACHE = (now, result[0], result[1])
        return result


@dataclass
class DeviceLogin:
    process: subprocess.Popen[str] | None = None
    verification_url: str | None = None
    user_code: str | None = None
    error: str | None = None
    generation: int = 0
    started_at: float | None = None
    timeout_seconds: int = 15 * 60
    _reader: threading.Thread | None = field(default=None, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def start(self) -> dict[str, object]:
        connected, _ = _login_status(fresh=True)
        if connected:
            return self.status()
        with self._lock:
            if self.process is not None and self.process.poll() is None:
                return self.status()
            self.verification_url = None
            self.user_code = None
            self.error = None
            self.generation += 1
            generation = self.generation
            try:
                proc = subprocess.Popen(
                    ["codex", "login", "--device-auth"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=os.environ.copy(),
                    start_new_session=True,
                )
            except OSError as exc:
                self.error = f"could not start Codex device login: {exc}"
                return self.status()
            self.process = proc
            self.started_at = time.monotonic()
            self._reader = threading.Thread(
                target=self._read_output, args=(proc, generation), daemon=True
            )
            self._reader.start()
            threading.Thread(
                target=self._expire, args=(proc, generation), daemon=True
            ).start()
        return self.status()

    def _is_current(self, proc: subprocess.Popen[str], generation: int) -> bool:
        return self.process is proc and self.generation == generation

    def _read_output(self, proc: subprocess.Popen[str], generation: int) -> None:
        assert proc.stdout is not None
        try:
            for raw in proc.stdout:
                line = _ANSI.sub("", raw)
                with self._lock:
                    if not self._is_current(proc, generation):
                        return
                    if not self.verification_url:
                        match = _URL.search(line)
                        if match:
                            self.verification_url = match.group(0)
                    if not self.user_code:
                        match = _CODE.search(line)
                        if match:
                            self.user_code = match.group(0)
            rc = proc.wait()
            connected, _ = _login_status(fresh=True)
            with self._lock:
                if self._is_current(proc, generation) and rc != 0 and not connected:
                    self.error = f"Codex device login exited with status {rc}"
        except Exception as exc:
            with self._lock:
                if self._is_current(proc, generation):
                    self.error = f"Codex device login output failed: {exc}"

    def _expire(self, proc: subprocess.Popen[str], generation: int) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
        with self._lock:
            if not self._is_current(proc, generation) or proc.poll() is not None:
                return
            self.error = "Codex device code expired; start a new login"
        os.killpg(proc.pid, signal.SIGTERM)

    def status(self) -> dict[str, object]:
        connected, auth_state = _login_status()
        with self._lock:
            pending = bool(self.process is not None and self.process.poll() is None)
            state = (
                "connected" if connected else
                "pending" if pending else
                "error" if self.error or auth_state == "unsupported_auth_method" else
                "disconnected"
            )
            error = self.error
            if auth_state == "unsupported_auth_method":
                error = "Codex is logged in without a ChatGPT subscription; API-key auth is not allowed"
            return {
                "state": state,
                "connected": connected,
                "pending": pending,
                "verification_url": self.verification_url if pending else None,
                "user_code": self.user_code if pending else None,
                "error": error,
            }

    def stop(self) -> None:
        with self._lock:
            proc = self.process
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)


device_login = DeviceLogin()
