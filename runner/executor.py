"""How the loop gets a model to do something. Two ways, one interface.

    ApiExecutor          — call the model API directly. Metered: tokens + per-search fees.
    SubscriptionExecutor — drive the human's TUI agent (Codex / Claude Code / Copilot) under the
                           flat-rate plan they already pay for.

Why subscription mode matters: the loop runs FOREVER. On API mode every cycle costs tokens plus
$10-14 per thousand web searches, and a research-heavy mission can run to real money. On
subscription mode the marginal cost of a cycle is ~zero, and web search comes bundled. Most people
already have one of these plans. It should be the default, and until now there was no code path
for it — the interview could approve subscription mode and the runner could not honour it.

The constraint moves rather than disappearing: on a subscription the limit is RATE, not dollars.
So this module treats a rate limit as a first-class, expected condition — something to wait out,
not an error to crash on.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal

log = logging.getLogger("aq.executor")


class RateLimited(RuntimeError):
    """The subscription's rate limit is exhausted. NOT a bug — the expected steady state of a
    loop that is running as hard as its plan allows. The caller waits and retries."""

    def __init__(self, message: str, retry_after_s: int | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class AgentFailed(RuntimeError):
    """The agent ran and did not produce usable output. Loud, never silent."""


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = Decimal("0")


# ---------------------------------------------------------------------------
# Subscription mode — drive the TUI agent the human already pays for
# ---------------------------------------------------------------------------

class SubscriptionExecutor:
    """Runs the human's coding agent non-interactively, once per call, with a JSON Schema
    pinning the reply shape.

    We use the agent's OWN structured-output support rather than asking it nicely for JSON and
    hoping. Prompt-and-pray parsing is how you get a loop that "learns" from a stray sentence
    the model wrapped around its answer. If the agent cannot produce a reply matching the
    schema, that is a failure and we say so.
    """

    # Agents that can be driven headlessly with a schema. Each entry says how to invoke it.
    ENGINES = {
        "codex": {
            "bin": "codex",
            "argv": lambda prompt, schema_file, cwd: [
                "codex", "exec",
                "--skip-git-repo-check",
                "--sandbox", "workspace-write",
                "-c", "tools.web_search=true",       # search is OFF by default in codex — see interview/07
                "--output-schema", schema_file,
                "--json",
                prompt,
            ],
        },
        "claude-code": {
            "bin": "claude",
            "argv": lambda prompt, schema_file, cwd: [
                "claude", "-p", prompt,
                "--output-format", "json",
            ],
        },
        "copilot": {
            "bin": "copilot",
            "argv": lambda prompt, schema_file, cwd: [
                "copilot", "-p", prompt, "--allow-all-tools",
            ],
        },
    }

    def __init__(self, engine: str, cwd: str = ".", timeout_s: int = 600) -> None:
        if engine not in self.ENGINES:
            raise ValueError(f"engine {engine!r} cannot be driven headlessly (known: {list(self.ENGINES)})")
        self.engine = engine
        self.spec = self.ENGINES[engine]
        self.cwd = cwd
        self.timeout_s = timeout_s

        # Fail at construction, not on the first cycle at 3am.
        if subprocess.run(["which", self.spec["bin"]], capture_output=True).returncode != 0:
            raise RuntimeError(
                f"{engine} is the configured engine but '{self.spec['bin']}' is not on PATH. "
                f"The loop cannot turn."
            )

    # -- rate limits are a normal condition, not an error ---------------------
    _RATE_PATTERNS = [
        re.compile(r"usage limit", re.I),
        re.compile(r"rate.?limit", re.I),
        re.compile(r"quota", re.I),
        re.compile(r"try again in (\d+)\s*(second|minute|hour)", re.I),
        re.compile(r"429"),
    ]

    def _rate_limited(self, text: str) -> int | None:
        """Returns a retry-after in seconds if this looks like a rate limit, else None."""
        for p in self._RATE_PATTERNS:
            m = p.search(text)
            if not m:
                continue
            if m.re.groups >= 2 and m.lastindex and m.lastindex >= 2:
                n, unit = int(m.group(1)), m.group(2).lower()
                return n * {"second": 1, "minute": 60, "hour": 3600}[unit]
            return 900  # unknown backoff: wait 15 min rather than hammering the plan
        return None

    def run(self, prompt: str, schema: dict) -> tuple[dict, Usage]:
        """One agent invocation. Returns (validated reply, usage).

        Cost is ZERO — that's the whole point of subscription mode — but we still record token
        counts when the agent reports them, so the human can see how hard the loop is working
        against their rate limit.
        """
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(schema, fh)
            schema_file = fh.name

        argv = self.spec["argv"](prompt, schema_file, self.cwd)
        try:
            proc = subprocess.run(
                argv, cwd=self.cwd, capture_output=True, text=True,
                timeout=self.timeout_s,
                env={**os.environ, "NO_COLOR": "1"},
            )
        except subprocess.TimeoutExpired:
            raise AgentFailed(f"{self.engine} exceeded {self.timeout_s}s and was killed") from None
        finally:
            os.unlink(schema_file)

        blob = (proc.stdout or "") + (proc.stderr or "")

        wait = self._rate_limited(blob)
        if wait is not None:
            raise RateLimited(
                f"{self.engine} hit its plan's rate limit. This is expected on a subscription — "
                f"the loop will wait {wait}s and pick up where it left off.",
                retry_after_s=wait,
            )

        if proc.returncode != 0:
            raise AgentFailed(f"{self.engine} exited {proc.returncode}: {blob[-500:]}")

        reply = self._extract(blob)
        if reply is None:
            raise AgentFailed(
                f"{self.engine} produced no reply matching the schema. "
                f"Not guessing at what it meant. Output tail: {blob[-300:]}"
            )
        return reply, Usage()  # cost_usd = 0: the plan already paid for this

    @staticmethod
    def _extract(blob: str) -> dict | None:
        """Pull the agent's final structured reply out of its event stream.

        `codex --json` emits a stream of envelopes — thread.started, item.started,
        item.completed, turn.completed. The ANSWER lives in exactly one place:

            {"type": "item.completed",
             "item": {"type": "agent_message", "text": "<the schema-matching JSON>"}}

        and the agent may emit several of those as it works, so we want the LAST one.

        The trap, which I fell into and which cost an hour: "take the last JSON object on
        stdout" grabs `turn.completed` — a TELEMETRY FOOTER carrying token counts. It parses
        cleanly. It looks like a successful structured reply. It is not the answer at all, and
        the loop would have happily recorded it as a decision and "learned" from it.

        So we do the opposite of lenient: we look ONLY at agent_message items, and if none
        contains schema-shaped JSON we return None and the caller fails loudly. A reply we had
        to guess at is not a reply.
        """
        best = None
        for line in blob.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(evt, dict) or evt.get("type") != "item.completed":
                continue
            item = evt.get("item") or {}
            if item.get("type") != "agent_message":
                continue                      # command_execution, reasoning, etc — not the answer
            text = item.get("text")
            if not isinstance(text, str):
                continue
            try:
                payload = json.loads(text.strip())
            except json.JSONDecodeError:
                continue                      # a prose message; keep looking for the structured one
            if isinstance(payload, dict):
                best = payload                # last structured agent_message wins
        return best


# ---------------------------------------------------------------------------
# API mode — the metered path (wraps the existing Gateway)
# ---------------------------------------------------------------------------

class ApiExecutor:
    """Calls the model API directly. Every call is metered and every call is charged to the run —
    including the deciding and the reflecting, not just the doing."""

    def __init__(self, gateway) -> None:
        self.gw = gateway

    def run(self, prompt: str, schema: dict) -> tuple[dict, Usage]:
        text, usage = self.gw._call("working", system="Reply with JSON only, matching the schema.", user=prompt)
        return self.gw._json(text), usage


# ---------------------------------------------------------------------------

def build(inst) -> "SubscriptionExecutor | ApiExecutor":
    """Pick the executor the interview asked for. No guessing, no silent fallback:
    if subscription mode was chosen and the agent isn't there, we fail loudly rather than
    quietly running up a metered API bill the human never agreed to."""
    mode = getattr(inst.engine, "mode", None) or inst.models.__dict__.get("mode") or "api"
    if mode == "subscription":
        engine = getattr(inst.engine, "resident_agent", None) or "codex"
        log.info("executor: SUBSCRIPTION mode, driving %s (flat rate, search included)", engine)
        return SubscriptionExecutor(engine)
    from .gateway import Gateway
    log.info("executor: API mode (metered — tokens + per-search fees)")
    return ApiExecutor(Gateway(inst.models))
