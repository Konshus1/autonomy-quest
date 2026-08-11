"""Verifier-owned pytest plugin that emits the authoritative test census.

This module is copied into the verifier image's site-packages and loaded explicitly before
candidate conftests.  Candidate terminal output and ``session.exitstatus`` are never inputs.

The recorder instance is created fresh inside :func:`pytest_configure` and is NOT exposed as a
module global -- ``verifier_census.recorder`` does not exist, so a candidate cannot import and
mutate a shared singleton before the census is snapshot.  (A candidate sharing this process can
still reach the registered plugin object through the pytest plugin manager; that residual is
inherent to the in-process model and is documented in HERMETIC_CENSUS_LIMITATION.md.)

The census is written to an anonymous pipe with a trailing newline and NO seek/truncate, so the
record is append-only: PID 1 rejects anything other than exactly one well-formed record.
"""
from __future__ import annotations

import json
import os

import pytest

CENSUS_SCHEMA = "aq.pytest-census.v1"


class CensusRecorder:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.errors = 0
        self.internal_error = False

    def pytest_runtest_logreport(self, report) -> None:
        if report.skipped:
            self.skipped += 1
        elif report.failed:
            if report.when == "call":
                self.failed += 1
            else:
                self.errors += 1
        elif report.passed and report.when == "call":
            self.passed += 1

    def pytest_collectreport(self, report) -> None:
        if report.failed:
            self.errors += 1

    def pytest_internalerror(self) -> None:
        self.internal_error = True

    def pytest_keyboard_interrupt(self) -> None:
        self.internal_error = True

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session, exitstatus):
        # Snapshot before yielding to candidate sessionfinish hooks.  In particular, a candidate
        # hookwrapper may rewrite session.exitstatus after the yield; that value is not trusted.
        census = {
            "schema": CENSUS_SCHEMA,
            "sessionfinish_reached": True,
            "collected_count": session.testscollected,
            "passed_count": self.passed,
            "failed_count": self.failed,
            "skipped_count": self.skipped,
            "error_count": self.errors,
            "internal_error": self.internal_error,
        }
        census["clean"] = (
            not census["internal_error"]
            and census["failed_count"] == 0
            and census["error_count"] == 0
        )
        yield
        _write_census(census)


def _write_census(census: dict) -> None:
    descriptor_text = os.environ.get("AQ_VERIFIER_CENSUS_FD", "")
    try:
        descriptor = int(descriptor_text)
    except (TypeError, ValueError):
        return
    # Append one newline-framed record.  No lseek/ftruncate: the channel is a pipe (ESPIPE), and
    # append-only delivery is exactly what stops a later candidate write from rewriting this record.
    payload = json.dumps(census, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
    try:
        os.write(descriptor, payload)
    except OSError:
        # PID 1 treats a missing record as failure.  Never fall back to candidate-controlled output.
        return


def pytest_configure(config) -> None:
    # Fresh instance per session, held only by the plugin manager -- no module-global singleton.
    config.pluginmanager.register(CensusRecorder(), "aq-verifier-census-recorder")
