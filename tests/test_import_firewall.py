"""The autonomous mission loop must never reach an ACTUATION surface — formal promotion, replication
execution, or fleet brokering. This uses the reusable import_firewall and extends the original
formal-only check to cover replication + the new fleet-registry too (one guard, all actuation)."""
import pathlib

from ralph_portable.import_firewall import forbidden_references

REPO = pathlib.Path(__file__).resolve().parent.parent

HOT_PATH = ["runner/loop.py", "runner/causal_sync.py", "ralph_portable/principle_mining.py"]

FORBIDDEN_IMPORTS = (
    "ralph_portable.formal",              # formal promotion / oracle
    "ralph_portable.fleet_registry",      # fleet identity + comms brokering
    "ralph_portable.host_replication_executor",  # replication execution
)
FORBIDDEN_NAMES = (
    "apply_promotion", "verify_and_record", "attach_executor", "run_oracle", "formal_proof_evidence",
    "execute_replication_copy", "FleetRegistryStore", "mint_identity", "grant_cross_fleet",
)


def test_loop_cannot_reach_any_actuation_surface():
    violations = forbidden_references(
        HOT_PATH, import_prefixes=FORBIDDEN_IMPORTS, names=FORBIDDEN_NAMES, repo_root=REPO)
    assert not violations, f"the mission loop must not reach actuation: {violations}"


def test_firewall_has_teeth(tmp_path):
    # Plant a hot-path file that DOES import + call a forbidden surface; the firewall must flag BOTH.
    bad = tmp_path / "bad_loop.py"
    bad.write_text("from ralph_portable.formal.oracle_harness import run_oracle\n"
                   "def cycle():\n    return run_oracle('k', None)\n")
    v = forbidden_references(["bad_loop.py"], import_prefixes=FORBIDDEN_IMPORTS,
                             names=FORBIDDEN_NAMES, repo_root=tmp_path)
    assert any("imports forbidden module" in x for x in v)
    assert any("references forbidden actuation name" in x for x in v)


def test_firewall_catches_dynamic_import_literal(tmp_path):
    # A dynamic import that dodges the AST channel (importlib with the module path as a STRING) is now
    # flagged by the text-path check — only deliberate string-splitting evades (documented limitation).
    bad = tmp_path / "sneaky_loop.py"
    bad.write_text("import importlib\n"
                   "def cycle():\n"
                   "    m = importlib.import_module('ralph_portable.formal.oracle_harness')\n"
                   "    return m\n")
    v = forbidden_references(["sneaky_loop.py"], import_prefixes=FORBIDDEN_IMPORTS,
                             names=FORBIDDEN_NAMES, repo_root=tmp_path)
    assert any("forbidden import path" in x for x in v), "dynamic-import literal must be flagged"


def test_firewall_is_clean_on_a_benign_file(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("import json\ndef f():\n    return json.dumps({'consult': True})\n")
    assert forbidden_references(["ok.py"], import_prefixes=FORBIDDEN_IMPORTS,
                                names=FORBIDDEN_NAMES, repo_root=tmp_path) == []
