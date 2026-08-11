#!/usr/bin/env python3
"""Actuator-side, read-only validation of a signed verdict. This program never pushes."""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import subprocess
import sys
import tempfile

try:
    from .run import SCHEMA, canonical
except ImportError:
    from run import SCHEMA, canonical


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-key", required=True, type=pathlib.Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("verdict", type=pathlib.Path)
    args = parser.parse_args()
    try:
        envelope = json.loads(args.verdict.read_text())
        payload = envelope["payload"]
        signature = envelope["signature"]
        if signature["algorithm"] != "ed25519":
            raise ValueError("unsupported signature algorithm")
        if payload["schema"] != SCHEMA:
            raise ValueError("unsupported verdict schema")
        if payload["verdict"] != "PASS":
            raise ValueError("verdict is not PASS")
        if payload["candidate"] != {"repository": args.repository, "commit": args.sha}:
            raise ValueError("verdict candidate does not match requested repository and SHA")
        raw_signature = base64.b64decode(signature["value"], validate=True)
        with tempfile.TemporaryDirectory(prefix="aq-verdict-verify-") as temp:
            root = pathlib.Path(temp)
            message = root / "payload.json"
            sig = root / "signature.bin"
            message.write_bytes(canonical(payload))
            sig.write_bytes(raw_signature)
            result = subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(args.public_key),
                 "-sigfile", str(sig), "-in", str(message)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            )
            if result.returncode:
                raise ValueError("invalid verdict signature")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"REJECT: {exc}", file=sys.stderr)
        return 1
    print("ACCEPT: signed PASS matches repository and candidate SHA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
