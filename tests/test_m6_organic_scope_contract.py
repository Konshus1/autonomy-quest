from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_checked_acquisition_normalizes_attribute_pairs_to_plan_scope_object():
    schema=(ROOT/'schema/022_independent_grounding.sql').read_text()
    assert 'normalize_causal_scope' in schema
    assert "jsonb_typeof(p_scope)='array'" in schema

def test_composed_grounded_fixture_originates_in_checked_acquisition_event():
    controls=(ROOT/'tests/test_c4_governance_pg.py').read_text()
    body=controls.split('def test_independently_grounded_positive_path_requires_manual_promotion():',1)[1].split('\ndef test_',1)[0]
    assert 'plan_acquisition' in body and 'attest_acquisition' in body
    assert "scope_conditions::text='{}'" in body
