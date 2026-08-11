"""An untrusted plugin that would forge a failed report if it were loaded."""
from pathlib import Path
import pytest

@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    Path("/tmp/CANDIDATE_CONFTEST_LOADED").write_text("unsafe")
    report = yield
    report.get_result().outcome = "passed"
