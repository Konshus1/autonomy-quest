"""Injection + static-contract tests for the `aqmem` memory skill (#4834).

The image puts `aqmem` on PATH, bakes the local embedding model into the image, and the entrypoint
seeds the SHARED AGENTS.md (which now covers BOTH aqdb and aqmem — extended, not forked). We don't
build an image in the unit suite, so the Dockerfile/entrypoint/migration checks are static-contract
assertions; the seeding behaviour is exercised for real via the entrypoint's shell function.
"""

import pathlib
import stat
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "container" / "Dockerfile"
ENTRYPOINT = ROOT / "container" / "app-entrypoint.sh"
REQUIREMENTS = ROOT / "requirements.txt"
MEM_DIR = ROOT / "agent_skills" / "memory"
AQMEM = MEM_DIR / "aqmem"
AGENTS_TEMPLATE = ROOT / "agent_skills" / "AGENTS.md"
MIGRATION = ROOT / "schema" / "030_agent_memory.sql"


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------
def test_skill_artifacts_present():
    assert AQMEM.is_file()
    assert (MEM_DIR / "SKILL.md").is_file()
    assert MIGRATION.is_file()


def test_aqmem_is_executable_with_python_shebang():
    mode = AQMEM.stat().st_mode
    assert mode & stat.S_IXUSR, "aqmem must be executable in the repo (git preserves the bit)"
    first = AQMEM.read_text().splitlines()[0]
    assert first.startswith("#!") and "python3" in first


def test_aqmem_never_hardcodes_credentials():
    text = AQMEM.read_text()
    assert 'os.environ.get("AQ_DB_URL")' in text
    assert "postgres://" not in text and "postgresql://" not in text
    assert "password=" not in text.lower()


# ---------------------------------------------------------------------------
# Dockerfile: copy + executable + on PATH + baked local model, no runtime network
# ---------------------------------------------------------------------------
def test_dockerfile_puts_aqmem_on_path():
    df = DOCKERFILE.read_text()
    # agent_skills/ (incl. memory/) is copied wholesale by the existing COPY.
    assert "COPY agent_skills/ /app/agent_skills/" in df
    assert "/app/agent_skills/memory/aqmem" in df  # chmod +x
    assert "ln -s /app/agent_skills/memory/aqmem /usr/local/bin/aqmem" in df


def test_dockerfile_bakes_local_model_with_build_time_assertions():
    df = DOCKERFILE.read_text()
    assert "fastembed" in REQUIREMENTS.read_text()
    assert "FASTEMBED_CACHE_PATH=/opt/aq/models" in df
    assert "BAAI/bge-small-en-v1.5" in df
    # the bake must ASSERT at build time so a network hiccup fails the BUILD, not runtime
    assert "assert len(vec) == 384" in df
    assert "model cache is empty" in df


# ---------------------------------------------------------------------------
# AGENTS.md was EXTENDED (both skills), not forked
# ---------------------------------------------------------------------------
def test_agents_template_covers_both_skills():
    body = AGENTS_TEMPLATE.read_text()
    assert "aqmem" in body and "aqdb" in body  # extended, not replaced
    assert "aqmem recall" in body and "aqmem remember" in body


# ---------------------------------------------------------------------------
# Migration: idempotent, existence-guarded, least-privilege
# ---------------------------------------------------------------------------
def test_migration_is_idempotent_and_guarded():
    sql = MIGRATION.read_text()
    assert "CREATE SCHEMA IF NOT EXISTS agent_memory" in sql
    assert "CREATE TABLE IF NOT EXISTS agent_memory.notes" in sql
    assert "vector(384)" in sql
    assert "vector_cosine_ops" in sql
    # AGE half is optional + guarded (must not fail the migration on a server without AGE)
    assert "pg_available_extensions WHERE name = 'age'" in sql
    assert "ag_catalog.ag_graph WHERE name = 'world_model'" in sql
    assert "create_graph('world_model')" in sql


def test_migration_grants_actor_only_on_memory_not_authority():
    sql = MIGRATION.read_text()
    assert "GRANT USAGE ON SCHEMA agent_memory TO aq_actor" in sql
    assert "GRANT SELECT, INSERT ON agent_memory.notes TO aq_actor" in sql
    # Inspect the ACTUAL grant statements (ignore prose/comments): no GRANT to aq_actor may name an
    # authority/evidence table. Comments start with `--`; grants are `EXECUTE 'GRANT ...'` lines.
    grant_lines = [
        ln for ln in sql.splitlines()
        if "GRANT" in ln and "aq_actor" in ln and not ln.lstrip().startswith("--")
    ]
    assert grant_lines, "expected explicit GRANT statements to aq_actor"
    for authority in ("learnings", "runs", " work", "causal_edge", "subscriptions", "customers"):
        for ln in grant_lines:
            assert authority not in ln, (
                "migration must not grant aq_actor on authority/business table %r: %s"
                % (authority, ln)
            )


# ---------------------------------------------------------------------------
# Entrypoint seeding still fail-safe + seeds the shared template mentioning aqmem
# ---------------------------------------------------------------------------
def test_entrypoint_still_failsafe():
    ep = ENTRYPOINT.read_text()
    assert "seed_agent_workspace || true" in ep
    assert "-e \"$target\"" in ep  # idempotency guard: never overwrite an existing AGENTS.md


def _extract_seed_function() -> str:
    ep = ENTRYPOINT.read_text().splitlines()
    start = next(i for i, l in enumerate(ep) if l.startswith("seed_agent_workspace() {"))
    end = next(i for i in range(start + 1, len(ep)) if ep[i] == "}")
    return "\n".join(ep[start:end + 1])


def test_real_template_seeds_and_mentions_aqmem(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    func = _extract_seed_function()
    script = tmp_path / "harness.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n" + func + "\nseed_agent_workspace\n"
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "AQ_ACT_WORKSPACE": str(ws),
        "AQ_AGENTS_TEMPLATE": str(AGENTS_TEMPLATE),
    }
    res = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    body = (ws / "AGENTS.md").read_text()
    assert "aqmem" in body and "aqdb" in body
