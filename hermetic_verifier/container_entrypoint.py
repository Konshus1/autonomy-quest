#!/usr/bin/env python3
"""Trusted PID 1. Candidate output is data; the host trusts only this process' exit status.

The census is delivered over an anonymous PIPE, never a seekable file.  A pipe cannot be
lseek'd or ftruncate'd, so a candidate ``atexit`` handler that inherits the write descriptor
can only *append* a second record after the trusted plugin's record -- it can never rewind and
overwrite it.  PID 1 requires EXACTLY ONE well-formed record: a missing record (plugin
suppressed / unregistered), a malformed record, or more than one record all fail closed.

KNOWN LIMITATION (see HERMETIC_CENSUS_LIMITATION.md): the census plugin runs *inside* the
candidate's own pytest process, at the candidate's uid.  This sandbox is non-root with all
capabilities dropped and no-new-privileges, so PID 1 cannot drop the candidate to a lower uid
(setuid is EPERM -- verified live).  A determined candidate can therefore still suppress the
trusted plugin and write a single forged record to the inherited pipe descriptor.  The pipe +
single-record discipline below closes the published atexit-rewrite / recorder-mutation /
plugin-unregister exploits, but it does NOT make the census unforgeable.  Sound closure requires
the census authority to live in a process the candidate uid cannot influence -- unachievable
while pytest imports candidate code in the same process.
"""
from __future__ import annotations

import os
import pathlib
import json
import shutil
import subprocess
import sys
import threading

SOURCE = pathlib.Path("/candidate")
WORK = pathlib.Path("/work/repo")
LOG = pathlib.Path("/tmp/candidate-test.log")
TAIL_BYTES = 128 * 1024
CENSUS_CAP = 64 * 1024
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


def _parse_census_records(raw: bytes) -> dict[str, object]:
    """Interpret the raw pipe bytes.  Require EXACTLY ONE well-formed, self-consistent record.

    Zero records (plugin suppressed / never wrote), more than one record (a genuine record plus
    a candidate-appended forgery, or the reverse), or a malformed record all fail closed.
    """
    records = [line for line in raw.split(b"\n") if line.strip()]
    if len(records) != 1:
        return _empty_census()
    try:
        census = json.loads(records[0])
    except (TypeError, ValueError, json.JSONDecodeError):
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
    # The only writable project tree is the container tmpfs. The pinned source bind stays read-only.
    shutil.copytree(SOURCE, WORK, symlinks=True)

    command = sys.argv[1:] or ["python", "-m", "pytest", "-q"]
    trusted_command = _trusted_pytest_command(command)

    # Anonymous pipe: PID 1 keeps the read end; the candidate pytest inherits the write end.  The
    # write end is not seekable, so the trusted plugin's record cannot be rewound and overwritten.
    census_read, census_write = os.pipe()
    os.set_inheritable(census_write, True)

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
        # The in-process trusted plugin must locate the write end.  Advertising it also hands it to
        # the candidate (same process/uid) -- an unavoidable consequence of the in-process model
        # documented in HERMETIC_CENSUS_LIMITATION.md.  The pipe's append-only nature and the
        # single-record rule below are what bound the resulting exposure.
        "AQ_VERIFIER_CENSUS_FD": str(census_write) if trusted_command is not None else "",
    }
    pathlib.Path(clean_env["HOME"]).mkdir(mode=0o700, exist_ok=True)

    captured = bytearray()

    def drain() -> None:
        while True:
            try:
                chunk = os.read(census_read, 4096)
            except OSError:
                return
            if not chunk:
                return
            room = CENSUS_CAP - len(captured)
            if room > 0:
                captured.extend(chunk[:room])
            # Keep draining past the cap so a candidate cannot deadlock the run by flooding the pipe;
            # an oversized stream simply yields more-than-one-record / malformed -> fail closed.

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    try:
        with LOG.open("wb") as log:
            process = subprocess.Popen(
                trusted_command or command,
                cwd=WORK,
                env=clean_env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                pass_fds=(census_write,),
            )
    except (OSError, ValueError) as exc:
        os.close(census_write)
        os.close(census_read)
        print(f"verifier infrastructure error: cannot execute candidate command: {exc}", file=sys.stderr)
        return 125
    # Drop PID 1's own copy of the write end so the reader observes EOF once the candidate exits.
    os.close(census_write)
    process.wait()
    reader.join(timeout=5)
    os.close(census_read)

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

    census = _parse_census_records(bytes(captured)) if trusted_command is not None else _empty_census()
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
