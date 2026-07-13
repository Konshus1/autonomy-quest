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


def _already_sandboxed() -> bool:
    """Are we running inside a container / VM that already confines us?

    Matters because Codex sandboxes shell commands with bubblewrap, and bwrap cannot create
    user namespaces inside an unprivileged container:

        bwrap: No permissions to create a new namespace ...

    When that happens EVERY shell command the agent runs fails — so the act phase can research
    the web perfectly well and then be unable to write a single row. The loop turns, the work
    fails, and the cause looks like a database problem. It isn't.

    Codex's own docs say --dangerously-bypass-approvals-and-sandbox is "intended solely for
    running in environments that are externally sandboxed", which is exactly this case: the
    container IS the sandbox. Outside a container we keep the real sandbox and simply grant the
    workspace the access the loop needs.
    """
    if os.environ.get("AQ_SANDBOX") == "bypass":
        return True
    if os.environ.get("AQ_SANDBOX") == "strict":
        return False
    return os.path.exists("/.dockerenv") or os.environ.get("container") is not None


def _codex_sandbox_args(cwd: str) -> list[str]:
    """How to let the agent actually DO the work, without handing it the whole machine.

    THE WORKSPACE MUST BE WRITABLE. This listed writable_roots as ["/var/run/postgresql","/tmp"]
    and left the repo OUT — so on a real Windows/WSL2 box the act phase researched Gemini's pricing
    perfectly and then had nowhere to put it:

        bwrap: Can't mkdir /run/postgresql/.git: Permission denied
        Failed to write file .../LLM_BASELINE_GEMINI_2_5_PRO.md

    Note the first line: codex was treating a writable root as the WORKSPACE. An agent that can
    research and cannot persist is the worst of both worlds — it burns the tokens, reports a
    truthful failure, and moves the number not at all.

    The cwd goes first, explicitly. Never assume the workspace is implicitly writable when you are
    also naming other roots.
    """
    if _already_sandboxed():
        # The container is the boundary. Confining twice just breaks bwrap.
        return ["--dangerously-bypass-approvals-and-sandbox"]

    # DO NOT list the postgres socket directory here.
    #
    # It was listed, and codex treated it as a WORKSPACE: it tried to `mkdir /run/postgresql/.git`
    # and bwrap died BEFORE any shell command ran. Putting the repo first did not help — a listed
    # root is a candidate workspace, full stop. The act phase could reach the web and could not
    # write a single row.
    #
    # So we remove the need for socket access entirely: the loop connects to Postgres over TCP
    # (127.0.0.1), which is a NETWORK operation, already permitted by network_access=true. No
    # filesystem access to /var/run is required at all. install.sh sets AQ_DB_URL accordingly.
    roots = json.dumps([os.path.abspath(cwd), "/tmp"])
    return [
        "--sandbox", "workspace-write",
        "-c", "sandbox_workspace_write.network_access=true",   # the web, AND the db over TCP
        "-c", f"sandbox_workspace_write.writable_roots={roots}",
    ]


def _extract_codex(blob: str) -> dict | None:
    """codex --json: a stream of events. The answer is the LAST agent_message whose text parses.

    The trap that cost an hour: 'take the last JSON object on stdout' grabs `turn.completed` — a
    TELEMETRY FOOTER with token counts. It parses cleanly and looks like a successful reply. So we
    read ONLY agent_message items.
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
            continue
        text = item.get("text")
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            best = payload
    return best


def _extract_claude(blob: str) -> dict | None:
    """claude -p --output-format json: ONE envelope, not a stream.

        {"type":"result", "subtype":"success", "result":"<the assistant's final text>", ...}

    Claude Code has NO --output-schema flag, so the answer arrives as TEXT inside `result`, and
    that text is what must parse as our object. It may be fenced (```json ... ```), because we
    asked for JSON in the prompt rather than at the tool layer.

    This is the whole reason the parser is engine-aware. The codex extractor looked for
    item.completed/agent_message events, found none in Claude's envelope, and returned None — so a
    perfectly good reply was rejected as 'no reply matching the schema'. The bug was the PARSER,
    not the model.
    """
    def _loads(text: str):
        t = text.strip()
        if t.startswith("```"):
            t = t.split("```")[1]
            t = t[4:] if t.lower().startswith("json") else t
            t = t.strip()
        try:
            v = json.loads(t)
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            return None

    # whole-stdout envelope first
    try:
        env = json.loads(blob.strip())
        if isinstance(env, dict):
            if isinstance(env.get("result"), str):
                return _loads(env["result"])
            if env.get("type") == "result" and isinstance(env.get("result"), dict):
                return env["result"]
    except json.JSONDecodeError:
        pass

    # otherwise scan lines for a result envelope (or the bare object)
    best = None
    for line in blob.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(o, dict):
            continue
        if isinstance(o.get("result"), str):
            got = _loads(o["result"])
            if got:
                best = got
        elif "type" not in o:
            best = o          # a bare object is acceptable only if it is not an envelope
    return best


def _validate(reply: dict, schema: dict, engine: str) -> None:
    """Enforce the schema for engines that cannot enforce it themselves.

    Deliberately strict, and deliberately NOT a repair. We do not fill in a missing field, coerce
    a type, or 'do our best' — because the caller is about to record this as a DECISION the loop
    acts on, or a LEARNING it carries forever. A half-understood reply is worse than no reply.
    """
    required = schema.get("required") or []
    missing = [k for k in required if k not in reply]
    if missing:
        raise AgentFailed(
            f"{engine} replied, but the reply does not match the schema. MISSING: {missing}.\n"
            f"Got keys: {sorted(reply)}\n"
            f"Not guessing at what it meant, and not filling in the blanks — this reply would "
            f"become a decision or a learning."
        )
    props = schema.get("properties") or {}
    for k, spec in props.items():
        if k not in reply:
            continue
        want = spec.get("type")
        val = reply[k]
        ok = {
            "string": isinstance(val, str),
            "boolean": isinstance(val, bool),
            "number": isinstance(val, (int, float)) and not isinstance(val, bool),
            "integer": isinstance(val, int) and not isinstance(val, bool),
            "array": isinstance(val, list),
            "object": isinstance(val, dict),
        }.get(want, True)
        if not ok:
            raise AgentFailed(
                f"{engine}: field {k!r} should be {want}, got {type(val).__name__} ({val!r}). "
                f"Refusing to coerce it."
            )


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

    # Each engine says how to INVOKE it, how to EXTRACT its reply, and whether it can enforce a
    # schema itself. These differ enormously and the differences are load-bearing:
    #
    #   codex        --output-schema: the SCHEMA IS ENFORCED AT THE TOOL LAYER. The model is made
    #                to produce a conforming object; we just read it.
    #   claude-code  NO schema flag. It returns a {"type":"result","result":"<text>"} envelope.
    #                So we must put the schema IN THE PROMPT and validate the reply OURSELVES.
    #
    # That second case is why validation lives in this module and not in the caller. A weaker
    # engine must not mean a weaker guarantee — it means WE do the work the engine won't.
    ENGINES = {
        "codex": {
            "bin": "codex",
            "schema_native": True,
            "argv": lambda prompt, schema_file, cwd: [
                "codex", "exec",
                "--skip-git-repo-check",
                *_codex_sandbox_args(cwd),
                "-c", "tools.web_search=true",       # search is OFF by default in codex — see interview/07
                "--output-schema", schema_file,
                "--json",
                prompt,
            ],
            "extract": lambda blob: _extract_codex(blob),
        },
        "claude-code": {
            "bin": "claude",
            "schema_native": False,                  # we enforce it ourselves — see _validate()
            "argv": lambda prompt, schema_file, cwd: [
                "claude", "-p", prompt,
                "--output-format", "json",
                # The loop is autonomous: it must be able to run its own tools without a human
                # clicking approve at 3am. Same trust posture as codex's workspace-write.
                "--dangerously-skip-permissions",
            ],
            "extract": lambda blob: _extract_claude(blob),
        },
        "copilot": {
            "bin": "copilot",
            "schema_native": False,
            "argv": lambda prompt, schema_file, cwd: [
                "copilot", "-p", prompt, "--allow-all-tools",
            ],
            "extract": lambda blob: _extract_claude(blob),   # same shape: a final-result envelope
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

    def run(self, prompt: str, schema: dict, tier: str = "working") -> tuple[dict, Usage]:
        """One agent invocation. `tier` is ignored — a subscription has one model. Returns (validated reply, usage).

        Cost is ZERO — that's the whole point of subscription mode — but we still record token
        counts when the agent reports them, so the human can see how hard the loop is working
        against their rate limit.
        """
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(schema, fh)
            schema_file = fh.name

        # An engine that cannot take a schema at the TOOL layer must be told in the PROMPT — and
        # then checked. Asking nicely is not enforcement; _validate() is.
        if not self.spec.get("schema_native"):
            prompt = (
                f"{prompt}\n\n"
                f"---\n"
                f"REPLY WITH JSON ONLY. No prose, no explanation, no code fence. It must match this "
                f"schema EXACTLY — every required field present, correct types:\n\n"
                f"{json.dumps(schema, separators=(',', ':'))}\n\n"   # compact: every char costs headroom
                f"Your entire final message must be that JSON object and nothing else."
            )

        argv = self.spec["argv"](prompt, schema_file, self.cwd)
        try:
            proc = subprocess.run(
                argv, cwd=self.cwd, capture_output=True, text=True,
                timeout=self.timeout_s,
                # No stdin. claude waits 3s for piped input and then warns on stderr — and that
                # warning, concatenated with stdout, breaks a whole-blob JSON parse.
                stdin=subprocess.DEVNULL,
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

        reply = self.spec["extract"](blob)
        if reply is None:
            raise AgentFailed(
                f"{self.engine} produced no reply matching the schema. "
                f"Not guessing at what it meant. Output tail: {blob[-400:]}"
            )

        # If the engine could not enforce the schema itself, WE enforce it. A weaker engine must
        # not mean a weaker guarantee — a half-understood reply becomes a decision the loop acts
        # on and a learning it carries forever.
        if not self.spec.get("schema_native"):
            _validate(reply, schema, self.engine)

        return reply, Usage()  # cost_usd = 0: the plan already paid for this

    @staticmethod
    def _extract_codex_impl(blob: str) -> dict | None:
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

    def run(self, prompt: str, schema: dict, tier: str = "working") -> tuple[dict, Usage]:
        text, usage = self.gw._call(
            tier,
            system=("Reply with JSON ONLY, matching this schema exactly:\n"
                    + json.dumps(schema)
                    + "\nNo prose, no code fences."),
            user=prompt,
        )
        return self.gw._json(text), usage


# ---------------------------------------------------------------------------

def build(inst) -> "SubscriptionExecutor | ApiExecutor":
    """Pick the executor the interview asked for. No guessing, no silent fallback:
    if subscription mode was chosen and the agent isn't there, we fail loudly rather than
    quietly running up a metered API bill the human never agreed to."""
    if inst.engine.mode == "subscription":
        engine = inst.engine.resident_agent
        log.info("executor: SUBSCRIPTION mode, driving %s (flat rate, search included)", engine)
        # The repo root — the agent's workspace, and it MUST be writable. Defaulting to "." meant
        # the sandbox's writable roots depended on where the process happened to be launched from.
        return SubscriptionExecutor(engine, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from .gateway import Gateway
    log.info("executor: API mode (metered — tokens + per-search fees)")
    return ApiExecutor(Gateway(inst.models))
