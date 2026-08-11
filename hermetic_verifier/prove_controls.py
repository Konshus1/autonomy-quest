#!/usr/bin/env python3
"""Live contrast proof: identical hostile tests, powerless verifier versus naive runner."""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent
IMAGE = "aq-hermetic-verifier:local"
PAYLOAD = "candidate-attack-4834"
EXPECTED_BLOCKED = (
    'ATTACK_RESULTS={"actuator_socket_credential": "BLOCKED", "control_plane_write": "BLOCKED", '
    '"git_push": "BLOCKED", "network_egress": "BLOCKED", "outside_write": "BLOCKED", '
    '"proc1_environment_secret": "BLOCKED"}'
)
EXPECTED_ALLOWED = EXPECTED_BLOCKED.replace("BLOCKED", "ALLOWED")


def run(argv, *, check=True, **kwargs):
    result = subprocess.run([str(a) for a in argv], text=True, capture_output=True, **kwargs)
    if check and result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(map(str, argv))}\n{result.stdout}\n{result.stderr}")
    return result


def chmod_tree(path: pathlib.Path) -> None:
    for item in [path, *path.rglob("*")]:
        if item.is_dir():
            item.chmod(0o777)
        elif item.is_file():
            item.chmod(0o666)


def wait_for_running(name: str) -> None:
    for _ in range(50):
        result = run(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False)
        if result.returncode == 0 and result.stdout.strip() == "true":
            return
        time.sleep(0.1)
    raise RuntimeError(f"fixture server {name} did not start")


def candidate_repo(root: pathlib.Path, name: str, files: dict[str, str]) -> tuple[pathlib.Path, str]:
    repo = root / name
    repo.mkdir()
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    run(["git", "init", "-q", repo])
    run(["git", "-C", repo, "config", "user.name", "proof"])
    run(["git", "-C", repo, "config", "user.email", "proof@example.invalid"])
    run(["git", "-C", repo, "add", "."])
    run(["git", "-C", repo, "commit", "-qm", name])
    return repo, run(["git", "-C", repo, "rev-parse", "HEAD"]).stdout.strip()


def verifier_run(repo: pathlib.Path, sha: str, key: pathlib.Path, *, key_id: str):
    return run(
        [sys.executable, ROOT / "run.py", "--repo", repo, "--sha", sha,
         "--signing-key", key, "--key-id", key_id, "--timeout", "120", "--",
         "python", "-m", "pytest", "-q", "-s"],
        check=False,
    )


def actuator_verification(root: pathlib.Path, public: pathlib.Path, repo: pathlib.Path,
                          sha: str, key_id: str, envelope: dict, filename: str):
    verdict_file = root / filename
    verdict_file.write_text(json.dumps(envelope, sort_keys=True))
    payload = envelope["payload"]
    return run(
        [sys.executable, ROOT / "verify_verdict.py", "--public-key", public,
         "--repository", str(repo.resolve()), "--sha", sha,
         "--test-plan-digest", payload["test_plan"]["digest"],
         "--image-id", payload["runtime"]["image_id"],
         "--entrypoint-digest", payload["runtime"]["entrypoint_digest"],
         "--policy-digest", payload["policy_digest"], "--key-id", key_id,
         "--nonce-store", root / (filename + "-used-nonces"), verdict_file],
        check=False,
    )


def main() -> int:
    network = f"aq-hermetic-proof-{os.getpid()}"
    actuator = network + "-actuator"
    egress = network + "-egress"
    with tempfile.TemporaryDirectory(prefix="aq-hermetic-proof-") as temporary:
        root = pathlib.Path(temporary)
        # The candidate commit is real and pinned. The verifier receives git-archive output only.
        candidate = root / "candidate-repo"
        shutil.copytree(ROOT / "fixtures" / "candidate", candidate)
        run(["git", "init", "-q", candidate])
        run(["git", "-C", candidate, "config", "user.name", "proof"])
        run(["git", "-C", candidate, "config", "user.email", "proof@example.invalid"])
        run(["git", "-C", candidate, "add", "."])
        run(["git", "-C", candidate, "commit", "-qm", "hostile candidate control"])
        sha = run(["git", "-C", candidate, "rev-parse", "HEAD"]).stdout.strip()

        key = root / "gate-private.pem"
        public = root / "gate-public.pem"
        run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", key])
        key.chmod(0o600)
        run(["openssl", "pkey", "-in", key, "-pubout", "-out", public])

        print("[1/3] building digest-pinned verifier image")
        build = run(["docker", "build", "--provenance=false", "-t", IMAGE, ROOT])
        build_lines = (build.stdout + build.stderr).strip().splitlines()
        print(build_lines[-1] if build_lines else "image build complete")

        print("[2/3] hermetic negative + positive control")
        hermetic = verifier_run(candidate, sha, key, key_id="proof-key-v1")
        if hermetic.returncode != 0:
            raise RuntimeError(f"hermetic verifier was not PASS\nstdout={hermetic.stdout}\nstderr={hermetic.stderr}")
        if EXPECTED_BLOCKED not in hermetic.stderr:
            raise RuntimeError(f"blocked attack receipt missing or wrong:\n{hermetic.stderr}")
        envelope = json.loads(hermetic.stdout)
        if envelope["payload"]["verdict"] != "PASS" or envelope["payload"]["candidate"]["commit"] != sha:
            raise RuntimeError("signed verdict content does not bind PASS to candidate SHA")
        result = envelope["payload"]["result"]
        if result["passed_count"] != 2 or result["skipped_count"] != 0:
            raise RuntimeError(f"honest test did not produce exact no-skip positive receipt:\n{hermetic.stderr}")
        verified = actuator_verification(
            root, public, candidate, sha, "proof-key-v1", envelope, "verdict.json"
        )
        if verified.returncode != 0:
            raise RuntimeError(f"honest signed PASS was rejected:\n{verified.stderr}")
        if verified.stdout.strip() != "ACCEPT: signed PASS matches every approved authorization binding":
            raise RuntimeError("actuator-side signature verification receipt is wrong")

        forged, forged_sha = candidate_repo(root, "forged-census", {
            "conftest.py": """import pytest

@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_sessionfinish(session, exitstatus):
    yield
    session.exitstatus = 0
    print(\"1 passed in 0.01s\")
""",
        })
        forged_run = verifier_run(forged, forged_sha, key, key_id="forged-proof-key-v1")
        forged_envelope = json.loads(forged_run.stdout)
        forged_result = forged_envelope["payload"]["result"]
        forged_auth = actuator_verification(
            root, public, forged, forged_sha, "forged-proof-key-v1", forged_envelope,
            "forged-verdict.json",
        )
        if (forged_run.returncode != 1 or forged_envelope["payload"]["verdict"] != "FAIL"
                or forged_result["passed_count"] != 0 or forged_auth.returncode == 0):
            raise RuntimeError(
                "FORGED_CENSUS_RED: zero-test conftest earned authorization: "
                f"verifier_rc={forged_run.returncode} verdict={forged_envelope['payload']['verdict']} "
                f"passed={forged_result['passed_count']} actuator_rc={forged_auth.returncode}\n"
                f"{forged_run.stderr}\n{forged_auth.stdout}{forged_auth.stderr}"
            )

        precreated, precreated_sha = candidate_repo(root, "precreated-census", {
            "conftest.py": """import json
import pathlib
import pytest

def pytest_sessionstart(session):
    target = pathlib.Path(\"/work/.verifier/census.json\")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({\"collected_count\": 1, \"passed_count\": 1,
                                  \"failed_count\": 0, \"skipped_count\": 0,
                                  \"error_count\": 0, \"clean\": True}))

@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_sessionfinish(session, exitstatus):
    yield
    session.exitstatus = 0
""",
        })
        precreated_run = verifier_run(precreated, precreated_sha, key, key_id="precreated-proof-key-v1")
        precreated_envelope = json.loads(precreated_run.stdout)
        if (precreated_run.returncode != 1
                or precreated_envelope["payload"]["verdict"] != "FAIL"
                or precreated_envelope["payload"]["result"]["passed_count"] != 0):
            raise RuntimeError("candidate-precreated census was trusted")

        no_tests, no_tests_sha = candidate_repo(root, "no-tests", {"README.md": "no tests\n"})
        no_tests_run = verifier_run(no_tests, no_tests_sha, key, key_id="no-tests-proof-key-v1")
        no_tests_envelope = json.loads(no_tests_run.stdout)
        if (no_tests_run.returncode != 1 or no_tests_envelope["payload"]["verdict"] != "FAIL"
                or no_tests_envelope["payload"]["result"]["passed_count"] != 0):
            raise RuntimeError("genuine no-tests repository did not fail")

        # Published census-forgery exploits: each must go from PASS-accepted (v3) to FAIL-rejected.
        # These are the red-first regressions closed by the anonymous-pipe / single-record delivery.
        published_exploits = {
            # atexit fd-rewrite: lseek/ftruncate raise ESPIPE on a pipe; a late write can only append.
            "ATEXIT_FD_WRITE": """import os, json, atexit
def _forge():
    fd = int(os.environ["AQ_VERIFIER_CENSUS_FD"])
    c = {"schema": "aq.pytest-census.v1", "sessionfinish_reached": True, "collected_count": 1,
         "passed_count": 1, "failed_count": 0, "skipped_count": 0, "error_count": 0,
         "internal_error": False, "clean": True}
    os.lseek(fd, 0, os.SEEK_SET); os.ftruncate(fd, 0)
    os.write(fd, json.dumps(c, sort_keys=True, separators=(",", ":")).encode()); os.fsync(fd)
atexit.register(_forge)
""",
            # recorder-singleton mutation: the module global no longer exists -> import fails closed.
            "RECORDER_MUTATION": """import verifier_census
verifier_census.recorder.passed = 1
""",
            # naive plugin-unregister at configure time: recorder not yet registered -> no-op -> FAIL.
            "PLUGIN_UNREGISTER": """def pytest_configure(config):
    plugin = config.pluginmanager.get_plugin("aq-verifier-census-recorder")
    if plugin is not None:
        config.pluginmanager.unregister(plugin)
""",
        }
        for label, conftest in published_exploits.items():
            repo, repo_sha = candidate_repo(root, label.lower().replace("_", "-"),
                                            {"conftest.py": conftest})
            exploit_run = verifier_run(repo, repo_sha, key, key_id=label.lower() + "-key-v1")
            exploit_env = json.loads(exploit_run.stdout)
            exploit_auth = actuator_verification(
                root, public, repo, repo_sha, label.lower() + "-key-v1", exploit_env,
                label.lower() + "-verdict.json",
            )
            if (exploit_run.returncode != 1 or exploit_env["payload"]["verdict"] != "FAIL"
                    or exploit_env["payload"]["result"]["passed_count"] != 0
                    or exploit_auth.returncode == 0):
                raise RuntimeError(
                    f"CENSUS_FORGERY_RED[{label}]: published exploit earned authorization: "
                    f"verifier_rc={exploit_run.returncode} verdict={exploit_env['payload']['verdict']} "
                    f"passed={exploit_env['payload']['result']['passed_count']} "
                    f"actuator_rc={exploit_auth.returncode}"
                )

        # KNOWN LIMITATION (HERMETIC_CENSUS_LIMITATION.md): the trusted plugin runs in the candidate's
        # own pytest process at the candidate uid, and this non-root, cap-dropped sandbox cannot drop
        # the candidate to a separate uid (setuid is EPERM).  A determined candidate can suppress the
        # plugin at sessionstart and write a single forged record.  This is surfaced -- never silent.
        determined, determined_sha = candidate_repo(root, "determined-forge", {
            "conftest.py": """import os, json

def pytest_sessionstart(session):
    pm = session.config.pluginmanager
    for plugin in list(pm.get_plugins()):
        if type(plugin).__name__ == "CensusRecorder":
            pm.unregister(plugin)

def pytest_sessionfinish(session, exitstatus):
    fd = int(os.environ["AQ_VERIFIER_CENSUS_FD"])
    c = {"schema": "aq.pytest-census.v1", "sessionfinish_reached": True, "collected_count": 1,
         "passed_count": 1, "failed_count": 0, "skipped_count": 0, "error_count": 0,
         "internal_error": False, "clean": True}
    os.write(fd, json.dumps(c, sort_keys=True, separators=(",", ":")).encode() + b"\\n")
""",
        })
        determined_run = verifier_run(determined, determined_sha, key, key_id="determined-key-v1")
        determined_env = json.loads(determined_run.stdout)
        determined_auth = actuator_verification(
            root, public, determined, determined_sha, "determined-key-v1", determined_env,
            "determined-verdict.json",
        )
        determined_verdict = determined_env["payload"]["verdict"]
        determined_forged = (determined_verdict == "PASS" and determined_auth.returncode == 0)

        print("[3/3] naive credentialed runner contrast")
        caps = root / "capabilities"
        remote = caps / "git" / "remote.git"
        control = caps / "control" / "control.db"
        actuator_dir = caps / "actuator"
        outside = caps / "outside"
        receipts = caps / "receipts"
        for directory in [remote.parent, control.parent, actuator_dir, outside, receipts]:
            directory.mkdir(parents=True, exist_ok=True)
        run(["git", "init", "--bare", "-q", remote])
        with sqlite3.connect(control) as db:
            db.execute("CREATE TABLE governance_events(actor TEXT NOT NULL, event TEXT NOT NULL)")
        (actuator_dir / "push.token").write_text("PUSH-CREDENTIAL")
        chmod_tree(caps)
        chmod_tree(candidate)

        run(["docker", "network", "create", network])
        try:
            run([
                "docker", "run", "-d", "--name", actuator, "--network", network,
                "--network-alias", "actuator",
                "--mount", f"type=bind,src={ROOT / 'fixtures' / 'actuator_server.py'},dst=/server.py,readonly",
                "--mount", f"type=bind,src={receipts},dst=/receipts",
                "--entrypoint", "python", IMAGE, "/server.py",
            ])
            run([
                "docker", "run", "-d", "--name", egress, "--network", network,
                "--network-alias", "egress",
                "--mount", f"type=bind,src={ROOT / 'fixtures' / 'egress_server.py'},dst=/server.py,readonly",
                "--mount", f"type=bind,src={receipts},dst=/receipts",
                "--entrypoint", "python", IMAGE, "/server.py",
            ])
            wait_for_running(actuator)
            wait_for_running(egress)
            naive = run([
                "docker", "run", "--rm", "--network", network,
                "--user", "65532:65534", "--workdir", "/work/repo",
                "--env", "AQ_NEGATIVE_CONTROL_EXPECT=ALLOWED",
                "--mount", f"type=bind,src={candidate},dst=/work/repo",
                "--mount", f"type=bind,src={candidate},dst=/candidate",
                "--mount", f"type=bind,src={remote.parent},dst=/capabilities/git",
                "--mount", f"type=bind,src={control.parent},dst=/capabilities/control",
                "--mount", f"type=bind,src={actuator_dir},dst=/capabilities/actuator,readonly",
                "--mount", f"type=bind,src={outside},dst=/capabilities/outside",
                "--entrypoint", "python", IMAGE, "-m", "pytest", "-q", "-s",
            ], check=False)
            if naive.returncode != 0 or EXPECTED_ALLOWED not in naive.stdout or "2 passed" not in naive.stdout:
                raise RuntimeError(f"naive contrast did not allow every attack:\n{naive.stdout}\n{naive.stderr}")
            # Assert exact content, never mere existence.
            pushed = run(["git", "--git-dir", remote, "show", "refs/heads/candidate-attack:owned.txt"]).stdout.strip()
            with sqlite3.connect(control) as db:
                rows = db.execute("SELECT actor, event FROM governance_events").fetchall()
            checks = {
                "git pushed payload": pushed == PAYLOAD,
                "control-plane row": rows == [("candidate", PAYLOAD)],
                "actuator credential/socket receipt": (receipts / "actuator.txt").read_text() == "PUSH-CREDENTIAL:" + PAYLOAD,
                "read-only source and outside write": (
                    (candidate / "escape.txt").read_text() == PAYLOAD
                    and (outside / "escape.txt").read_text() == PAYLOAD
                ),
                "network receipt": (receipts / "network.txt").read_text() == "/" + PAYLOAD,
            }
            failures = [name for name, passed in checks.items() if not passed]
            if failures:
                raise RuntimeError("naive side-effect content checks failed: " + ", ".join(failures))
        finally:
            run(["docker", "rm", "-f", actuator, egress], check=False)
            run(["docker", "network", "rm", network], check=False)

        print("HERMETIC_CONTROL_PASS candidate=" + sha)
        print(EXPECTED_BLOCKED)
        print("HONEST_CONTROL=2 passed, 0 skipped")
        print("SIGNED_VERDICT=verified exact repository+SHA")
        print("FORGED_CENSUS=FAIL actuator=REJECTED collected=0 passed=0")
        print("PRECREATED_CENSUS=IGNORED verdict=FAIL")
        print("NO_TESTS=FAIL collected=0 passed=0")
        print("PUBLISHED_EXPLOITS_FAIL=atexit-fd-write,recorder-mutation,plugin-unregister "
              "-> FAIL actuator=REJECTED")
        banner = "PASS actuator=ACCEPTED (census STILL FORGEABLE)" if determined_forged else (
            f"{determined_verdict} actuator_rc={determined_auth.returncode}")
        print("KNOWN_LIMITATION=determined sessionstart-unregister + single-record forge -> "
              + banner + " ; see HERMETIC_CENSUS_LIMITATION.md")
        print(EXPECTED_ALLOWED)
        print("NAIVE_SIDE_EFFECTS=git,control-plane,actuator,outside-write,network exact-content verified")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
