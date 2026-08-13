"""Injection contract for the `aqdb` skill (#4834): the image puts aqdb on PATH and the
entrypoint seeds the AGENTS.md template into the agent workspace — idempotently and fail-safe.

The Dockerfile/entrypoint checks are static-contract assertions (we don't build an image in the
unit suite). The seeding behaviour is exercised for real by extracting the entrypoint's shell
function and running it under bash against temp directories.
"""

import os
import pathlib
import re
import stat
import subprocess
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "container" / "Dockerfile"
ENTRYPOINT = ROOT / "container" / "app-entrypoint.sh"
SKILL_DIR = ROOT / "agent_skills" / "db"
AQDB = SKILL_DIR / "aqdb"
AGENTS_TEMPLATE = ROOT / "agent_skills" / "AGENTS.md"


# ---------------------------------------------------------------------------
# Artifacts exist and are shaped right
# ---------------------------------------------------------------------------
def test_skill_artifacts_present():
    assert AQDB.is_file()
    assert (SKILL_DIR / "SKILL.md").is_file()
    assert AGENTS_TEMPLATE.is_file()


def test_aqdb_is_executable_with_python_shebang():
    mode = AQDB.stat().st_mode
    assert mode & stat.S_IXUSR, "aqdb must be executable in the repo (git preserves the bit)"
    first = AQDB.read_text().splitlines()[0]
    assert first.startswith("#!") and "python3" in first


def test_aqdb_never_hardcodes_credentials():
    text = AQDB.read_text()
    assert 'os.environ.get("AQ_DB_URL")' in text
    # no inline DSN / password literals
    assert "postgres://" not in text and "postgresql://" not in text
    assert "password=" not in text.lower()


# ---------------------------------------------------------------------------
# Dockerfile contract: copy + executable + on PATH
# ---------------------------------------------------------------------------
def test_dockerfile_copies_and_symlinks_aqdb_onto_path():
    df = DOCKERFILE.read_text()
    assert "COPY agent_skills/ /app/agent_skills/" in df
    assert "chmod +x" in df and "/app/agent_skills/db/aqdb" in df
    assert "ln -s /app/agent_skills/db/aqdb /usr/local/bin/aqdb" in df


def test_dockerfile_pins_shebang_to_venv_and_smoke_tests_the_skills():
    """The skills import psycopg2 at module load; the agent runs them from a codex sandbox whose
    PATH does not front the venv, so `#!/usr/bin/env python3` picks the system python (no driver)
    and the tool cannot start. The image MUST repoint the shebang to the venv AND smoke-test that
    the tools actually run at build — a PATH-only contract passes even when the tool can't import
    its driver (the gap that only surfaced in a live codex cycle)."""
    df = DOCKERFILE.read_text()
    # shebang repinned to the venv interpreter that carries the drivers, for BOTH skills
    assert "#!/opt/venv/bin/python3" in df
    assert "/app/agent_skills/db/aqdb /app/agent_skills/memory/aqmem" in df  # the sed targets both
    # build-time smoke test: `--help` loads the module (and thus imports psycopg2), failing the
    # BUILD if the interpreter can't import the driver
    assert "/usr/local/bin/aqdb --help" in df and "/usr/local/bin/aqmem --help" in df


# ---------------------------------------------------------------------------
# Entrypoint contract: fail-safe idempotent seeding
# ---------------------------------------------------------------------------
def test_entrypoint_has_failsafe_seed_block():
    ep = ENTRYPOINT.read_text()
    assert "seed_agent_workspace" in ep
    # invoked in a way that cannot abort the entrypoint under `set -e`
    assert "seed_agent_workspace || true" in ep
    # idempotency guard: never overwrite an existing AGENTS.md
    assert "-e \"$target\"" in ep


def _extract_seed_function() -> str:
    ep = ENTRYPOINT.read_text().splitlines()
    start = next(i for i, l in enumerate(ep) if l.startswith("seed_agent_workspace() {"))
    end = next(i for i in range(start + 1, len(ep)) if ep[i] == "}")
    return "\n".join(ep[start:end + 1])


def _run_seed(tmp_path, workspace, template, extra=""):
    """Run the real entrypoint seeding function under `set -euo pipefail`."""
    func = _extract_seed_function()
    script = tmp_path / "harness.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + func
        + "\n"
        + extra
        + "\nseed_agent_workspace\n"
        "echo \"RC=$?\"\n"
    )
    env = dict(os.environ)
    env["AQ_ACT_WORKSPACE"] = str(workspace)
    env["AQ_AGENTS_TEMPLATE"] = str(template)
    return subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True
    )


def test_seeding_places_template_into_empty_workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    tpl = tmp_path / "AGENTS.md"
    tpl.write_text("AGENT INSTRUCTIONS BODY\n")

    res = _run_seed(tmp_path, ws, tpl)
    assert res.returncode == 0, res.stderr
    assert "RC=0" in res.stdout
    seeded = ws / "AGENTS.md"
    assert seeded.is_file()
    assert seeded.read_text() == "AGENT INSTRUCTIONS BODY\n"


def test_seeding_is_idempotent_never_overwrites(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    tpl = tmp_path / "AGENTS.md"
    tpl.write_text("TEMPLATE\n")
    target = ws / "AGENTS.md"
    target.write_text("OPERATOR EDITED — DO NOT CLOBBER\n")

    res = _run_seed(tmp_path, ws, tpl)
    assert res.returncode == 0, res.stderr
    assert target.read_text() == "OPERATOR EDITED — DO NOT CLOBBER\n"


def test_seeding_failsafe_when_workspace_missing(tmp_path):
    ws = tmp_path / "does-not-exist"
    tpl = tmp_path / "AGENTS.md"
    tpl.write_text("TEMPLATE\n")

    res = _run_seed(tmp_path, ws, tpl)
    assert res.returncode == 0, res.stderr  # never aborts the entrypoint
    assert "RC=0" in res.stdout
    assert not ws.exists()


def test_seeding_failsafe_when_template_missing(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    tpl = tmp_path / "nope.md"  # absent

    res = _run_seed(tmp_path, ws, tpl)
    assert res.returncode == 0, res.stderr
    assert not (ws / "AGENTS.md").exists()


def test_real_template_seeds_and_mentions_aqdb(tmp_path):
    """The actual shipped AGENTS.md is what production seeds, and it must point at aqdb."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    res = _run_seed(tmp_path, ws, AGENTS_TEMPLATE)
    assert res.returncode == 0, res.stderr
    body = (ws / "AGENTS.md").read_text()
    assert "aqdb" in body
