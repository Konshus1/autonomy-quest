from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_m6_exact_acceptance_entrypoint_exists():
    path=ROOT/'scripts/prove_scope_b_m6_acceptance.sh'
    assert path.is_file() and path.stat().st_mode & 0o111


def test_built_cycle_marks_same_grounded_use_outcome_demotion_chain():
    proof=(ROOT/'scripts/prove_m4_pre_act_boundary.py').read_text()
    harness=(ROOT/'scripts/run_c4_controls.sh').read_text()
    assert 'M6_GROUNDED_PROMOTION' in proof
    assert 'M6 COMPOSED CHAIN OK' in harness


def test_acceptance_checks_restart_default_off_and_actor_credential_denial():
    harness=(ROOT/'scripts/run_c4_controls.sh').read_text()
    assert 'M6_EVALUATOR_RESTART_RECOVERY' in harness
    assert 'M6_DEFAULT_FLAG_OFF' in harness
    assert 'M6_ACT_CREDENTIAL_DENIAL' in harness
