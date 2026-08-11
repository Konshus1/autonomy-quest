#!/usr/bin/env python3
"""Trusted PID 1. Candidate output is data; the host trusts only this process' exit status."""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

SOURCE = pathlib.Path("/candidate")
WORK = pathlib.Path("/work/repo")
LOG = pathlib.Path("/tmp/candidate-test.log")
TAIL_BYTES = 128 * 1024


def main() -> int:
    if not SOURCE.is_dir():
        print("verifier infrastructure error: /candidate is not mounted", file=sys.stderr)
        return 125
    if WORK.exists():
        shutil.rmtree(WORK)
    # The only writable project tree is the container tmpfs. The pinned source bind stays read-only.
    shutil.copytree(SOURCE, WORK, symlinks=True)

    command = sys.argv[1:] or ["python", "-m", "pytest", "-q"]
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
    }
    pathlib.Path(clean_env["HOME"]).mkdir(mode=0o700, exist_ok=True)
    try:
        with LOG.open("wb") as log:
            completed = subprocess.run(
                command,
                cwd=WORK,
                env=clean_env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
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
    return completed.returncode if 0 <= completed.returncode <= 124 else 124


if __name__ == "__main__":
    raise SystemExit(main())
