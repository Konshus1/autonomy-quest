"""Oracle harness (FORMAL_LAYER_SPEC §4) — the VERDICT boundary of the formal layer.

WHAT THIS BOUNDARY IS, PRECISELY (do not overstate it as "sandboxed"): this harness is fail-closed
on the VERDICT channel — nothing but a clean, digest-verified, deterministic GREEN can ever record a
proof. Capability confinement is BEST-EFFORT + PLATFORM-DEPENDENT, and the active mode is REPORTED in
every result as `confinement`:
  - WHERE an OS sandbox is available — `seatbelt` (macOS sandbox-exec) or `bwrap` (Linux bubblewrap,
    self-tested at first use) — the oracle runs with NETWORK DENIED and filesystem WRITES confined to a
    throwaway workdir: a digest-verified-but-hostile oracle can no longer exfiltrate over the network or
    write the operator's files. Filesystem READS stay allowed (the oracle must read its encoding), so the
    trust root is STILL the digest-pinned, reviewed registry + operator/CI-only invocation — not a perfect
    jail, but real network + write confinement.
  - WHERE neither is available it falls back to `rlimit-only` (the earlier NOT-confined behavior — an
    oracle then CAN read/write files + reach the network); the result says `confinement: rlimit-only` so a
    reader is never misled about what actually protected a given proof.
An oracle you would not review + commit must not be in the manifest. (nsjail / read-only-rootfs remain a
further hardening step; the reason field is also untrusted + hard-capped so it can't smuggle data out.)

Every oracle payload is treated as hostile ON THE VERDICT. An oracle is a self-contained executable
that reads its encoding and emits EXACTLY ONE json line `{"result":"GREEN"|"RED","reason":...}` on
stdout, exit 0, deterministic, within a CPU budget. Outcome mapping:

  GREEN  -> a formal proof (promotion evidence)
  RED    -> a non-confirming result (records nothing; never increments support)
  ERROR  -> anything else at all (bad digest, nonzero exit, extra/!json output, timeout, no sandbox,
            nondeterministic rerun) -> records NOTHING. Uncertainty is never a proof.

Controls: (1) only digest-verified registry keys run; (2) no shell — a fixed argv, a scrubbed minimal
env, `python -I`; (3) resource.setrlimit bounds (CPU, address space, file size, open files, procs) —
if limits cannot be set, run REFUSES (ERROR). NOTE: RLIMIT_AS (memory) is not enforced on macOS, so
the memory bound is effectively CI/Linux-only; the CPU + wall timeout still bound runtime everywhere.
(4) layered timeout; (5) bounded single-line JSON output, and the oracle-supplied `reason` is UNTRUSTED
content — it is hard-capped (it must never be a smuggling channel for secrets into the evidence record);
(6) determinism — a disagreeing rerun is ERROR. IMPORTANT: the determinism re-run executes the oracle
(and thus any side effects) TWICE — oracles MUST be idempotent. Invoked ONLY by operator/CI; the
autonomous loop never calls it.
"""
from __future__ import annotations

import json
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from ralph_portable.formal.executor_registry import ExecutorRegistry

try:
    import resource  # unix-only; absence => we cannot sandbox => fail closed
except ImportError:  # pragma: no cover - non-unix
    resource = None  # type: ignore

_CONFINEMENT: str | None = "__unset__"  # cached: "seatbelt" | "bwrap" | None (rlimit-only)

_CPU_SECONDS = 5
_MEM_BYTES = 512 * 1024 * 1024
_FSIZE_BYTES = 8 * 1024 * 1024
_NOFILE = 64
_NPROC = 64            # allow a bounded child (e.g. a clingo subprocess) but not a fork bomb
_WALL_TIMEOUT = 10     # outer watchdog, larger than the CPU limit
_MAX_OUTPUT = 64 * 1024
_MAX_REASON = 200      # the oracle-supplied reason is UNTRUSTED — hard-cap it so it can't be used to
                       # smuggle secrets/large payloads into the edge's evidence record (review MED).


def _error(reason: str) -> dict[str, Any]:
    return {"result": "ERROR", "reason": reason}


def _set_limits() -> None:  # pragma: no cover - runs in the child pre-exec
    resource.setrlimit(resource.RLIMIT_CPU, (_CPU_SECONDS, _CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (_FSIZE_BYTES, _FSIZE_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (_NOFILE, _NOFILE))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (_MEM_BYTES, _MEM_BYTES))
    except (ValueError, OSError):
        pass  # some platforms (macOS) don't enforce RLIMIT_AS; CPU + wall timeout still bound it
    # RLIMIT_NPROC is per-UID-total on macOS (counts every process owned by the real UID, not
    # just this oracle's subtree), so a tight value breaks the LEGITIMATE bounded child the
    # formal layer anticipates (a clingo subprocess) whenever the host UID already has more
    # processes than the limit -- EAGAIN on the first fork, so no clingo oracle can ever run.
    # On Linux NPROC is effectively per-process-subtree, so the 64 cap still bounds runaway
    # forking there. CPU + wall timeout + the determinism rerun remain the real fork-bomb
    # backstop on every platform (a fork bomb hits the CPU limit or emits nondeterministic /
    # multiline output -> ERROR), so skipping NPROC on Darwin loses no real protection.
    if platform.system() != "Darwin":
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (_NPROC, _NPROC))
        except (ValueError, OSError):
            pass


def _confinement() -> str | None:
    """Detect (once, cached) an OS confinement that adds network + filesystem-write isolation ON TOP
    of the rlimit bounds: 'seatbelt' (macOS sandbox-exec), 'bwrap' (Linux bubblewrap, self-tested to
    confirm it works in this environment), or None (rlimit-only fallback — honestly reported). This
    UPGRADES the boundary from the earlier verdict-only fail-closed toward real capability confinement
    where the platform supports it; where it doesn't, behavior is unchanged and the result says so."""
    global _CONFINEMENT
    if _CONFINEMENT != "__unset__":
        return _CONFINEMENT
    _CONFINEMENT = None
    system = platform.system()
    if system == "Darwin" and shutil.which("sandbox-exec"):
        _CONFINEMENT = "seatbelt"
    elif system == "Linux" and shutil.which("bwrap"):
        try:  # self-test: only trust bwrap if this exact invocation shape works here
            r = subprocess.run(["bwrap", "--ro-bind", "/", "/", "--unshare-net", "--die-with-parent",
                                "--", "true"], capture_output=True, timeout=5)
            if r.returncode == 0:
                _CONFINEMENT = "bwrap"
        except Exception:
            _CONFINEMENT = None
    return _CONFINEMENT


def _confined_argv(inner: list[str], workdir: str) -> list[str]:
    """Wrap the interpreter command with OS confinement: deny network + confine filesystem WRITES to
    the throwaway workdir (reads stay allowed — the trust root is still digest-pin + review, but a
    reviewed-yet-buggy/hostile oracle can no longer exfiltrate over the network or scribble on the
    operator's files). Falls back to the bare command (rlimit-only) when no confinement is available."""
    conf = _confinement()
    if conf == "seatbelt":
        profile = ("(version 1)(allow default)(deny network*)(deny file-write*)"
                   f'(allow file-write* (subpath "{workdir}") (literal "/dev/null"))')
        return ["sandbox-exec", "-p", profile, *inner]
    if conf == "bwrap":
        return ["bwrap", "--ro-bind", "/", "/", "--tmpfs", workdir, "--proc", "/proc", "--dev", "/dev",
                "--unshare-net", "--die-with-parent", "--chdir", workdir, "--", *inner]
    return inner


def _run_once(oracle_path: pathlib.Path, payload_path: pathlib.Path) -> dict[str, Any]:
    if resource is None:
        return _error("no sandbox available (resource limits cannot be set) — refusing to run")
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"}  # scrubbed, minimal
    # Absolute paths: the child runs in an isolated temp cwd, so relative paths would not resolve.
    oracle_abs = str(oracle_path.resolve())
    payload_abs = str(payload_path.resolve())
    with tempfile.TemporaryDirectory() as workdir:
        # -I isolated (no env/site), -B no bytecode writes; then OS confinement (deny net + write) atop.
        inner = [sys.executable, "-I", "-B", oracle_abs, payload_abs]
        argv = _confined_argv(inner, str(pathlib.Path(workdir).resolve()))
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=_WALL_TIMEOUT,
                env=env, cwd=workdir, preexec_fn=_set_limits, shell=False,
            )
        except subprocess.TimeoutExpired:
            return _error("oracle exceeded the wall-clock timeout")
        except Exception as e:  # spawn failure, e.g. preexec rlimit could not be applied
            return _error(f"oracle could not be run under sandbox: {e}")
    if proc.returncode != 0:
        return _error(f"oracle exited nonzero ({proc.returncode}): {proc.stderr[:200]}")
    out = (proc.stdout or "")[:_MAX_OUTPUT]
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if len(lines) != 1:
        return _error(f"oracle must emit exactly one non-empty line, got {len(lines)}")
    try:
        parsed = json.loads(lines[0])
    except json.JSONDecodeError:
        return _error("oracle output is not valid JSON")
    result = parsed.get("result")
    if result not in ("GREEN", "RED"):
        return _error(f"oracle result must be GREEN or RED, got {result!r}")
    # The reason is attacker-controlled (the oracle is untrusted). Coerce to str and HARD-CAP it so
    # it cannot carry a secret/large payload downstream into the edge's evidence record.
    reason = parsed.get("reason")
    reason = (str(reason)[:_MAX_REASON]) if reason is not None else None
    return {"result": result, "reason": reason}


def run_oracle(registry_key: str, registry: ExecutorRegistry, *, determinism_check: bool = True) -> dict[str, Any]:
    """Run the oracle for `registry_key` under the sandbox and return
    {result: GREEN|RED|ERROR, reason, registry_key, oracle_digest}. Fail-closed throughout."""
    try:
        asset = registry.verify_payload(registry_key)  # digest gate — nothing runs unverified
    except Exception as e:
        return {**_error(f"registry integrity failure: {e}"), "registry_key": registry_key}

    oracle_path = registry.oracle_path(registry_key)
    payload_path = registry.payload_path(registry_key)

    first = _run_once(oracle_path, payload_path)
    if first["result"] == "ERROR":
        return {**first, "registry_key": registry_key, "oracle_digest": asset["oracle_digest"]}

    if determinism_check:
        second = _run_once(oracle_path, payload_path)
        if second["result"] != first["result"]:
            return {**_error("oracle is nondeterministic (reruns disagreed) — refusing as proof"),
                    "registry_key": registry_key, "oracle_digest": asset["oracle_digest"]}

    return {"result": first["result"], "reason": first.get("reason"),
            "registry_key": registry_key, "oracle_digest": asset["oracle_digest"],
            "confinement": _confinement() or "rlimit-only"}


def confinement_report() -> dict[str, Any]:
    """OPERATOR SELF-CHECK: report this host's oracle-confinement posture, VERIFIED BY A LIVE PROBE —
    not merely the detected mode. Runs a built-in probe under the ACTIVE confinement that attempts a
    real (benign) network connection and a write OUTSIDE the throwaway workdir, and reports what was
    actually blocked. Lets an operator confirm their machine confines before trusting formal proofs —
    or learn honestly that it does not (rlimit-only: reads/writes/network are NOT confined).

    Returns {mode, network_blocked, write_blocked, confined, note}. `confined` is True only when an OS
    sandbox is active AND both the probe's network + out-of-workdir write were refused at runtime.
    """
    mode = _confinement() or "rlimit-only"
    probe = (
        "import json,os,socket\n"
        "net='blocked'\n"
        "try:\n socket.create_connection(('1.1.1.1',80),timeout=2).close(); net='OPEN'\n"
        "except Exception: pass\n"
        "w='blocked'\n"
        "p=os.path.expanduser('~/.aq_confcheck_probe')\n"
        "try:\n open(p,'w').write('x'); os.remove(p); w='OPEN'\n"
        "except Exception:\n"
        " pass\n"
        "print(json.dumps({'net':net,'write':w}))\n"
    )
    out: dict[str, Any] = {}
    if resource is not None:
        with tempfile.TemporaryDirectory() as workdir:
            wd = str(pathlib.Path(workdir).resolve())
            (pathlib.Path(workdir) / "probe.py").write_text(probe)
            env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"}
            argv = _confined_argv([sys.executable, "-I", "-B", str(pathlib.Path(workdir) / "probe.py")], wd)
            try:
                r = subprocess.run(argv, capture_output=True, text=True, timeout=_WALL_TIMEOUT,
                                   env=env, cwd=workdir, preexec_fn=_set_limits, shell=False)
                line = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
                out = json.loads(line) if line else {"error": (r.stderr or "")[:200]}
            except Exception as e:  # pragma: no cover
                out = {"error": str(e)}
    return _interpret_probe(mode, out)


def _interpret_probe(mode: str, out: dict[str, Any]) -> dict[str, Any]:
    """Pure interpretation of a probe result — the SELF-CHECK's teeth. `confined` is True ONLY when an
    OS sandbox is active AND the probe's live network attempt AND its out-of-workdir write were BOTH
    refused. If the probe leaked (net or write == OPEN), or the mode is rlimit-only, or the probe
    errored, confined is False and the note says so. A self-check that can't report UNSAFE is worthless;
    this reports honestly, and `confinement_report`'s CLI exits nonzero unless confined."""
    if not isinstance(out, dict):  # a non-object probe result is treated as an error -> UNSAFE, never confined
        out = {"error": "probe emitted non-object output"}
    net_blocked = out.get("net") == "blocked"
    write_blocked = out.get("write") == "blocked"
    confined = mode in ("seatbelt", "bwrap") and net_blocked and write_blocked
    return {
        "mode": mode,
        "network_blocked": net_blocked,
        "write_blocked": write_blocked,
        "confined": confined,
        "note": ("OS-sandboxed (net + out-of-workdir writes refused, reads still allowed)" if confined
                 else "NOT capability-confined — a digest-verified oracle can read/write files + reach "
                      "the network; trust rests on the digest-pinned, reviewed registry + operator/CI use"),
    }


if __name__ == "__main__":  # operator: `python -m ralph_portable.formal.oracle_harness`
    rep = confinement_report()
    print(json.dumps(rep, indent=2))
    # exit 0 when genuinely confined, 1 otherwise — so CI/operators can GATE on a real block, not a flag.
    raise SystemExit(0 if rep["confined"] else 1)
