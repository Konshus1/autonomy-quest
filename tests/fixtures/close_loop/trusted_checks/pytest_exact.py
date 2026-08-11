#!/usr/bin/env python3
"""Image-owned pytest adapter; candidate conftest and plugin loading are disabled."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys

# Imported from image site-packages, not from the candidate (-I is used by shebang invocation).
import pytest

PREFIX = "AQ_CHECK_RESULT:"

class Recorder:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def pytest_runtest_logreport(self, report):
        # Setup/teardown failures are failures. Only the call-phase pass counts.
        if report.skipped:
            self.skipped += 1
        elif report.failed:
            self.failed += 1
        elif report.when == "call" and report.passed:
            self.passed += 1

def main() -> int:
    check_id = os.environ.get("AQ_REQUIRED_CHECK_ID", "")
    candidate = Path(os.environ.get("AQ_CANDIDATE_ROOT", "/candidate"))
    if not check_id or check_id.startswith("/") or ".." in Path(check_id).parts:
        return 64
    recorder = Recorder()
    # Exact node id comes from the external digest-pinned policy. Candidate configuration,
    # entry-point plugins and conftest.py are never loaded.
    target = str(candidate / check_id)
    rc = int(pytest.main([
        "--noconftest", "-p", "no:cacheprovider", "--tb=no", "-q", target
    ], plugins=[recorder]))
    if recorder.skipped:
        outcome = "skipped"
    elif rc == 0 and recorder.passed == 1 and recorder.failed == 0:
        outcome = "passed"
    elif rc in (pytest.ExitCode.NO_TESTS_COLLECTED, pytest.ExitCode.INTERNAL_ERROR,
                pytest.ExitCode.USAGE_ERROR, pytest.ExitCode.INTERRUPTED):
        outcome = "error"
    else:
        outcome = "failed"
    receipt = {
        "protocol": "aq-trusted-check-v1", "id": check_id, "outcome": outcome,
        "passed_count": recorder.passed, "skipped_count": recorder.skipped,
        "detail": f"pytest_exit={rc};failed={recorder.failed}",
    }
    print(PREFIX + json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    # Preserve pytest status. A skip can exit zero, but the trusted receipt still HOLDs it.
    return rc

if __name__ == "__main__":
    raise SystemExit(main())
