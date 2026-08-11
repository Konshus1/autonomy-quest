"""Red-first tests for the REVERT-DISCRIMINATE primitive (close-the-loop Check 1).

Each behavioral test is written to FAIL against a naive/absent implementation
(one that trusts a pass count instead of reading the fails-on-revert structure)
and PASS against ``runner/close_loop/revert_discriminate.py``.

All tests build their own throwaway git repos, so they are hermetic w.r.t. this
repository and require neither Docker nor the sandbox image.  The production
sandbox execution path (``SandboxedPytestRunner``) reuses the existing sandbox
unchanged; its host-side framing is unit-tested here and its live Docker run is
gated behind ``AQ_RD_SANDBOX=1``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from runner.close_loop.revert_discriminate import (
    DiscriminateResult,
    LocalPytestRunner,
    SandboxedPytestRunner,
    NOTE_CANDIDATE_TEST_NOT_GREEN,
    NOTE_NO_CHANGE_UNDER_TEST,
    NOTE_NO_TESTS_COLLECTED,
    changed_test_targets,
    discriminate,
    extract_sandbox_junit,
    failure_is_assertion,
    is_test_path,
    parse_junit_xml,
)

GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "fixture owner",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture owner",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
}


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, env=GIT_ENV, capture_output=True, text=True
    )
    if check:
        assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def make_candidate(
    tmp_path: Path,
    base_files: dict[str, str],
    candidate_changes: dict[str, str | None],
) -> tuple[Path, str, str]:
    """Build a repo with a base commit and a candidate commit.

    ``candidate_changes`` maps path -> new content, or ``None`` to delete.
    Returns ``(repo, base_sha, candidate_sha)``.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    for rel, content in base_files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")
    base_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "switch", "-c", "task/candidate")
    for rel, content in candidate_changes.items():
        path = repo / rel
        if content is None:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "candidate")
    candidate_sha = git(repo, "rev-parse", "HEAD")
    return repo, base_sha, candidate_sha


# ---------------------------------------------------------------------------
# Classification unit (fast, no git).
# ---------------------------------------------------------------------------
def test_is_test_path_classification() -> None:
    assert is_test_path("tests/test_foo.py")
    assert is_test_path("pkg/test_bar.py")
    assert is_test_path("pkg/bar_test.py")
    assert is_test_path("conftest.py")
    assert is_test_path("a/b/tests/helper.py")
    assert not is_test_path("app.py")
    assert not is_test_path("pkg/core.py")
    assert not is_test_path("src/testimonials.py")  # not a test despite the substring


# ---------------------------------------------------------------------------
# RED-FIRST #1: zero-real-tests candidate -> discriminates=False (forged census).
# ---------------------------------------------------------------------------
def test_zero_real_tests_does_not_discriminate(tmp_path: Path) -> None:
    repo, base, candidate = make_candidate(
        tmp_path,
        base_files={"app.py": "VALUE = 1\n"},
        candidate_changes={
            "app.py": "VALUE = 2\n",
            # A supplied "test" file that collects NO tests: the forged census.
            "test_forged.py": "# looks like tests, contains none\nHELPER = 3\n",
        },
    )
    result = discriminate(repo, base, candidate, ["test_forged.py"])
    assert isinstance(result, DiscriminateResult)
    assert result.discriminates is False
    assert result.tests_total == 0
    assert NOTE_NO_TESTS_COLLECTED in result.notes


# ---------------------------------------------------------------------------
# RED-FIRST #2: genuine change + a test that fails on revert -> discriminates=True.
# ---------------------------------------------------------------------------
def test_genuine_change_with_failing_on_revert_discriminates(tmp_path: Path) -> None:
    repo, base, candidate = make_candidate(
        tmp_path,
        base_files={"app.py": "def greet():\n    return 'old'\n"},
        candidate_changes={
            "app.py": "def greet():\n    return 'new'\n",
            "test_app.py": (
                "from app import greet\n"
                "def test_greet():\n"
                "    assert greet() == 'new'\n"
            ),
        },
    )
    result = discriminate(repo, base, candidate, ["test_app.py"])
    assert result.discriminates is True
    assert result.tests_total == 1
    assert result.strong_flips == 1
    assert result.weak_flips == 0
    assert len(result.tests_flipping) == 1
    flip = result.tests_flipping[0]
    assert flip.outcome_candidate == "passed"
    assert flip.outcome_reverted == "failed"
    assert flip.kind == "assertion_failure"
    # The default convenience target derives the same supplied test file.
    assert changed_test_targets(repo, base, candidate) == ("test_app.py",)


# ---------------------------------------------------------------------------
# RED-FIRST #3: no-op tests (pass both ways) -> discriminates=False.
# ---------------------------------------------------------------------------
def test_noop_tests_do_not_discriminate(tmp_path: Path) -> None:
    repo, base, candidate = make_candidate(
        tmp_path,
        base_files={"app.py": "VALUE = 1\n"},
        candidate_changes={
            "app.py": "VALUE = 2\n",
            "test_app.py": (
                "def test_true():\n"
                "    assert True\n"
                "def test_arith():\n"
                "    assert 1 + 1 == 2\n"
            ),
        },
    )
    result = discriminate(repo, base, candidate, ["test_app.py"])
    assert result.discriminates is False
    assert result.tests_total == 2  # the tests are real and collected...
    assert result.tests_flipping == ()  # ...they just do not test the change.


# ---------------------------------------------------------------------------
# RED-FIRST #4: a test that ERRORS (not fails) on revert -> handled deterministically.
# It is a WEAK flip: excluded under the strict DEFAULT (require_clean_failure=True),
# and only counts toward discriminates when require_clean_failure=False.
# ---------------------------------------------------------------------------
def test_error_on_revert_is_weak_flip_and_strictable(tmp_path: Path) -> None:
    repo, base, candidate = make_candidate(
        tmp_path,
        base_files={"app.py": "def existing():\n    return 0\n"},
        candidate_changes={
            # The change ADDS `feature`; reverting removes it, so the test module
            # can no longer import it -> collection error on the reverted tree.
            "app.py": "def existing():\n    return 0\n\ndef feature():\n    return 42\n",
            "test_app.py": (
                "from app import feature\n"
                "def test_feature():\n"
                "    assert feature() == 42\n"
            ),
        },
    )

    # Default is strict: a weak (non-assertion) flip does NOT discriminate.
    strict = discriminate(repo, base, candidate, ["test_app.py"])
    assert strict.discriminates is False
    assert strict.strong_flips == 0
    assert strict.weak_flips == 1  # still reported, just not sufficient
    flip = strict.tests_flipping[0]
    assert flip.outcome_candidate == "passed"
    assert flip.outcome_reverted in {"error", "collection_error"}
    assert flip.kind == "error"

    # Opt in to counting weak flips.
    lenient = discriminate(
        repo, base, candidate, ["test_app.py"], require_clean_failure=False
    )
    assert lenient.discriminates is True
    assert lenient.weak_flips == 1


# ---------------------------------------------------------------------------
# RED-FIRST F1 (the confirmed false-positive): a body-level import is pytest's
# <failure>, NOT an AssertionError.  A zero-behavioral-work candidate that only
# imports its throwaway "change" file inside a test must NOT forge a strong flip,
# and must be excluded under the strict default.
# ---------------------------------------------------------------------------
def test_body_import_failure_on_revert_is_weak_not_strong(tmp_path: Path) -> None:
    repo, base, candidate = make_candidate(
        tmp_path,
        base_files={"app.py": "VALUE = 1\n"},
        candidate_changes={
            # The ENTIRE change is a throwaway file; the test asserts nothing
            # behavioral -- it only imports the marker in its body.
            "_marker.py": "MARKER = True\n",
            "test_app.py": (
                "def test_x():\n"
                "    import _marker\n"  # body-level import -> call-phase <failure> on revert
                "    assert True\n"
            ),
        },
    )
    # On revert `_marker.py` is removed -> the body import raises
    # ModuleNotFoundError -> pytest <failure>.  This must be WEAK, not strong.
    strict = discriminate(repo, base, candidate, ["test_app.py"])
    assert strict.strong_flips == 0
    assert strict.discriminates is False  # the exploit is defused under the default
    assert strict.weak_flips == 1
    flip = strict.tests_flipping[0]
    assert flip.kind == "error"
    assert flip.outcome_reverted == "failed_non_assertion"

    # Even opting into weak flips never promotes this to a strong/assertion flip.
    lenient = discriminate(
        repo, base, candidate, ["test_app.py"], require_clean_failure=False
    )
    assert lenient.strong_flips == 0


# ---------------------------------------------------------------------------
# RED-FIRST F1 (the other side): a genuine behavioral AssertionError on revert
# must still be a STRONG flip and satisfy the strict default.
# ---------------------------------------------------------------------------
def test_assertion_failure_on_revert_is_strong_under_strict(tmp_path: Path) -> None:
    repo, base, candidate = make_candidate(
        tmp_path,
        base_files={"app.py": "def n():\n    return 1\n"},
        candidate_changes={
            "app.py": "def n():\n    return 2\n",
            "test_app.py": "from app import n\ndef test_n():\n    assert n() == 2\n",
        },
    )
    result = discriminate(repo, base, candidate, ["test_app.py"])  # strict default
    assert result.discriminates is True
    assert result.strong_flips == 1
    assert result.weak_flips == 0
    assert result.tests_flipping[0].kind == "assertion_failure"


# ---------------------------------------------------------------------------
# Masquerade guard: an ERROR on the CANDIDATE tree is never counted as a pass,
# so it can never contribute a flip.
# ---------------------------------------------------------------------------
def test_error_on_candidate_never_masquerades_as_pass(tmp_path: Path) -> None:
    repo, base, candidate = make_candidate(
        tmp_path,
        base_files={"app.py": "VALUE = 1\n"},
        candidate_changes={
            "app.py": "VALUE = 2\n",
            "test_app.py": (
                "import pytest\n"
                "@pytest.fixture\n"
                "def boom():\n"
                "    raise RuntimeError('setup boom')\n"
                "def test_needs_boom(boom):\n"
                "    assert True\n"
            ),
        },
    )
    result = discriminate(repo, base, candidate, ["test_app.py"])
    assert result.discriminates is False
    assert result.tests_flipping == ()
    assert any(n.startswith(NOTE_CANDIDATE_TEST_NOT_GREEN) for n in result.notes)


# ---------------------------------------------------------------------------
# Change hidden inside a test file -> nothing to revert -> fails closed.
# ---------------------------------------------------------------------------
def test_change_only_in_test_files_does_not_discriminate(tmp_path: Path) -> None:
    repo, base, candidate = make_candidate(
        tmp_path,
        base_files={"test_app.py": "def test_x():\n    assert True\n"},
        candidate_changes={
            "test_app.py": (
                "def helper():\n    return 5\n"
                "def test_x():\n    assert helper() == 5\n"
            ),
        },
    )
    result = discriminate(repo, base, candidate, ["test_app.py"])
    assert result.discriminates is False
    assert NOTE_NO_CHANGE_UNDER_TEST in result.notes


# ---------------------------------------------------------------------------
# The revert must revert ONLY the change, never the supplied tests, and it must
# handle candidate-deleted source (base content restored) too.
# ---------------------------------------------------------------------------
def test_revert_restores_deleted_source_and_keeps_tests(tmp_path: Path) -> None:
    # base has two source funcs; candidate deletes one and the test asserts it is
    # gone (raises).  On revert the deleted func returns -> test flips.
    repo, base, candidate = make_candidate(
        tmp_path,
        base_files={
            "app.py": "def a():\n    return 1\ndef b():\n    return 2\n",
        },
        candidate_changes={
            "app.py": "def a():\n    return 1\n",  # b() removed
            "test_app.py": (
                "import app\n"
                "def test_b_removed():\n"
                "    assert not hasattr(app, 'b')\n"
            ),
        },
    )
    result = discriminate(repo, base, candidate, ["test_app.py"])
    assert result.discriminates is True
    assert result.strong_flips == 1


# ---------------------------------------------------------------------------
# Mixed suite: one discriminating test among several no-ops still discriminates,
# and only the genuine one is reported as flipping.
# ---------------------------------------------------------------------------
def test_mixed_suite_reports_only_the_discriminating_test(tmp_path: Path) -> None:
    repo, base, candidate = make_candidate(
        tmp_path,
        base_files={"app.py": "def n():\n    return 1\n"},
        candidate_changes={
            "app.py": "def n():\n    return 99\n",
            "test_app.py": (
                "from app import n\n"
                "def test_noop():\n    assert True\n"
                "def test_real():\n    assert n() == 99\n"
            ),
        },
    )
    result = discriminate(repo, base, candidate, ["test_app.py"])
    assert result.discriminates is True
    assert result.tests_total == 2
    assert result.strong_flips == 1
    assert [f.test_id for f in result.tests_flipping] == ["test_app::test_real"]


# ---------------------------------------------------------------------------
# to_dict emits exactly the canonical advisory shape.
# ---------------------------------------------------------------------------
def test_result_to_dict_shape(tmp_path: Path) -> None:
    repo, base, candidate = make_candidate(
        tmp_path,
        base_files={"app.py": "V=1\n"},
        candidate_changes={
            "app.py": "V=2\n",
            "test_app.py": "import app\ndef test_v():\n    assert app.V == 2\n",
        },
    )
    payload = discriminate(repo, base, candidate, ["test_app.py"]).to_dict()
    assert set(payload) >= {"discriminates", "tests_flipping", "tests_total", "notes"}
    assert payload["discriminates"] is True
    assert isinstance(payload["tests_flipping"], list)
    assert payload["tests_flipping"][0]["kind"] == "assertion_failure"


# ---------------------------------------------------------------------------
# junit parser unit: the one place pytest output shape is interpreted.
# ---------------------------------------------------------------------------
def test_parse_junit_distinguishes_outcomes_and_collection_errors() -> None:
    xml = (
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="pkg.test_m" name="test_pass"/>'
        '<testcase classname="pkg.test_m" name="test_fail">'
        '<failure message="assert False">boom</failure></testcase>'
        '<testcase classname="pkg.test_m" name="test_err">'
        '<error message="failed on setup">boom</error></testcase>'
        '<testcase classname="pkg.test_m" name="test_skip">'
        '<skipped message="nope"/></testcase>'
        '<testcase classname="" name="pkg.test_broken">'
        '<error message="collection failure">ImportError</error></testcase>'
        "</testsuite></testsuites>"
    )
    parsed = parse_junit_xml(xml)
    assert parsed.outcomes["pkg.test_m::test_pass"] == "passed"
    assert parsed.outcomes["pkg.test_m::test_fail"] == "failed"
    assert parsed.outcomes["pkg.test_m::test_err"] == "error"
    assert parsed.outcomes["pkg.test_m::test_skip"] == "skipped"
    assert "pkg.test_broken" in parsed.collection_errors
    # a collection failure is NOT a collected test
    assert parsed.collected == 4
    assert "::pkg.test_broken" not in parsed.outcomes


def test_parse_junit_classifies_assertion_vs_other_failures() -> None:
    # Same shape pytest emits: <failure> for BOTH an AssertionError and an
    # incidental call-phase exception.  Only the former is an assertion.
    xml = (
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="pkg.test_m" name="test_assert">'
        '<failure message="assert 1 == 2">def test_assert():\n'
        '&gt;       assert 1 == 2\nE       assert 1 == 2</failure></testcase>'
        '<testcase classname="pkg.test_m" name="test_import">'
        '<failure message="ModuleNotFoundError: No module named \'x\'">'
        "E       ModuleNotFoundError: No module named 'x'</failure></testcase>"
        '<testcase classname="pkg.test_m" name="test_type">'
        '<failure message="TypeError: bad">E       TypeError: bad</failure></testcase>'
        "</testsuite></testsuites>"
    )
    parsed = parse_junit_xml(xml)
    assert parsed.outcomes["pkg.test_m::test_assert"] == "failed"
    assert parsed.outcomes["pkg.test_m::test_import"] == "failed"
    assert parsed.outcomes["pkg.test_m::test_type"] == "failed"
    # ...but only the real assertion is recorded as an assertion failure.
    assert parsed.assertion_failures == frozenset({"pkg.test_m::test_assert"})


def test_failure_is_assertion_unit() -> None:
    assert failure_is_assertion("assert 1 == 2", "E       assert 1 == 2")
    assert failure_is_assertion("AssertionError: explicit", "E       AssertionError: explicit")
    assert failure_is_assertion("AssertionError: msg\nassert False", "E       assert False")
    assert not failure_is_assertion(
        "ModuleNotFoundError: No module named 'x'",
        "E       ModuleNotFoundError: No module named 'x'",
    )
    assert not failure_is_assertion("TypeError: bad", "E       TypeError: bad")
    # pytest.fail renders as "Failed:" and is deliberately NOT strong.
    assert not failure_is_assertion("Failed: boom", "E       Failed: boom")


# ---------------------------------------------------------------------------
# Sandbox reuse: host-side framing round-trips (no Docker needed).
# ---------------------------------------------------------------------------
def test_extract_sandbox_junit_roundtrip() -> None:
    xml = '<testsuites><testsuite name="pytest"/></testsuites>'
    stdout = f"some noise\nAQ_RD_JUNIT_BEGIN\n{xml}\nAQ_RD_JUNIT_END\ntrailing\n"
    assert extract_sandbox_junit(stdout) == xml
    assert extract_sandbox_junit("no markers here") == ""


# ---------------------------------------------------------------------------
# Live sandbox integration (gated: needs Docker + the sandbox image).
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    os.environ.get("AQ_RD_SANDBOX") != "1" or shutil.which("docker") is None,
    reason="set AQ_RD_SANDBOX=1 with Docker + aq-close-loop-verifier:local to run",
)
def test_discriminate_under_sandbox_runner(tmp_path: Path) -> None:  # pragma: no cover
    repo, base, candidate = make_candidate(
        tmp_path,
        base_files={"app.py": "def greet():\n    return 'old'\n"},
        candidate_changes={
            "app.py": "def greet():\n    return 'new'\n",
            "test_app.py": "from app import greet\ndef test_greet():\n    assert greet() == 'new'\n",
        },
    )
    result = discriminate(
        repo, base, candidate, ["test_app.py"], runner=SandboxedPytestRunner()
    )
    assert result.discriminates is True
    assert result.strong_flips == 1
