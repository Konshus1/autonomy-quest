"""Pure fail-closed closure checks for independently observed verifier results.

This module does not execute candidate code.  It judges results emitted by a
separate hermetic runner against a :class:`VerifierPolicy` loaded from a trusted
path outside the candidate checkout.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import fnmatch
import hashlib
import re
from typing import Iterable, Mapping, Sequence

try:
    from .policy import PolicyError, VerifierPolicy, canonical_json
except (ImportError, ValueError):  # direct execution in minimal verifier image
    import importlib.util
    import sys
    from pathlib import Path as _Path
    _spec = importlib.util.spec_from_file_location(
        "aq_close_loop_policy", _Path(__file__).with_name("policy.py")
    )
    if _spec is None or _spec.loader is None:
        raise
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _module
    _spec.loader.exec_module(_module)
    PolicyError = _module.PolicyError
    VerifierPolicy = _module.VerifierPolicy
    canonical_json = _module.canonical_json

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PASS = "passed"
_NONPASS = frozenset({"failed", "skipped", "xfailed", "error", "timeout", "missing"})


def _nonempty(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ResultBinding:
    """The exact immutable objects against which one check was observed."""

    base_sha: str
    candidate_sha: str
    tree_sha: str
    manifest_digest: str

    def __post_init__(self) -> None:
        if not _HEX40.fullmatch(self.base_sha):
            raise ValueError("base_sha must be lowercase 40-hex")
        if not _HEX40.fullmatch(self.candidate_sha):
            raise ValueError("candidate_sha must be lowercase 40-hex")
        if not _HEX40.fullmatch(self.tree_sha):
            raise ValueError("tree_sha must be lowercase 40-hex")
        if not _HEX64.fullmatch(self.manifest_digest):
            raise ValueError("manifest_digest must be lowercase sha256")


@dataclass(frozen=True, slots=True)
class ObservedResult:
    """One untrusted parsed observation from the sandbox.

    Status is deliberately checked by :func:`verify_closure`, rather than the
    constructor, so unknown/malformed status values produce a durable HOLD.
    """

    check_id: str
    status: str
    exit_code: int | None
    stdout_digest: str
    stderr_digest: str
    binding: ResultBinding

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ObservedResult":
        """Strictly parse the wire shape.  Callers should turn errors into HOLD."""
        required = {
            "check_id", "status", "exit_code", "stdout_digest", "stderr_digest",
            "base_sha", "candidate_sha", "tree_sha", "manifest_digest",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("observed result has missing or unknown fields")
        check_id, status = value["check_id"], value["status"]
        if not isinstance(check_id, str) or not isinstance(status, str):
            raise ValueError("observed check_id and status must be strings")
        exit_code = value["exit_code"]
        if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
            raise ValueError("observed exit_code must be an integer or null")
        stdout_digest, stderr_digest = value["stdout_digest"], value["stderr_digest"]
        if not isinstance(stdout_digest, str) or not isinstance(stderr_digest, str):
            raise ValueError("observed output digests must be strings")
        return cls(
            check_id, status, exit_code, stdout_digest, stderr_digest,
            ResultBinding(
                str(value["base_sha"]), str(value["candidate_sha"]),
                str(value["tree_sha"]), str(value["manifest_digest"]),
            ),
        )


@dataclass(frozen=True, slots=True)
class VerificationDecision:
    verdict: str
    reason_codes: tuple[str, ...]
    binding: ResultBinding
    required_ids: tuple[str, ...]
    observed_ids: tuple[str, ...]
    changed_paths: tuple[str, ...]
    evidence_digest: str

    @property
    def approved(self) -> bool:
        return self.verdict == "approve_local"

    @property
    def held(self) -> bool:
        return not self.approved


def _path_is_safe(path: object) -> bool:
    if not isinstance(path, str) or not path or "\x00" in path or "\\" in path:
        return False
    parts = path.split("/")
    return not path.startswith("/") and all(part not in {"", ".", ".."} for part in parts)


def _matches(path: str, pattern: str) -> bool:
    # Pure slash-separated repository paths; fnmatch is deterministic here.
    return fnmatch.fnmatchcase(path, pattern)


def verify_closure(
    policy: VerifierPolicy,
    *,
    base_sha: str,
    candidate_sha: str,
    tree_sha: str,
    changed_paths: Sequence[str],
    expected_changed_paths: Sequence[str],
    observed_results: Iterable[ObservedResult | Mapping[str, object]],
    manifest_digest: str | None = None,
    protected_paths: Sequence[str] = (),
) -> VerificationDecision:
    """Return approval only for an exact, bound, non-vacuous result closure.

    ``expected_changed_paths`` is a trusted path-pattern coverage contract: each
    expected pattern must match at least one committed changed path, and every
    changed path must be covered by one expected pattern.  Candidate copies or
    edits of a manifest have no authority; ``policy`` must already have been
    loaded by the supervisor from its external trusted path.
    """
    supplied_digest = manifest_digest or policy.digest
    try:
        binding = ResultBinding(base_sha, candidate_sha, tree_sha, supplied_digest)
    except (TypeError, ValueError):
        # Preserve a fully typed decision even for malformed invocation identities.
        binding = ResultBinding(policy.base_sha, policy.candidate_sha, "0" * 40, policy.digest)
        reasons = ["malformed_binding"]
    else:
        reasons = []

    changed = tuple(changed_paths)
    expected = tuple(expected_changed_paths)
    protected = tuple(protected_paths)
    if binding.base_sha != policy.base_sha:
        reasons.append("base_sha_mismatch")
    if binding.candidate_sha != policy.candidate_sha:
        reasons.append("candidate_sha_mismatch")
    if binding.manifest_digest != policy.digest:
        reasons.append("test_manifest_mismatch")

    if not changed:
        reasons.append("candidate_empty")
    if not expected:
        reasons.append("changed_path_policy_empty")
    if any(not _path_is_safe(path) for path in changed):
        reasons.append("changed_path_malformed")
    if any(not _path_is_safe(pattern) for pattern in expected):
        reasons.append("changed_path_policy_malformed")
    if changed and expected and all(_path_is_safe(x) for x in (*changed, *expected)):
        if any(not any(_matches(path, pattern) for path in changed) for pattern in expected):
            reasons.append("expected_changed_path_missing")
        if any(not any(_matches(path, pattern) for pattern in expected) for path in changed):
            reasons.append("changed_path_uncovered")
    if any(
        _path_is_safe(path) and _path_is_safe(pattern) and _matches(path, pattern)
        for path in changed for pattern in protected
    ):
        reasons.append("protected_path")

    parsed: list[ObservedResult] = []
    malformed_count = 0
    try:
        iterator = iter(observed_results)
    except TypeError:
        iterator = iter(())
        malformed_count += 1
    while True:
        try:
            value = next(iterator)
        except StopIteration:
            break
        except Exception:
            malformed_count += 1
            break
        try:
            item = value if isinstance(value, ObservedResult) else ObservedResult.from_mapping(value)
            if not isinstance(item, ObservedResult):
                raise ValueError("not an observed result")
            parsed.append(item)
        except (TypeError, ValueError):
            malformed_count += 1
    if malformed_count:
        reasons.append("observed_result_malformed")

    required = policy.required_ids
    observed_ids = tuple(item.check_id for item in parsed)
    if not required:
        reasons.append("required_check_set_empty")
    counts = {check_id: observed_ids.count(check_id) for check_id in set(observed_ids)}
    if any(count > 1 for count in counts.values()):
        reasons.append("required_check_duplicate")
    if any(check_id not in observed_ids for check_id in required):
        reasons.append("required_check_missing")
    if any(check_id not in required for check_id in observed_ids):
        reasons.append("unexpected_check_result")

    for item in parsed:
        if item.binding != binding:
            reasons.append("evidence_binding_mismatch")
        if not isinstance(item.check_id, str) or not item.check_id.strip():
            reasons.append("observed_result_malformed")
        if not isinstance(item.status, str) or item.status not in {_PASS, *_NONPASS}:
            reasons.append("observed_result_malformed")
        elif item.status == "skipped" or item.status == "xfailed":
            reasons.append("required_test_skipped")
        elif item.status == "missing":
            reasons.append("required_check_missing")
        elif item.status != _PASS:
            reasons.append("required_test_failed")
        if item.status == _PASS and item.exit_code != 0:
            reasons.append("observed_result_malformed")
        if item.exit_code is not None and (not isinstance(item.exit_code, int) or isinstance(item.exit_code, bool)):
            reasons.append("observed_result_malformed")
        if (
            not isinstance(item.stdout_digest, str)
            or not _HEX64.fullmatch(item.stdout_digest)
            or not isinstance(item.stderr_digest, str)
            or not _HEX64.fullmatch(item.stderr_digest)
        ):
            reasons.append("observed_result_malformed")

    # Stable order without concealing multiple independent refusal causes.
    unique_reasons = tuple(dict.fromkeys(reasons))
    verdict = "hold" if unique_reasons else "approve_local"
    surface = {
        "verdict": verdict,
        "reason_codes": unique_reasons,
        "binding": asdict(binding),
        "required_ids": required,
        "observed_ids": observed_ids,
        "changed_paths": changed,
    }
    import hashlib
    evidence_digest = hashlib.sha256(canonical_json(surface)).hexdigest()
    return VerificationDecision(
        verdict, unique_reasons, binding, required, observed_ids, changed, evidence_digest
    )


# Clear semantic alias for gate callers.
verify_results = verify_closure


# Execution layer used by the disposable credential-free verifier image.
import argparse
import json
from pathlib import Path
import subprocess
import sys
import uuid
from typing import Any

_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RESULT_PREFIX = "AQ_CHECK_RESULT:"
_ALLOWED_OUTCOMES = {"passed", "failed", "skipped", "error", "timeout"}


@dataclass(frozen=True)
class CheckObservation:
    id: str
    outcome: str
    exit_code: int | None
    passed_count: int
    skipped_count: int
    stdout_sha256: str
    stderr_sha256: str
    detail: str = ""


@dataclass(frozen=True)
class VerificationResult:
    verdict: str
    reason_codes: tuple[str, ...]
    policy_digest: str
    base_sha: str
    candidate_sha: str
    observations: tuple[CheckObservation, ...]

    def as_mapping(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason_codes": list(self.reason_codes),
            "policy_digest": self.policy_digest,
            "base_sha": self.base_sha,
            "candidate_sha": self.candidate_sha,
            "observations": [asdict(item) for item in self.observations],
        }


def exact_closure(policy: VerifierPolicy, observations: Sequence[CheckObservation]) -> VerificationResult:
    """Approve only exact, non-vacuous, one-pass-per-required-ID closure."""
    reasons: list[str] = []
    required = policy.required_ids
    if not required:  # defensive; policy loader already rejects this
        reasons.append("manifest_empty")
    seen: dict[str, list[CheckObservation]] = {}
    for observation in observations:
        seen.setdefault(observation.id, []).append(observation)
    for check_id in required:
        items = seen.get(check_id, [])
        if not items:
            reasons.append(f"required_check_missing:{check_id}")
            continue
        if len(items) != 1:
            reasons.append(f"required_check_duplicate:{check_id}")
            continue
        item = items[0]
        if item.outcome == "skipped" or item.skipped_count:
            reasons.append(f"required_check_skipped:{check_id}")
        elif item.outcome != "passed" or item.exit_code != 0 or item.passed_count < 1:
            reasons.append(f"required_check_{item.outcome}:{check_id}")
    for check_id in sorted(set(seen) - set(required)):
        reasons.append(f"unexpected_check:{check_id}")
    # A result object with a malformed outcome never becomes authority.
    for item in observations:
        if item.outcome not in _ALLOWED_OUTCOMES:
            reasons.append(f"malformed_check_outcome:{item.id}")
    reasons = list(dict.fromkeys(reasons))
    return VerificationResult(
        verdict="approve_local" if not reasons else "hold",
        reason_codes=tuple(reasons),
        policy_digest=policy.digest,
        base_sha=policy.base_sha,
        candidate_sha=policy.candidate_sha,
        observations=tuple(observations),
    )


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_trusted_receipt(check_id: str, stdout: bytes) -> tuple[str, int, int, str]:
    """Parse the immutable wrapper's terminal receipt, never pytest human text."""
    lines = stdout.decode("utf-8", "replace").splitlines()
    hits = [line[len(_RESULT_PREFIX):] for line in lines if line.startswith(_RESULT_PREFIX)]
    if len(hits) != 1:
        return "error", 0, 0, "missing or duplicate trusted completion receipt"
    try:
        value = json.loads(hits[0])
    except json.JSONDecodeError:
        return "error", 0, 0, "malformed trusted completion receipt"
    if not isinstance(value, dict) or value.get("protocol") != "aq-trusted-check-v1" or value.get("id") != check_id:
        return "error", 0, 0, "trusted completion receipt identity mismatch"
    outcome = value.get("outcome")
    passed = value.get("passed_count")
    skipped = value.get("skipped_count")
    if outcome not in _ALLOWED_OUTCOMES or not isinstance(passed, int) or isinstance(passed, bool) or passed < 0:
        return "error", 0, 0, "trusted completion receipt fields invalid"
    if not isinstance(skipped, int) or isinstance(skipped, bool) or skipped < 0:
        return "error", 0, 0, "trusted completion receipt fields invalid"
    if outcome == "passed" and (passed < 1 or skipped != 0):
        return "error", passed, skipped, "vacuous or partially skipped green held"
    return outcome, passed, skipped, str(value.get("detail", ""))[:500]


def execute_inside(policy_path: Path, checks_root: Path, candidate_root: Path) -> VerificationResult:
    """Run inside the credential-free container; default failures remain HOLD."""
    policy = VerifierPolicy.load(policy_path, trusted_checks_root=checks_root)
    observations: list[CheckObservation] = []
    base_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp/home",
        "TMPDIR": "/tmp",
        "PYTHONNOUSERSITE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "AQ_CANDIDATE_ROOT": str(candidate_root),
    }
    Path("/tmp/home").mkdir(mode=0o700, exist_ok=True)
    for check in policy.checks:
        env = dict(base_env)
        env["AQ_REQUIRED_CHECK_ID"] = check.id
        try:
            completed = subprocess.run(
                list(check.argv), cwd=candidate_root, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=check.timeout_seconds,
                check=False,
            )
            outcome, passed, skipped, detail = _parse_trusted_receipt(check.id, completed.stdout)
            if completed.returncode != 0 and outcome == "passed":
                outcome, detail = "failed", f"trusted check exited {completed.returncode} despite pass receipt"
            observations.append(CheckObservation(
                id=check.id, outcome=outcome, exit_code=completed.returncode,
                passed_count=passed, skipped_count=skipped,
                stdout_sha256=_digest(completed.stdout), stderr_sha256=_digest(completed.stderr), detail=detail,
            ))
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b""; stderr = exc.stderr or b""
            if isinstance(stdout, str): stdout = stdout.encode()
            if isinstance(stderr, str): stderr = stderr.encode()
            observations.append(CheckObservation(
                id=check.id, outcome="timeout", exit_code=None, passed_count=0, skipped_count=0,
                stdout_sha256=_digest(stdout), stderr_sha256=_digest(stderr), detail="trusted check timed out",
            ))
        except Exception as exc:  # fail closed; do not let a missing runner become green
            observations.append(CheckObservation(
                id=check.id, outcome="error", exit_code=None, passed_count=0, skipped_count=0,
                stdout_sha256=_digest(b""), stderr_sha256=_digest(b""),
                detail=f"{type(exc).__name__}: {exc}"[:500],
            ))
    return exact_closure(policy, observations)


class DockerVerifier:
    """Trusted host-side launcher. Candidate code only runs in the Docker child."""

    def __init__(self, image_id: str, *, docker: str = "docker", timeout_seconds: int = 1200) -> None:
        if not _IMAGE_ID_RE.fullmatch(image_id):
            raise ValueError("verifier image must be addressed by immutable sha256 image ID")
        self.image_id = image_id
        self.docker = docker
        self.timeout_seconds = timeout_seconds

    def verify(self, policy_path: str | Path, candidate_root: str | Path, *, trusted_checks_root: str | Path) -> VerificationResult:
        policy_path = Path(policy_path).resolve(strict=True)
        candidate_root = Path(candidate_root).resolve(strict=True)
        checks_root = Path(trusted_checks_root).resolve(strict=True)
        if not candidate_root.is_dir() or candidate_root.is_symlink():
            raise PolicyError("candidate root must be a real directory")
        policy = VerifierPolicy.load(policy_path, trusted_checks_root=checks_root)
        name = "aq-verifier-" + uuid.uuid4().hex
        argv = [
            self.docker, "run", "--rm", "--name", name,
            "--network", "none", "--read-only",
            "--mount", f"type=bind,src={candidate_root},dst=/candidate,readonly",
            "--mount", f"type=bind,src={policy_path},dst=/policy/policy.json,readonly",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777",
            "--user", "65532:65532", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--pids-limit", "128", "--memory", "1g", "--cpus", "2",
            self.image_id, "/policy/policy.json", "/opt/aq-trusted/checks", "/candidate",
        ]
        try:
            completed = subprocess.run(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=self.timeout_seconds, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"verifier sandbox rig failure: {exc}") from exc
        if completed.returncode != 0:
            raise RuntimeError(
                f"verifier sandbox failed closed rc={completed.returncode}: "
                + completed.stderr.decode("utf-8", "replace")[-500:]
            )
        if len(completed.stdout) > 1_000_000:
            raise RuntimeError("verifier emitted oversized result")
        try:
            raw = json.loads(completed.stdout)
            observations = tuple(CheckObservation(**item) for item in raw["observations"])
            result = VerificationResult(
                verdict=raw["verdict"], reason_codes=tuple(raw["reason_codes"]),
                policy_digest=raw["policy_digest"], base_sha=raw["base_sha"],
                candidate_sha=raw["candidate_sha"], observations=observations,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("malformed verifier result held") from exc
        # Recompute closure outside the sandbox; never trust its claimed verdict alone.
        expected = exact_closure(policy, result.observations)
        if result != expected:
            raise RuntimeError("verifier result does not match trusted host closure")
        return result


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("policy", type=Path)
    parser.add_argument("checks_root", type=Path)
    parser.add_argument("candidate_root", type=Path)
    args = parser.parse_args(argv)
    try:
        result = execute_inside(args.policy, args.checks_root, args.candidate_root)
    except Exception as exc:
        # Container failure is distinguishable from a product HOLD by the trusted host.
        print(f"verifier internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 70
    sys.stdout.write(json.dumps(result.as_mapping(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
