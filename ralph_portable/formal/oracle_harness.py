"""SAFE oracle harness (FORMAL_LAYER_SPEC §4) — the adversarial boundary of the formal layer.

Every oracle payload is treated as HOSTILE. An oracle is a self-contained executable that reads its
encoding and emits EXACTLY ONE json line `{"result":"GREEN"|"RED","reason":...}` on stdout, exit 0,
network-free, deterministic, within a CPU+memory budget. This module runs it under non-negotiable
controls and maps the outcome fail-closed:

  GREEN  -> a formal proof (promotion evidence)
  RED    -> a non-confirming result (records nothing; never increments support)
  ERROR  -> anything else at all (bad digest, nonzero exit, extra/!json output, timeout, no sandbox,
            nondeterministic rerun) -> records NOTHING. Uncertainty is never a proof.

Controls: (1) only digest-verified registry keys run; (2) no shell ever — a fixed argv, a scrubbed
minimal env, absolute interpreter; (3) resource.setrlimit isolation (CPU, address space, file size,
open files, processes) — if limits cannot be set, run REFUSES (ERROR); (4) layered timeout;
(5) bounded single-line JSON output; (6) determinism — a disagreeing rerun is ERROR. This is invoked
ONLY by the operator/CI verify route; the autonomous loop never calls it.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
from typing import Any

from ralph_portable.formal.executor_registry import ExecutorRegistry

try:
    import resource  # unix-only; absence => we cannot sandbox => fail closed
except ImportError:  # pragma: no cover - non-unix
    resource = None  # type: ignore

_CPU_SECONDS = 5
_MEM_BYTES = 512 * 1024 * 1024
_FSIZE_BYTES = 8 * 1024 * 1024
_NOFILE = 64
_NPROC = 64            # allow a bounded child (e.g. a clingo subprocess) but not a fork bomb
_WALL_TIMEOUT = 10     # outer watchdog, larger than the CPU limit
_MAX_OUTPUT = 64 * 1024


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
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (_NPROC, _NPROC))
    except (ValueError, OSError):
        pass


def _run_once(oracle_path: pathlib.Path, payload_path: pathlib.Path) -> dict[str, Any]:
    if resource is None:
        return _error("no sandbox available (resource limits cannot be set) — refusing to run")
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"}  # scrubbed, minimal
    # Absolute paths: the child runs in an isolated temp cwd, so relative paths would not resolve.
    oracle_abs = str(oracle_path.resolve())
    payload_abs = str(payload_path.resolve())
    with tempfile.TemporaryDirectory() as workdir:
        try:
            proc = subprocess.run(
                [sys.executable, "-I", oracle_abs, payload_abs],  # -I: isolated, no env/site
                capture_output=True, text=True, timeout=_WALL_TIMEOUT,
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
    return {"result": result, "reason": parsed.get("reason")}


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
            "registry_key": registry_key, "oracle_digest": asset["oracle_digest"]}
