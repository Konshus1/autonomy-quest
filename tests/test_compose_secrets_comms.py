"""#4834 comms deploy wiring — scripts/compose-with-secrets.sh generates the comms credentials.

Runs the REAL script (with a stub `docker` on PATH so `exec docker compose ...` is a no-op) against
a temp state dir, and asserts:
  - a FRESH secret file is minted with AQ_COMMS_OUTBOX_READ_TOKEN + AQ_COMMS_INSTANCE_TOKEN;
  - an EXISTING secret file gains the new comms keys WITHOUT rotating any established credential.
Never touches the docker daemon — the stub records nothing and exits 0.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compose-with-secrets.sh"


def _run(state_dir: Path, secret_file: Path) -> None:
    # A stub `docker` so the script's terminal `exec docker compose up -d` is an inert no-op.
    bin_dir = state_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "docker"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["AQ_STATE_DIR"] = str(state_dir)
    env["AQ_COMPOSE_SECRET_FILE"] = str(secret_file)
    subprocess.run(["sh", str(SCRIPT)], check=True, env=env, cwd=str(ROOT),
                   capture_output=True, text=True, timeout=60)


def _parse(secret_file: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in secret_file.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def test_fresh_secret_file_gets_comms_tokens(tmp_path):
    state = tmp_path / "state"
    secret = state / "compose-secrets"
    _run(state, secret)
    kv = _parse(secret)
    assert kv.get("AQ_COMMS_OUTBOX_READ_TOKEN"), "fresh file must mint the outbox-read token"
    assert kv.get("AQ_COMMS_INSTANCE_TOKEN"), "fresh file must mint the instance token"
    assert len(kv["AQ_COMMS_OUTBOX_READ_TOKEN"]) >= 32
    assert oct(secret.stat().st_mode)[-3:] == "600"


def test_existing_secret_file_upgraded_without_rotating(tmp_path):
    state = tmp_path / "state"
    state.mkdir(parents=True)
    secret = state / "compose-secrets"
    # A pre-existing file WITHOUT the comms keys but WITH established creds we must not rotate.
    established = {
        "AQ_DB_OWNER_PASSWORD": "owner-keep",
        "AQ_LOOP_DB_PASSWORD": "loop-keep",
        "AQ_ACTOR_DB_PASSWORD": "actor-keep",
        "AQ_GOVERNANCE_DB_PASSWORD": "gov-keep",
        "AQ_EVALUATOR_DB_PASSWORD": "eval-keep",
        "AQ_GOVERNANCE_EVIDENCE_TOKEN": "evidence-keep",
        "AQ_GOVERNANCE_DECISION_TOKEN": "decision-keep",
        "AQ_INSTANCE_ID": "urn:uuid:keep-me",
        "AQ_EVALUATOR_TOKEN": "evaltok-keep",
        "AQ_EVALUATOR_TRIGGER_TOKEN": "trigger-keep",
        "AQ_GOVERNANCE_TOKEN": "govtok-keep",
        "AQ_GOVERNANCE_ADJUDICATOR": "local-human-adjudicator",
    }
    secret.write_text("".join(f"{k}={v}\n" for k, v in established.items()))
    secret.chmod(0o600)

    _run(state, secret)
    kv = _parse(secret)
    # New comms keys appended...
    assert kv.get("AQ_COMMS_OUTBOX_READ_TOKEN"), "upgrade must add the outbox-read token"
    assert kv.get("AQ_COMMS_INSTANCE_TOKEN"), "upgrade must add the instance token"
    # ...WITHOUT rotating any established credential.
    for k, v in established.items():
        assert kv[k] == v, f"{k} must not be rotated on upgrade"


def test_second_run_is_idempotent_and_does_not_rotate_comms(tmp_path):
    state = tmp_path / "state"
    secret = state / "compose-secrets"
    _run(state, secret)
    first = _parse(secret)
    _run(state, secret)
    second = _parse(secret)
    assert first == second, "a second run must not rotate any credential (comms included)"
