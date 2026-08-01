"""Host + container tests for the stdlib-only Ralph portable surprise core.

Checkpoint v1 for task #3981: prove the #3913 portable package runs inside the
autonomy-quest tree without importing TalkingBack's app/ tree.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "ralph_surprise_resolution"
CORE = ROOT / "ralph_portable" / "surprise_resolution.py"
EXPECTED_CORE_SHA = "59b00bd7a70ca7d550e20cfc9aa2a6b3a3b7edfeee8b29242c297b4e490cc0d9"


def test_portable_core_sha_matches_3913_pin() -> None:
    digest = hashlib.sha256(CORE.read_bytes()).hexdigest()
    assert digest == EXPECTED_CORE_SHA


def test_dry_run_valid_high_without_app_imports() -> None:
    """Run with python -S so site-packages/app cannot silently satisfy imports."""
    script = r"""
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from ralph_portable.surprise_resolution import (
    build_portable_surprise_resolution_intent,
    dry_run_receipt,
)
packet = json.loads((root / "tests/fixtures/ralph_surprise_resolution/valid_high.json").read_text())
intent = build_portable_surprise_resolution_intent(packet, repo_root=root)
receipt = dry_run_receipt(intent)
assert intent.intent_type == "investigate_surprise"
assert receipt["apply_attempted"] is False
assert receipt["write_count"] == 0
print(json.dumps({
    "intent_type": intent.intent_type,
    "source_packet_sha256": intent.source_packet_sha256,
    "dedup_key": intent.dedup_key,
    "apply_attempted": receipt["apply_attempted"],
    "write_count": receipt["write_count"],
}))
"""
    proc = subprocess.run(
        [sys.executable, "-S", "-c", script, str(ROOT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["intent_type"] == "investigate_surprise"
    assert payload["apply_attempted"] is False
    assert payload["write_count"] == 0


def test_invalid_schema_fails_closed() -> None:
    sys.path.insert(0, str(ROOT))
    from ralph_portable.surprise_resolution import (
        SurpriseResolutionError,
        build_portable_surprise_resolution_intent,
        validate_surprise_packet_v0,
    )

    packet = json.loads(
        (FIXTURES / "invalid_schema_missing_evidence.json").read_text()
    )
    diagnostics = validate_surprise_packet_v0(packet)
    assert diagnostics["status"] != "valid"
    try:
        build_portable_surprise_resolution_intent(packet, repo_root=ROOT)
        raise AssertionError("expected SurpriseResolutionError")
    except SurpriseResolutionError as exc:
        assert exc.code == "invalid_surprise_packet"
