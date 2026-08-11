#!/usr/bin/env python3
"""Trusted PID 1. Candidate output is data; the host trusts only this process' exit status."""
from __future__ import annotations

import os
import pathlib
import json
import shutil
import subprocess
import sys

SOURCE = pathlib.Path("/candidate")
WORK = pathlib.Path("/work/repo")
LOG = pathlib.Path("/tmp/candidate-test.log")
CENSUS_DIR = pathlib.Path("/work/.verifier")
CENSUS = CENSUS_DIR / "census.json"
TAIL_BYTES = 128 * 1024
RESULT_PREFIX = "AQ_HERMETIC_RESULT:"
CENSUS_SCHEMA = "aq.pytest-census.v1"


def _trusted_pytest_command(command: list[str]) -> list[str] | None:
    if (len(command) >= 3 and pathlib.PurePath(command[0]).name.startswith("python")
            and command[1:3] == ["-m", "pytest"]):
        pytest_args = command[3:]
    elif command and pathlib.PurePath(command[0]).name in {"pytest", "py.test"}:
        pytest_args = command[1:]
    else:
        return None
    # Isolated mode prevents a candidate's verifier_census.py from shadowing the root-owned module
    # in site-packages.  Pytest still adds the candidate root when it begins normal collection.
    return [sys.executable, "-I", "-m", "pytest", "-p", "verifier_census", *pytest_args]


def _empty_census() -> dict[str, object]:
    return {
        "census_valid": False,
        "clean": False,
        "collected_count": 0,
        "passed_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "error_count": 0,
    }


def _read_census(descriptor: int) -> dict[str, object]:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        raw = os.read(descriptor, 64 * 1024)
        census = json.loads(raw)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _empty_census()
    integer_fields = ("collected_count", "passed_count", "failed_count", "skipped_count", "error_count")
    valid = (
        isinstance(census, dict)
        and census.get("schema") == CENSUS_SCHEMA
        and census.get("sessionfinish_reached") is True
        and type(census.get("clean")) is bool
        and type(census.get("internal_error")) is bool
        and all(type(census.get(field)) is int and census[field] >= 0 for field in integer_fields)
        and census.get("clean") == (
            census.get("internal_error") is False
            and census.get("failed_count") == 0
            and census.get("error_count") == 0
        )
    )
    if not valid:
        return _empty_census()
    return {"census_valid": True, **{field: census[field] for field in integer_fields},
            "clean": census["clean"]}


def main() -> int:
    if not SOURCE.is_dir():
        print("verifier infrastructure error: /candidate is not mounted", file=sys.stderr)
        return 125
    if WORK.exists():
        shutil.rmtree(WORK)
    if CENSUS_DIR.exists():
        shutil.rmtree(CENSUS_DIR)
    CENSUS_DIR.mkdir(mode=0o700)
    census_fd = os.open(CENSUS, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    # The plugin writes through the inherited descriptor.  The path is already exclusively
    # created by PID 1, and candidate attempts to pre-create it cannot supply the final record.
    CENSUS.chmod(0o400)
    CENSUS_DIR.chmod(0o500)
    # The only writable project tree is the container tmpfs. The pinned source bind stays read-only.
    shutil.copytree(SOURCE, WORK, symlinks=True)

    command = sys.argv[1:] or ["python", "-m", "pytest", "-q"]
    trusted_command = _trusted_pytest_command(command)
    clean_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp/home",
        "TMPDIR": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "AQ_HERMETIC_VERIFIER": "1",
        "AQ_CANDIDATE_SHA": os.environ.get("AQ_CANDIDATE_SHA", ""),
        "AQ_VERIFIER_CENSUS_FD": str(census_fd),
    }
    pathlib.Path(clean_env["HOME"]).mkdir(mode=0o700, exist_ok=True)
    tail = b""
    try:
        with LOG.open("wb") as log:
            completed = subprocess.run(
                trusted_command or command,
                cwd=WORK,
                env=clean_env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                pass_fds=(census_fd,),
            )
    except (OSError, ValueError) as exc:
        print(f"verifier infrastructure error: cannot execute candidate command: {exc}", file=sys.stderr)
        return 125

    # Logs are explicitly untrusted and bounded. The signed verdict is built by the host, never here.
    try:
        with LOG.open("rb") as log:
            log.seek(0, os.SEEK_END)
            size = log.tell()
            log.seek(max(0, size - TAIL_BYTES))
            tail = log.read()
        if size > TAIL_BYTES:
            sys.stderr.write(f"[candidate log truncated; total={size} bytes]\n")
        sys.stderr.buffer.write(tail)
        sys.stderr.flush()
    except OSError as exc:
        print(f"verifier warning: cannot read candidate log: {exc}", file=sys.stderr)
    census = _read_census(census_fd) if trusted_command is not None else _empty_census()
    os.close(census_fd)
    print(RESULT_PREFIX + json.dumps(census, sort_keys=True),
          file=sys.stderr, flush=True)
    trusted_pass = (
        census["census_valid"] is True
        and census["clean"] is True
        and census["collected_count"] > 0
        and census["passed_count"] > 0
        and census["failed_count"] == 0
        and census["error_count"] == 0
    )
    return 0 if trusted_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
