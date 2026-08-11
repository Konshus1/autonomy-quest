#!/usr/bin/env python3
"""Actuator-side authorization of a signed verdict. This program never pushes."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import fcntl
import json
import pathlib
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass

try:
    from .run import SCHEMA, canonical, digest_json
except ImportError:
    from run import SCHEMA, canonical, digest_json

FULL_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class AuthorizationRequest:
    public_key: pathlib.Path
    repository: str
    candidate_sha: str
    test_plan_digest: str
    image_id: str
    entrypoint_digest: str
    policy_digest: str
    key_id: str
    nonce_store: pathlib.Path
    now: dt.datetime | None = None


@dataclass(frozen=True)
class AuthorizationDecision:
    accepted: bool
    reason: str
    nonce: str | None = None


def _reject(reason: str) -> AuthorizationDecision:
    return AuthorizationDecision(False, reason)


def _timestamp(value: object) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError("authorization timestamp is not a string")
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("authorization timestamp lacks timezone")
    return parsed.astimezone(dt.timezone.utc)


def _signature_valid(payload: dict, signature: dict, public_key: pathlib.Path) -> bool:
    if signature.get("algorithm") != "ed25519" or not isinstance(signature.get("value"), str):
        return False
    try:
        raw_signature = base64.b64decode(signature["value"], validate=True)
        with tempfile.TemporaryDirectory(prefix="aq-verdict-verify-") as temp:
            root = pathlib.Path(temp)
            message = root / "payload.json"
            sig = root / "signature.bin"
            message.write_bytes(canonical(payload))
            sig.write_bytes(raw_signature)
            result = subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey",
                 str(public_key), "-sigfile", str(sig), "-in", str(message)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            )
        return result.returncode == 0
    except (OSError, ValueError):
        return False


def _consume_nonce(path: pathlib.Path, nonce: str) -> bool:
    """Atomically append a nonce; False means it was already consumed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as ledger:
        fcntl.flock(ledger.fileno(), fcntl.LOCK_EX)
        ledger.seek(0)
        used = {line.rstrip("\n") for line in ledger}
        if nonce in used:
            return False
        ledger.seek(0, 2)
        ledger.write(nonce + "\n")
        ledger.flush()
        return True


def verify_envelope(envelope: object, request: AuthorizationRequest) -> AuthorizationDecision:
    try:
        if not isinstance(envelope, dict):
            return _reject("malformed_envelope")
        payload = envelope["payload"]
        signature = envelope["signature"]
        if not isinstance(payload, dict) or not isinstance(signature, dict):
            return _reject("malformed_envelope")
        if not _signature_valid(payload, signature, request.public_key):
            return _reject("invalid_signature")
        if payload.get("schema") != SCHEMA:
            return _reject("unsupported_schema")
        if payload.get("verdict") != "PASS":
            return _reject("verdict_not_pass")

        candidate = payload["candidate"]
        if not FULL_SHA.fullmatch(request.candidate_sha) or candidate.get("commit") != request.candidate_sha:
            return _reject("candidate_sha_mismatch")
        if candidate.get("repository") != request.repository:
            return _reject("repository_mismatch")

        plan = payload["test_plan"]
        if plan.get("digest") != digest_json({"command": plan.get("command")}):
            return _reject("test_plan_self_digest_mismatch")
        if plan.get("digest") != request.test_plan_digest:
            return _reject("test_plan_digest_mismatch")

        runtime = payload["runtime"]
        if runtime.get("image_id") != request.image_id:
            return _reject("image_identity_mismatch")
        if runtime.get("entrypoint_digest") != digest_json(runtime.get("entrypoint")):
            return _reject("entrypoint_self_digest_mismatch")
        if runtime.get("entrypoint_digest") != request.entrypoint_digest:
            return _reject("entrypoint_identity_mismatch")

        policy = payload["policy"]
        if payload.get("policy_digest") != digest_json(policy):
            return _reject("effective_policy_self_digest_mismatch")
        if payload.get("policy_digest") != request.policy_digest:
            return _reject("effective_policy_mismatch")
        if policy.get("effective_policy_inspected") is not True:
            return _reject("effective_policy_not_inspected")

        result = payload["result"]
        if result.get("exit_code") != 0 or result.get("timed_out") is not False or result.get("infrastructure_error") is not None:
            return _reject("test_result_not_clean")
        passed = result.get("passed_count")
        skipped = result.get("skipped_count")
        collected = result.get("collected_count")
        failed = result.get("failed_count")
        errors = result.get("error_count")
        counts = (collected, passed, failed, skipped, errors)
        if any(type(value) is not int or value < 0 for value in counts):
            return _reject("malformed_test_counts")
        if result.get("census_valid") is not True or result.get("clean") is not True:
            return _reject("untrusted_test_census")
        if failed != 0 or errors != 0:
            return _reject("test_result_not_clean")
        if collected <= 0 or passed <= 0:
            return _reject("all_tests_skipped" if skipped > 0 else "vacuous_test_run")

        authorization = payload["authorization"]
        if authorization.get("key_id") != request.key_id:
            return _reject("signing_key_id_mismatch")
        nonce = authorization.get("nonce")
        if not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", nonce):
            return _reject("invalid_nonce")
        issued = _timestamp(authorization.get("issued_at"))
        expires = _timestamp(authorization.get("expires_at"))
        now = (request.now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        if expires <= issued:
            return _reject("invalid_expiry")
        if issued > now + dt.timedelta(seconds=30):
            return _reject("verdict_not_yet_valid")
        if expires <= now:
            return _reject("verdict_expired")
        if not _consume_nonce(request.nonce_store, nonce):
            return _reject("nonce_replayed")
        return AuthorizationDecision(True, "accepted", nonce)
    except (KeyError, TypeError, ValueError, OSError):
        return _reject("malformed_payload")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-key", required=True, type=pathlib.Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--test-plan-digest", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--entrypoint-digest", required=True)
    parser.add_argument("--policy-digest", required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--nonce-store", required=True, type=pathlib.Path)
    parser.add_argument("verdict", type=pathlib.Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        envelope = json.loads(args.verdict.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"REJECT: malformed_envelope: {exc}", file=sys.stderr)
        return 1
    request = AuthorizationRequest(
        args.public_key, args.repository, args.sha, args.test_plan_digest,
        args.image_id, args.entrypoint_digest, args.policy_digest, args.key_id,
        args.nonce_store,
    )
    decision = verify_envelope(envelope, request)
    if not decision.accepted:
        print(f"REJECT: {decision.reason}", file=sys.stderr)
        return 1
    print("ACCEPT: signed PASS matches every approved authorization binding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
