"""Red-first tests for the REVERT-DISCRIMINATE primitive (close-the-loop Check 1).

The primitive is a FAIL-CLOSED NEGATIVE FILTER: ``discriminates=False`` soundly
means "the supplied tests do not test the change" (killing the zero-tests /
no-op-tests / tests-only forgeries), while ``discriminates=True`` is NECESSARY-
NOT-SUFFICIENT -- it proves a test's outcome depends on the change, never that
the test is behaviorally meaningful.  These tests pin both directions AND the
honest contract: a candidate-manufactured flip must register as a flip yet must
NOT be labeled by the output as proof of a real/meaningful test.

All tests build their own throwaway git repos (hermetic; no Docker needed).  The
production sandbox path (``SandboxedPytestRunner``) reuses the existing sandbox
unchanged; its host-side framing is unit-tested here and its live Docker run is
gated behind ``AQ_RD_SANDBOX=1``.
"""
from __future__ import annotations

import json
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
    NOTE_NECESSARY_NOT_SUFFICIENT,
    NOTE_NO_CHANGE_UNDER_TEST,
    NOTE_NO_FLIP,
    NOTE_NO_TESTS_COLLECTED,
    changed_test_targets,
    discriminate,
    extract_sandbox_junit,
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
    assert len(result.tests_flipping) == 1
    flip = result.tests_flipping[0]
    assert flip.test_id == "test_app::test_greet"
    assert flip.outcome_candidate == "passed"
    assert flip.outcome_reverted == "failed"
    # The contract is always in the payload when True.
    assert NOTE_NECESSARY_NOT_SUFFICIENT in result.notes
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
    assert NOTE_NO_FLIP in result.notes


# ---------------------------------------------------------------------------
# RED-FIRST #4: a test that ERRORS (not fails) on revert still registers as a
# flip -- the outcome depends on the change.  Reported as a plain descriptive
# category, NOT ranked below a "failed".
# ---------------------------------------------------------------------------
def test_error_on_revert_registers_as_a_flip(tmp_path: Path) -> None:
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
    result = discriminate(repo, base, candidate, ["test_app.py"])
    assert result.discriminates is True
    assert len(result.tests_flipping) == 1
    flip = result.tests_flipping[0]
    assert flip.outcome_candidate == "passed"
    assert flip.outcome_reverted in {"error", "collection_error"}
    assert NOTE_NECESSARY_NOT_SUFFICIENT in result.notes


# ---------------------------------------------------------------------------
# RED-FIRST (the confirmed spoof + the deeper truth): a candidate-manufactured
# flip with zero behavioral content MUST NOT be labeled as proof of a real test.
# It may register as a flip (necessary signal); the output must present it as
# necessary-not-sufficient and must carry no "strong"/"assertion"/"meaningful"
# grade -- because such a grade parses candidate-authored text and is spoofable.
# ---------------------------------------------------------------------------
_SPOOF_BODIES = {
    # Raises an exception whose MESSAGE mimics an assertion (defeats message-parsing).
    "message_spoof": (
        "def test_x():\n"
        "    try:\n"
        "        import _marker\n"
        "    except ImportError:\n"
        "        raise Exception('AssertionError: forced by attacker')\n"
        "    assert True\n"  # asserts nothing behavioral
    ),
    # Raises a genuine AssertionError (defeats type-checking too): the deeper truth.
    "bare_assertion_error": (
        "def test_x():\n"
        "    try:\n"
        "        import _marker\n"
        "    except ImportError:\n"
        "        raise AssertionError()\n"
        "    assert True\n"
    ),
    # Pure import-coupling: body import raises on revert, asserts nothing.
    "import_coupling": (
        "def test_x():\n"
        "    import _marker\n"
        "    assert True\n"
    ),
}


@pytest.mark.parametrize("label", sorted(_SPOOF_BODIES))
def test_manufactured_flip_is_not_labeled_meaningful(tmp_path: Path, label: str) -> None:
    repo, base, candidate = make_candidate(
        tmp_path,
        base_files={"README.md": "base\n"},
        candidate_changes={
            # The ENTIRE change is a throwaway marker file (non-test); the test
            # only couples to its existence and asserts nothing behavioral.
            "_marker.py": "MARKER = True\n",
            "test_app.py": _SPOOF_BODIES[label],
        },
    )
    result = discriminate(repo, base, candidate, ["test_app.py"])

    # It may register as a flip -- that is the necessary signal, and it is honest.
    assert result.discriminates is True

    # But the OUTPUT must not present it as proof of a real/meaningful test.
    payload = result.to_dict()
    blob = json.dumps(payload)
    assert "strong_flips" not in payload
    assert "assertion_failure" not in blob
    assert "meaningful" not in blob
    for flip in payload["tests_flipping"]:
        assert "kind" not in flip  # no strong/weak quality grade
    # The contract is stated in the payload itself.
    assert payload["necessary_not_sufficient"] is True
    assert NOTE_NECESSARY_NOT_SUFFICIENT in payload["notes"]


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
    # gone.  On revert the deleted func returns -> test flips.
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
    assert len(result.tests_flipping) == 1
    assert result.tests_flipping[0].outcome_reverted == "failed"


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
    assert [f.test_id for f in result.tests_flipping] == ["test_app::test_real"]


# ---------------------------------------------------------------------------
# to_dict emits the canonical advisory shape AND the necessary-not-sufficient
# contract, and carries no strong/weak/meaningful grade.
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
    assert set(payload) >= {
        "discriminates", "necessary_not_sufficient", "tests_flipping",
        "tests_total", "notes",
    }
    assert payload["discriminates"] is True
    assert payload["necessary_not_sufficient"] is True
    assert "strong_flips" not in payload and "weak_flips" not in payload
    flip = payload["tests_flipping"][0]
    assert set(flip) == {"test_id", "outcome_candidate", "outcome_reverted"}
    assert "kind" not in flip


# ---------------------------------------------------------------------------
# junit parser unit: the one place pytest output shape is interpreted.  Only the
# outcome CATEGORY is read; failure text/type is deliberately not classified.
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
    # the parser carries no assertion/quality classification
    assert not hasattr(parsed, "assertion_failures")


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
    assert len(result.tests_flipping) == 1
