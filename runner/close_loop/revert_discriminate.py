"""Check 1 of the close-the-loop gate: the REVERT-DISCRIMINATE primitive.

Given a candidate change (``base_sha`` -> ``candidate_sha``) and the candidate's
supplied tests, decide whether those tests genuinely *discriminate* the change:
do they PASS on the candidate tree and FAIL when the change under test is
reverted?  A suite that passes with and without the change does not test the
change; a zero-real-tests forgery discriminates trivially-not.  This is the
red-first discipline made mechanical (design:
``docs/design/close-loop-grounded-evaluator-gate.md`` -> "Discriminate / revert").

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
This module reads the *discriminate structure* (fails-on-revert), which is far
harder to forge than a self-reported pass count.  It is **advisory input to the
evaluator**, never a standalone verdict.

It does not provide the powerlessness guarantee itself.  Test *execution* is
delegated to an injected :class:`TreeTestRunner`.  The default
:class:`LocalPytestRunner` runs pytest as an ordinary host subprocess (used by
the unit tests, where containment is not the property under test); the
production caller injects :class:`SandboxedPytestRunner`, which reuses
``runner.close_loop.sandbox.run_in_sandbox`` **unchanged** so the tests run under
the existing Docker powerlessness boundary.  Running tests is already contained;
this primitive only reads the structure of the two contained runs.

THE MECHANIC
------------
1. ``changed = diff(base, candidate)``, split into *test* files and *non-test*
   files by :func:`is_test_path`.  The **non-test** files are "the change under
   test"; the test files are the candidate's supplied tests.
2. Tree A = the candidate tree, verbatim.
   Tree B = the candidate tree with the **non-test** change reverted to its base
   content, while the test files are kept exactly as supplied.  Reverting only
   the change (never the tests) is what lets us observe a test *flip*.
3. Run the supplied test target against both trees, collecting per-test
   outcomes.  The change discriminates iff at least one supplied test PASSES on
   A and does NOT pass on B.

GAMING / EDGE CASES (documented, not silently swallowed)
--------------------------------------------------------
* **Error != pass, ever.** A flip *requires* a genuine ``passed`` on the
  candidate tree (a junit ``<testcase>`` with no failure/error/skipped child).
  A test that errors or fails on the candidate can never contribute a flip, so
  an error can never masquerade as a pass.
* **STRONG vs WEAK flips, and why ``<failure> != AssertionError``.** pytest
  emits ``<failure>`` for ANY uncaught call-phase exception -- a body-level
  ``import`` that raises ``ModuleNotFoundError``, a ``TypeError``, a ``NameError``
  -- not only for real assertions.  Treating every ``<failure>`` as a "strong
  assertion" is a false positive: a zero-work candidate can add a throwaway file
  as "the change" plus a test whose body only ``import``s it and asserts nothing;
  on revert the import raises ``<failure>`` though nothing was behaviorally
  tested.  So a flip is **strong** (``kind="assertion_failure"``) only when the
  ``<failure>`` is a genuine ``AssertionError`` (see :func:`failure_is_assertion`);
  every other on-revert failure/error -- non-assertion call-phase exceptions,
  setup/teardown errors, and collection errors -- is a **weak** flip
  (``kind="error"``).  ``strong_flips`` therefore means exactly "a real assertion
  failed when the change was reverted."
* **Error-on-revert / import-coupling is WEAK, not sufficient by default.**
  ``require_clean_failure`` defaults to ``True`` so ``discriminates`` requires a
  strong flip.  A weak flip proves only that the reverted tree cannot *run* the
  test, not that it *asserts* anything; the consuming evaluator MUST keep the
  strict default and treat weak flips as non-grounding.
* **Change hidden inside a test file.**  If the candidate puts real logic in a
  file classified as a test, that file is never reverted, so nothing flips and
  the candidate simply *fails* this check.  Misclassifying change-as-test can
  only make a candidate fail, never falsely pass -- fail-closed by construction.
* **Change touches no non-test files.**  Reverting nothing yields Tree B == Tree
  A; nothing can flip; ``discriminates=False`` with an explanatory note.  A
  tests-only change has no change-under-test to discriminate.
* **Punted (documented, not handled):** flakiness and order-dependence.  A test
  whose outcome is non-deterministic can flip or fail to flip by luck; we run
  each tree once and report what we observed.  Side-effect coupling between
  tests (test X only passes because test Y ran first) is likewise not detected.
  These are evaluator-weighted, not gate-fatal, because the check is advisory.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Mapping, Protocol, Sequence, runtime_checkable

# ---------------------------------------------------------------------------
# Note codes -- stable, machine-readable reasons surfaced in the result.
# ---------------------------------------------------------------------------
NOTE_NO_CHANGE_UNDER_TEST = "change_touches_no_non_test_files"
NOTE_NO_TESTS_COLLECTED = "no_supplied_tests_collected"
NOTE_CANDIDATE_COLLECTION_FAILED = "candidate_tests_failed_to_collect"
NOTE_CANDIDATE_TEST_NOT_GREEN = "test_not_passing_on_candidate_excluded"
NOTE_SKIPPED_ON_REVERT = "test_skipped_on_revert_not_counted"
NOTE_MISSING_ON_REVERT = "test_absent_on_revert_without_collection_error_not_counted"
NOTE_REVERT_CONSTRUCTION_FAILED = "revert_tree_construction_failed"


class DiscriminateError(RuntimeError):
    """A fail-closed setup failure (bad repo, unresolvable commit, git error)."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


# ---------------------------------------------------------------------------
# Test-file classification.  The NON-test changed files are the change under
# test; they get reverted.  Test files are held constant.
# ---------------------------------------------------------------------------
def is_test_path(path: str) -> bool:
    """Heuristic: is ``path`` a test/support file rather than change-under-test?"""
    p = PurePosixPath(path)
    name = p.name
    if name == "conftest.py":
        return True
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    # any parent directory literally named tests/ or test/
    return any(part in {"tests", "test"} for part in p.parts[:-1])


# ---------------------------------------------------------------------------
# Per-tree run outcome + runner protocol.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TreeRunOutcome:
    """Structured result of running the supplied tests against one tree.

    ``outcomes`` maps ``"<classname>::<name>"`` -> one of
    ``passed|failed|error|skipped``.  ``collection_errors`` is the set of dotted
    module names pytest could not import/collect (these abort the session, so
    their tests never appear in ``outcomes``).  ``assertion_failures`` is the
    subset of ``failed`` test ids whose ``<failure>`` is a genuine
    ``AssertionError``-class failure (see :func:`failure_is_assertion`) -- as
    opposed to some other uncaught call-phase exception (ImportError, TypeError,
    ...), which pytest ALSO reports as ``<failure>`` but which is not a real
    assertion and must not count as strong red-first evidence.
    """

    outcomes: Mapping[str, str]
    collection_errors: frozenset[str]
    collected: int
    exit_code: int
    raw: str = ""
    assertion_failures: frozenset[str] = frozenset()


@runtime_checkable
class TreeTestRunner(Protocol):
    def run(self, tree: Path, target: Sequence[str]) -> TreeRunOutcome:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# junit parsing -- the one place the pytest output shape is interpreted.
# ---------------------------------------------------------------------------
def failure_is_assertion(message: str, traceback_text: str) -> bool:
    """Is a pytest ``<failure>`` a genuine ``AssertionError``-class failure?

    pytest emits ``<failure>`` for ANY uncaught call-phase exception, not only
    ``AssertionError`` -- a body-level ``import`` that raises ``ModuleNotFoundError``,
    a ``TypeError``, a ``NameError`` all land here too.  Only a real assertion is
    strong red-first evidence; everything else is incidental exception coupling
    and must be treated as a WEAK flip.

    pytest (>=8) does not populate the junit ``type`` attribute, so we read the
    rendered error text.  A genuine assertion renders as either the rewritten
    expression (``assert ...``) or the exception class (``AssertionError...``);
    every other exception renders as ``<OtherClass>: ...``.  ``pytest.fail(...)``
    renders as ``Failed: ...`` and is deliberately classified WEAK: it is an
    author-chosen failure, not an ``AssertionError``, so the ``assertion_failure``
    label stays literally honest.  A legitimate ``pytest.fail`` test still shows
    up as a weak flip for the evaluator to weigh; it is simply not "strong".
    """
    # The junit ``message`` attribute's first line is the exception headline as
    # pytest renders it: ``assert <expr>`` / ``AssertionError: ...`` for a real
    # assertion, ``<OtherClass>: ...`` for anything else.  (The traceback's LAST
    # ``E `` line is unreliable -- an assertion with an explanation appends
    # ``E   +  where ...`` detail lines after the headline -- so we take the
    # FIRST rendered line, preferring the message and falling back to the first
    # ``E `` traceback line.)
    signal = (message or "").strip()
    if not signal:
        for line in (traceback_text or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("E "):
                signal = stripped[1:].strip()
                break
    headline = signal.splitlines()[0].strip() if signal else ""
    return headline.startswith("assert") or headline.startswith("AssertionError")


def parse_junit_xml(text: str) -> TreeRunOutcome:
    """Parse a pytest ``--junitxml`` document into a :class:`TreeRunOutcome`.

    Keying is ``"<classname>::<name>"`` (stable across the two runs because the
    test files are identical between them).  A collection failure is emitted by
    pytest as ``classname="" name="<dotted.module>"`` with an ``<error
    message="collection failure">`` child; those are recorded in
    ``collection_errors`` and never counted as tests.
    """
    outcomes: dict[str, str] = {}
    collection_errors: set[str] = set()
    assertion_failures: set[str] = set()
    exit_code = 0  # not carried by junit; the runner overrides this
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise DiscriminateError("junit_unparseable", f"could not parse junit xml: {exc}") from exc

    for testcase in root.iter("testcase"):
        classname = testcase.get("classname", "") or ""
        name = testcase.get("name", "") or ""
        error = testcase.find("error")
        failure = testcase.find("failure")
        skipped = testcase.find("skipped")

        if error is not None and (error.get("message", "").startswith("collection failure") or not classname):
            # A collection failure -> record the module, not a test.
            collection_errors.add(name)
            continue

        test_id = f"{classname}::{name}"
        if skipped is not None:
            outcomes[test_id] = "skipped"
        elif failure is not None:
            outcomes[test_id] = "failed"
            if failure_is_assertion(failure.get("message", "") or "", failure.text or ""):
                assertion_failures.add(test_id)
        elif error is not None:
            outcomes[test_id] = "error"
        else:
            outcomes[test_id] = "passed"

    collected = sum(1 for v in outcomes.values())
    return TreeRunOutcome(
        outcomes, frozenset(collection_errors), collected, exit_code,
        assertion_failures=frozenset(assertion_failures),
    )


def _module_of(test_id: str) -> str:
    return test_id.split("::", 1)[0]


# ---------------------------------------------------------------------------
# Default host runner (NOT sandboxed) -- used by the unit tests.
# ---------------------------------------------------------------------------
_CLEAN_PYTEST_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", "/tmp"),
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    # Neutralize inherited git config so a candidate worktree behaves predictably.
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


@dataclass(frozen=True)
class LocalPytestRunner:
    """Run pytest in a tree as an ordinary host subprocess and read junit.

    This provides NO containment; it exists for the structure tests and for
    trusted local use.  Production injects :class:`SandboxedPytestRunner`.
    """

    python: str = sys.executable
    timeout_seconds: int = 300

    def run(self, tree: Path, target: Sequence[str]) -> TreeRunOutcome:
        tree = Path(tree)
        with tempfile.TemporaryDirectory(prefix="aq-rd-junit-") as tmp:
            xml_path = Path(tmp) / "report.xml"
            argv = [
                self.python, "-m", "pytest",
                "-p", "no:cacheprovider",
                "-o", "addopts=",  # ignore the tree's own addopts; run exactly the target
                "--junitxml", str(xml_path),
                "-q", *target,
            ]
            try:
                proc = subprocess.run(
                    argv, cwd=str(tree), capture_output=True, text=True,
                    timeout=self.timeout_seconds, env=dict(_CLEAN_PYTEST_ENV), check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise DiscriminateError("test_timeout", f"pytest exceeded {self.timeout_seconds}s") from exc
            raw = (proc.stdout or "") + (proc.stderr or "")
            if not xml_path.exists():
                # No junit at all: usually pytest usage error or a total crash.
                return TreeRunOutcome({}, frozenset(), 0, proc.returncode, raw)
            parsed = parse_junit_xml(xml_path.read_text(encoding="utf-8", errors="replace"))
            return TreeRunOutcome(
                parsed.outcomes, parsed.collection_errors, parsed.collected,
                proc.returncode, raw, assertion_failures=parsed.assertion_failures,
            )


# ---------------------------------------------------------------------------
# Result shape.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TestFlip:
    """One supplied test that passed on the candidate and did not on revert."""

    test_id: str
    outcome_candidate: str  # always "passed"
    outcome_reverted: str   # failed | error | collection_error
    kind: str               # "assertion_failure" (strong) | "error" (weak)

    def to_dict(self) -> dict[str, str]:
        return {
            "test_id": self.test_id,
            "outcome_candidate": self.outcome_candidate,
            "outcome_reverted": self.outcome_reverted,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class DiscriminateResult:
    discriminates: bool
    tests_flipping: tuple[TestFlip, ...]
    tests_total: int
    notes: tuple[str, ...]
    strong_flips: int = 0
    weak_flips: int = 0
    changed_non_test_files: tuple[str, ...] = ()
    changed_test_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "discriminates": self.discriminates,
            "tests_flipping": [flip.to_dict() for flip in self.tests_flipping],
            "tests_total": self.tests_total,
            "notes": list(self.notes),
            "strong_flips": self.strong_flips,
            "weak_flips": self.weak_flips,
            "changed_non_test_files": list(self.changed_non_test_files),
            "changed_test_files": list(self.changed_test_files),
        }


# ---------------------------------------------------------------------------
# git plumbing.
# ---------------------------------------------------------------------------
def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(repo), capture_output=True, text=True,
            timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DiscriminateError("git_unavailable", f"git {' '.join(args)} failed: {exc}") from exc
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise DiscriminateError("git_command_failed", f"git {' '.join(args)} failed: {detail}")
    return proc


def _resolve_commit(repo: Path, ref: str) -> str:
    resolved = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()
    if len(resolved) != 40 or any(c not in "0123456789abcdef" for c in resolved):
        raise DiscriminateError("commit_unresolved", f"not a canonical commit: {ref!r}")
    return resolved


def _changed_files(repo: Path, base: str, candidate: str) -> tuple[str, ...]:
    # --no-renames so a rename is a delete(old)+add(new): each side reverts cleanly.
    proc = _git(repo, "diff", "--name-only", "--no-renames", base, candidate)
    return tuple(line for line in proc.stdout.splitlines() if line)


def _blob_exists(repo: Path, ref: str, path: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{ref}:{path}", check=False).returncode == 0


def _add_worktree(repo: Path, sha: str, dest: Path) -> None:
    _git(repo, "worktree", "add", "--detach", str(dest), sha)


def _remove_worktree(repo: Path, dest: Path) -> None:
    _git(repo, "worktree", "remove", "--force", str(dest), check=False)


def _build_reverted_tree(
    repo: Path, base: str, worktree: Path, non_test_files: Sequence[str]
) -> None:
    """Revert ONLY the change-under-test in an already-materialized candidate tree.

    For each non-test changed path: restore its base content if base has it
    (covers modify + candidate-deleted); otherwise remove it (candidate-added).
    Test files are untouched, so a flip remains observable.
    """
    for path in non_test_files:
        if _blob_exists(repo, base, path):
            # checkout updates worktree+index for this path only; HEAD is unmoved.
            _git(worktree, "checkout", base, "--", path)
        else:
            target = worktree / path
            if target.exists() or target.is_symlink():
                target.unlink()


# ---------------------------------------------------------------------------
# The primitive.
# ---------------------------------------------------------------------------
def changed_test_targets(repo: str | Path, base_sha: str, candidate_sha: str) -> tuple[str, ...]:
    """Convenience: the test files the candidate changed, as a default target."""
    root = Path(repo).resolve(strict=True)
    base = _resolve_commit(root, base_sha)
    candidate = _resolve_commit(root, candidate_sha)
    return tuple(f for f in _changed_files(root, base, candidate) if is_test_path(f))


def discriminate(
    repo: str | Path,
    base_sha: str,
    candidate_sha: str,
    test_target: Sequence[str],
    *,
    runner: TreeTestRunner | None = None,
    require_clean_failure: bool = True,
    test_classifier=is_test_path,
) -> DiscriminateResult:
    """Decide whether ``test_target`` discriminates the base->candidate change.

    ``test_target`` is the candidate's supplied tests as pytest node arguments
    (e.g. ``["tests/test_feature.py"]``).  ``runner`` defaults to
    :class:`LocalPytestRunner`; production injects :class:`SandboxedPytestRunner`.

    ``require_clean_failure`` (default ``True``) makes ``discriminates`` require
    at least one **strong** flip -- a genuine ``AssertionError`` that fired when
    the change was reverted.  Set it ``False`` to also let **weak** flips (an
    error/import-coupling on revert) satisfy ``discriminates``.

    STRONG vs WEAK, and why the default is strict
    ---------------------------------------------
    A weak flip proves only that the reverted tree *cannot run* the test (the
    change removed a symbol it references) -- it does not prove the test makes a
    behaviorally meaningful assertion.  A candidate can manufacture a weak flip
    with zero real work: add a throwaway file as "the change" and a test whose
    body merely ``import``s it and asserts nothing.  Reverting removes the file,
    the body import raises, pytest records a ``<failure>`` -- but nothing was
    behaviorally tested.  So the default is strict, and **an evaluator consuming
    this result MUST keep ``require_clean_failure=True`` and MUST treat weak
    flips as NON-grounding.**  ``strong_flips`` genuinely means "a real assertion
    failed on revert"; ``weak_flips`` is advisory context only.  Whether a strong
    assertion is *itself* behaviorally meaningful is the evaluator's
    artifact-vs-claim job, not this primitive's; this primitive's job is only to
    classify assertion-vs-not correctly.

    Returns a :class:`DiscriminateResult` (advisory input, not a verdict).
    """
    root = Path(repo).resolve(strict=True)
    base = _resolve_commit(root, base_sha)
    candidate = _resolve_commit(root, candidate_sha)
    active_runner = runner if runner is not None else LocalPytestRunner()

    changed = _changed_files(root, base, candidate)
    non_test = tuple(f for f in changed if not test_classifier(f))
    test_changed = tuple(f for f in changed if test_classifier(f))

    if not non_test:
        # Reverting nothing => Tree B == Tree A => no flip is possible.
        return DiscriminateResult(
            discriminates=False, tests_flipping=(), tests_total=0,
            notes=(NOTE_NO_CHANGE_UNDER_TEST,),
            changed_non_test_files=non_test, changed_test_files=test_changed,
        )

    target = tuple(test_target)
    parent = Path(tempfile.mkdtemp(prefix="aq-rd-"))
    tree_a = parent / "candidate"
    tree_b = parent / "reverted"
    notes: list[str] = []
    try:
        _add_worktree(root, candidate, tree_a)
        _add_worktree(root, candidate, tree_b)
        try:
            _build_reverted_tree(root, base, tree_b, non_test)
        except DiscriminateError:
            return DiscriminateResult(
                discriminates=False, tests_flipping=(), tests_total=0,
                notes=(NOTE_REVERT_CONSTRUCTION_FAILED,),
                changed_non_test_files=non_test, changed_test_files=test_changed,
            )

        run_a = active_runner.run(tree_a, target)
        run_b = active_runner.run(tree_b, target)
    finally:
        _remove_worktree(root, tree_a)
        _remove_worktree(root, tree_b)
        import shutil

        shutil.rmtree(parent, ignore_errors=True)

    tests_total = run_a.collected

    if tests_total == 0:
        # Zero real tests collected on the candidate -> the forged-census case.
        code = (
            NOTE_CANDIDATE_COLLECTION_FAILED
            if run_a.collection_errors
            else NOTE_NO_TESTS_COLLECTED
        )
        return DiscriminateResult(
            discriminates=False, tests_flipping=(), tests_total=0, notes=(code,),
            changed_non_test_files=non_test, changed_test_files=test_changed,
        )

    strong: list[TestFlip] = []
    weak: list[TestFlip] = []
    for test_id, outcome_a in run_a.outcomes.items():
        if outcome_a != "passed":
            # A flip requires a genuine pass on the candidate.  An error/failure
            # here is the candidate's own problem and can never be a flip: an
            # error can never masquerade as a pass.
            if outcome_a in {"failed", "error"}:
                notes.append(f"{NOTE_CANDIDATE_TEST_NOT_GREEN}:{test_id}")
            continue

        outcome_b = run_b.outcomes.get(test_id)
        if outcome_b == "passed":
            continue  # no-op test w.r.t. the change
        if outcome_b == "failed":
            # A pytest <failure> is only STRONG when it is a genuine
            # AssertionError.  Any other call-phase exception on revert
            # (ImportError from a body-level import, TypeError, NameError, ...)
            # is import/exception coupling, not a behavioral assertion, so it is
            # a WEAK flip -- otherwise a zero-work candidate that merely imports
            # its throwaway change file inside a test would forge a strong flip.
            if test_id in run_b.assertion_failures:
                strong.append(TestFlip(test_id, "passed", "failed", "assertion_failure"))
            else:
                weak.append(TestFlip(test_id, "passed", "failed_non_assertion", "error"))
        elif outcome_b == "error":
            weak.append(TestFlip(test_id, "passed", "error", "error"))
        elif outcome_b == "skipped":
            notes.append(f"{NOTE_SKIPPED_ON_REVERT}:{test_id}")
        elif outcome_b is None:
            # Missing on revert: a collection error in this test's module (the
            # change removed something the module imports) is a real, if weak,
            # discriminating signal; a bare absence is not counted.
            if _module_of(test_id) in run_b.collection_errors:
                weak.append(TestFlip(test_id, "passed", "collection_error", "error"))
            else:
                notes.append(f"{NOTE_MISSING_ON_REVERT}:{test_id}")

    flips = tuple(strong) + tuple(weak)
    discriminates = bool(strong) or (not require_clean_failure and bool(weak))

    return DiscriminateResult(
        discriminates=discriminates,
        tests_flipping=flips,
        tests_total=tests_total,
        notes=tuple(notes),
        strong_flips=len(strong),
        weak_flips=len(weak),
        changed_non_test_files=non_test,
        changed_test_files=test_changed,
    )


# ---------------------------------------------------------------------------
# Production execution path: reuse the existing powerlessness sandbox unchanged.
# ---------------------------------------------------------------------------
_SANDBOX_JUNIT_BEGIN = "AQ_RD_JUNIT_BEGIN"
_SANDBOX_JUNIT_END = "AQ_RD_JUNIT_END"

# Program run *inside* the sandbox.  It runs pytest to a junit file on the
# writable tmpfs, then frames the XML on stdout so the trusted host can parse the
# structure without any writable bind back to the host.
_SANDBOX_PROGRAM = (
    "import sys, pytest\n"
    "xml='/tmp/aq_rd_report.xml'\n"
    "pytest.main(['-p','no:cacheprovider','-o','addopts=','--junitxml',xml,'-q',*sys.argv[1:]])\n"
    "sys.stdout.write('\\n" + _SANDBOX_JUNIT_BEGIN + "\\n')\n"
    "try:\n"
    "    sys.stdout.write(open(xml,encoding='utf-8',errors='replace').read())\n"
    "except OSError:\n"
    "    pass\n"
    "sys.stdout.write('\\n" + _SANDBOX_JUNIT_END + "\\n')\n"
    "raise SystemExit(0)\n"
)


def extract_sandbox_junit(stdout: str) -> str:
    """Pull the framed junit XML back out of the sandbox's stdout (host-side)."""
    begin = stdout.find(_SANDBOX_JUNIT_BEGIN)
    end = stdout.find(_SANDBOX_JUNIT_END)
    if begin == -1 or end == -1 or end < begin:
        return ""
    return stdout[begin + len(_SANDBOX_JUNIT_BEGIN):end].strip()


@dataclass(frozen=True)
class SandboxedPytestRunner:
    """Run the supplied tests inside ``sandbox.run_in_sandbox`` (Docker, powerless).

    Reuses the existing close-loop sandbox verbatim: the tree is mounted
    read-only, there is no network, no capabilities, no writable host mount, and
    junit is returned framed on stdout (never via a writable bind).  This is the
    production execution path; it requires Docker and the sandbox image.
    """

    image: str = "aq-close-loop-verifier:local"
    python: str = "python3"

    def run(self, tree: Path, target: Sequence[str]) -> TreeRunOutcome:
        # Imported lazily so the pure structure code (and its tests) never depend
        # on Docker being present.
        from .sandbox import SandboxLimits, run_in_sandbox

        command = [self.python, "-c", _SANDBOX_PROGRAM, *target]
        result = run_in_sandbox(tree, command, image=self.image, limits=SandboxLimits())
        junit = extract_sandbox_junit(result.stdout)
        if not junit:
            return TreeRunOutcome({}, frozenset(), 0, result.exit_code, result.stdout + result.stderr)
        parsed = parse_junit_xml(junit)
        return TreeRunOutcome(
            parsed.outcomes, parsed.collection_errors, parsed.collected,
            result.exit_code, result.stdout + result.stderr,
            assertion_failures=parsed.assertion_failures,
        )


# pytest must not try to collect the dataclasses whose names start with "Test".
TreeTestRunner.__test__ = False  # type: ignore[attr-defined]
TreeRunOutcome.__test__ = False
TestFlip.__test__ = False
