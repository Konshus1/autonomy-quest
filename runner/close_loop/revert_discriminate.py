"""Check 1 of the close-the-loop gate: the REVERT-DISCRIMINATE primitive.

Given a candidate change (``base_sha`` -> ``candidate_sha``) and the candidate's
supplied tests, decide whether those tests *discriminate* the change: does at
least one supplied test PASS on the candidate tree and NOT pass when the change
under test is reverted?  (design:
``docs/design/close-loop-grounded-evaluator-gate.md`` -> "Discriminate / revert").

WHAT THIS PRIMITIVE IS -- READ THIS BEFORE USING THE RESULT
-----------------------------------------------------------
This is a **fail-closed NEGATIVE FILTER**, and ``discriminates`` is a
**NECESSARY-BUT-NOT-SUFFICIENT** signal:

* ``discriminates=False`` is **sound and valuable**: if no supplied test changes
  outcome when the change is reverted, then the supplied tests do not test the
  change.  This kills the zero-real-tests forgery, no-op ``assert True`` suites,
  and tests-only changes.  A ``False`` here is a real, trustworthy "this did not
  test the change."

* ``discriminates=True`` is **necessary, not sufficient**.  It proves a test's
  outcome *depends* on the change -- nothing more.  It does **not** prove the
  test asserts anything behaviorally meaningful, and this primitive **cannot**
  prove that, ever.

WHY "TRUE" CANNOT BE MADE SUFFICIENT (the census limit, again)
--------------------------------------------------------------
The candidate authors the test.  A prior version of this primitive tried to
grade a flip as "strong" (a real assertion) vs "weak" (mere exception coupling)
by classifying the on-revert failure.  That is unwinnable and was removed:

* Reading the failure *message* parses candidate-authored text -- a candidate
  writes ``raise Exception("AssertionError: forced")`` on revert and is graded
  "strong" with zero behavioral content.
* Reading the failure *type* does not help either -- a candidate writes
  ``raise AssertionError()`` directly: a genuine ``AssertionError`` by every
  mechanical check, still zero behavioral content.

When the candidate supplies the code that runs its own tests, you can prove a
test **flips** (necessary) but never that it **means** something (sufficient).
So this primitive makes only the claim it can defend -- the flip -- and hands the
meaning judgment to the layer that can make it.

THE TWO-LAYER CONTRACT
----------------------
Sufficiency is the **de-correlated evaluator's** job (design: "Artifact-vs-claim
check").  The evaluator MUST read the flipping test's SOURCE together with the
diff and judge whether the test meaningfully asserts the changed behavior.  This
mechanical primitive is the cheap, sound *pre-filter* in front of that judgment;
it never stands in for it.  ``tests_flipping`` names exactly which tests to read.

THE MECHANIC
------------
1. ``changed = diff(base, candidate)``, split into *test* files and *non-test*
   files by :func:`is_test_path`.  The **non-test** files are "the change under
   test"; the test files are the candidate's supplied tests.
2. Tree A = the candidate tree, verbatim.
   Tree B = the candidate tree with the **non-test** change reverted to its base
   content, while the test files are kept exactly as supplied.  Reverting only
   the change (never the tests) is what lets us observe a test *flip*.
3. Run the supplied test target against both trees.  A test *flips* iff it is
   host-observed to PASS on A and to NOT pass on B (fail / error / vanish behind
   a collection error).  ``discriminates`` iff at least one supplied test flips.

Both observations are made by the harness (we run pytest and read its junit); the
candidate supplies test *code*, never the pass/fail verdict, so the flip itself
is sound.  What the candidate can trivially manufacture is a *meaningless* flip
-- which is exactly why ``True`` is necessary-not-sufficient.

GAMING / EDGE CASES (documented, not silently swallowed)
--------------------------------------------------------
* **Error != pass, ever.** A flip *requires* a host-observed ``passed`` on the
  candidate tree (a junit ``<testcase>`` with no failure/error/skipped child).
  A test that errors or fails on the candidate can never contribute a flip, so
  an error can never masquerade as a pass.
* **A flip is only necessary.** A flip whose on-revert outcome is a failure, an
  error, or a collection error all count equally -- the primitive does NOT rank
  them, because the ranking (assertion vs incidental exception) is candidate-
  spoofable and therefore meaningless as evidence.  ``outcome_reverted`` is
  reported as a plain descriptive fact for the evaluator, carrying no verdict.
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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Protocol, Sequence, runtime_checkable

# ---------------------------------------------------------------------------
# Note codes -- stable, machine-readable reasons surfaced in the result.
# ---------------------------------------------------------------------------
# Always present when discriminates=True: the machine-readable statement of the
# contract, so a downstream consumer cannot mistake a flip for proof of meaning.
NOTE_NECESSARY_NOT_SUFFICIENT = (
    "discriminates_true_is_necessary_not_sufficient__evaluator_must_read_flipping_test_source"
)
NOTE_NO_FLIP = "no_supplied_test_flips_on_revert__change_not_exercised"
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
    their tests never appear in ``outcomes``).

    These are host-observed pytest OUTCOME CATEGORIES only.  The primitive
    deliberately does not parse candidate-authored failure text (message or
    exception type) -- that is spoofable and carries no trustworthy meaning.
    """

    outcomes: Mapping[str, str]
    collection_errors: frozenset[str]
    collected: int
    exit_code: int
    raw: str = ""


@runtime_checkable
class TreeTestRunner(Protocol):
    def run(self, tree: Path, target: Sequence[str]) -> TreeRunOutcome:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# junit parsing -- the one place the pytest output shape is interpreted.
# ---------------------------------------------------------------------------
def parse_junit_xml(text: str) -> TreeRunOutcome:
    """Parse a pytest ``--junitxml`` document into a :class:`TreeRunOutcome`.

    Keying is ``"<classname>::<name>"`` (stable across the two runs because the
    test files are identical between them).  A collection failure is emitted by
    pytest as ``classname="" name="<dotted.module>"`` with an ``<error
    message="collection failure">`` child; those are recorded in
    ``collection_errors`` and never counted as tests.

    Only the outcome CATEGORY (passed/failed/error/skipped/collection-error) is
    read.  The failure's message and type are candidate-authored and are NOT
    interpreted -- see the module docstring on why that classification is
    unwinnable.
    """
    outcomes: dict[str, str] = {}
    collection_errors: set[str] = set()
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
        elif error is not None:
            outcomes[test_id] = "error"
        else:
            outcomes[test_id] = "passed"

    collected = sum(1 for v in outcomes.values())
    return TreeRunOutcome(outcomes, frozenset(collection_errors), collected, exit_code)


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
                proc.returncode, raw,
            )


# ---------------------------------------------------------------------------
# Result shape.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TestFlip:
    """One supplied test whose outcome depends on the change.

    It PASSED on the candidate and did NOT pass on revert.  ``outcome_reverted``
    is the host-observed pytest category on the reverted tree
    (``failed``/``error``/``collection_error``) -- a descriptive fact, NOT a
    quality ranking.  A flip is a necessary signal; whether the test *means*
    anything is the evaluator's judgment on the test SOURCE.
    """

    test_id: str
    outcome_candidate: str  # always "passed"
    outcome_reverted: str   # failed | error | collection_error (descriptive only)

    def to_dict(self) -> dict[str, str]:
        return {
            "test_id": self.test_id,
            "outcome_candidate": self.outcome_candidate,
            "outcome_reverted": self.outcome_reverted,
        }


@dataclass(frozen=True)
class DiscriminateResult:
    """Advisory result of the revert-discriminate filter.

    CONTRACT (see module docstring):
    * ``discriminates=False`` is a sound "the supplied tests do not test the
      change."
    * ``discriminates=True`` is NECESSARY-NOT-SUFFICIENT.  It does not assert the
      flipping tests are meaningful; the de-correlated evaluator must read
      ``tests_flipping``'s SOURCE against the diff to decide that.  When ``True``,
      ``notes`` always includes :data:`NOTE_NECESSARY_NOT_SUFFICIENT`.

    There is deliberately no ``strong``/``weak`` count and no "meaningful"/
    "assertion" label: any such grade would parse candidate-authored text and is
    spoofable, so it is not provided.
    """

    discriminates: bool
    tests_flipping: tuple[TestFlip, ...]
    tests_total: int
    notes: tuple[str, ...]
    changed_non_test_files: tuple[str, ...] = ()
    changed_test_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "discriminates": self.discriminates,
            "necessary_not_sufficient": True,  # the contract, stated in the payload itself
            "tests_flipping": [flip.to_dict() for flip in self.tests_flipping],
            "tests_total": self.tests_total,
            "notes": list(self.notes),
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
    test_classifier=is_test_path,
) -> DiscriminateResult:
    """Run the revert-discriminate NEGATIVE FILTER over the base->candidate change.

    ``test_target`` is the candidate's supplied tests as pytest node arguments
    (e.g. ``["tests/test_feature.py"]``).  ``runner`` defaults to
    :class:`LocalPytestRunner`; production injects :class:`SandboxedPytestRunner`.

    Returns a :class:`DiscriminateResult`.  ``discriminates=True`` is
    NECESSARY-NOT-SUFFICIENT: it means at least one supplied test's outcome
    depends on the change, and it names those tests in ``tests_flipping`` so the
    de-correlated evaluator can read their SOURCE and judge meaning.  It is NOT a
    standalone verdict and does NOT assert the tests are behaviorally meaningful
    -- this primitive cannot prove that (see the module docstring on the census
    limit).  ``discriminates=False`` is the sound direction: the supplied tests do
    not test the change.
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

    flips: list[TestFlip] = []
    for test_id, outcome_a in run_a.outcomes.items():
        if outcome_a != "passed":
            # A flip requires a host-observed pass on the candidate.  An
            # error/failure here is the candidate's own problem and can never be a
            # flip: an error can never masquerade as a pass.
            if outcome_a in {"failed", "error"}:
                notes.append(f"{NOTE_CANDIDATE_TEST_NOT_GREEN}:{test_id}")
            continue

        outcome_b = run_b.outcomes.get(test_id)
        if outcome_b == "passed":
            continue  # outcome unchanged by the revert -> does not test the change
        if outcome_b in {"failed", "error"}:
            # A flip: the outcome depends on the change.  We report the observed
            # category but do NOT rank failure-vs-error (that ranking would parse
            # candidate-authored text and is spoofable / meaningless).
            flips.append(TestFlip(test_id, "passed", outcome_b))
        elif outcome_b == "skipped":
            notes.append(f"{NOTE_SKIPPED_ON_REVERT}:{test_id}")
        elif outcome_b is None:
            # Absent on revert: only a flip if the change made this test's module
            # un-collectable (the change removed something the module needs); a
            # bare absence is inconclusive and not counted.
            if _module_of(test_id) in run_b.collection_errors:
                flips.append(TestFlip(test_id, "passed", "collection_error"))
            else:
                notes.append(f"{NOTE_MISSING_ON_REVERT}:{test_id}")

    discriminates = bool(flips)
    if discriminates:
        # Always emit the contract in the payload so a flip can never be mistaken
        # for proof that the flipping test is behaviorally meaningful.
        notes.insert(0, NOTE_NECESSARY_NOT_SUFFICIENT)
    else:
        notes.insert(0, NOTE_NO_FLIP)

    return DiscriminateResult(
        discriminates=discriminates,
        tests_flipping=tuple(flips),
        tests_total=tests_total,
        notes=tuple(notes),
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
        )


# pytest must not try to collect the dataclasses whose names start with "Test".
TreeTestRunner.__test__ = False  # type: ignore[attr-defined]
TreeRunOutcome.__test__ = False
TestFlip.__test__ = False
