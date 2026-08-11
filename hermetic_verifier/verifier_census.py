"""Verifier-owned pytest plugin that emits the authoritative test census.

This module is copied into the verifier image's site-packages and loaded explicitly before
candidate conftests.  Candidate terminal output and ``session.exitstatus`` are never inputs.
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
        payload = json.dumps(census, sort_keys=True, separators=(",", ":")).encode("ascii")
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        os.write(descriptor, payload)
        os.fsync(descriptor)
    except (OSError, TypeError, ValueError):
        # The trusted PID 1 treats a missing/malformed record as failure.  Do not turn a census
        # delivery problem into candidate-controlled pytest output or an alternate fallback.
        return


recorder = CensusRecorder()


def pytest_configure(config) -> None:
    config.pluginmanager.register(recorder, "aq-verifier-census-recorder")
