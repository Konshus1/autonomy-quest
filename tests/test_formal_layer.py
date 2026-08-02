"""Tests for the formal causal-principle layer (Slice 2, FORMAL_LAYER_SPEC, BB #746/#775).

The load-bearing #615 invariants under test:
  - the RUNG FIREWALL: operational confirms cap an edge at EVIDENTIAL; reaching FORMAL requires a
    passing sandboxed oracle proof (executor_verified), never repetition.
  - the LOOP FIREWALL: the autonomous loop has NO path to arm/verify/promote — actuation is
    operator/CI-only, off the hot path (test_loop_cannot_reach_formal_actuation).
  - digest-pinned encodings (tamper => refuse) and a fail-closed oracle sandbox.
"""
import hashlib
import json
import pathlib
import textwrap

import pytest

from ralph_portable.causal_edges import edge_certainty, is_guaranteed, surprise
from ralph_portable.causal_edge_store import InMemoryCausalEdgeStore
from ralph_portable.formal.attach import attach_executor
from ralph_portable.formal.executor_registry import ExecutorRegistry, RegistryError
from ralph_portable.formal.oracle_harness import run_oracle

REPO = pathlib.Path(__file__).resolve().parent.parent
STUB_MANIFEST = REPO / "ralph_portable" / "formal" / "encodings" / "manifest.json"


def _mined_edge(cause="pin_dependency_versions", effect="build_reproducible"):
    return {"cause": cause, "effect": effect, "scope": {}, "formality": "fuzzy",
            "strictness": "advisory", "directness": "judgment", "executor": {"kind": "judgment"},
            "predicted_certainty": 0.34, "support_count": 0}


def _make_registry(tmp_path, *, oracle_body, payload="x", kind="constraint",
                   cause="a", effect="b", corrupt_digest=False):
    """Build a one-asset registry in tmp_path with a custom oracle body."""
    enc = tmp_path
    (enc / "asp").mkdir(exist_ok=True)
    (enc / "oracle").mkdir(exist_ok=True)
    (enc / "asp" / "p.lp").write_text(payload)
    (enc / "oracle" / "o.py").write_text(textwrap.dedent(oracle_body))
    dig = hashlib.sha256((enc / "asp" / "p.lp").read_bytes()).hexdigest()
    odig = hashlib.sha256((enc / "oracle" / "o.py").read_bytes()).hexdigest()
    if corrupt_digest:
        odig = "0" * 64
    manifest = {"version": 1, "assets": [{
        "key": "k", "kind": kind, "lang": "asp", "payload": "asp/p.lp", "digest": dig,
        "oracle": "oracle/o.py", "oracle_digest": odig, "binds": {"cause": cause, "effect": effect}}]}
    (enc / "manifest.json").write_text(json.dumps(manifest))
    return ExecutorRegistry.load(enc / "manifest.json")


_GREEN = 'import json,sys; open(sys.argv[1]); print(json.dumps({"result":"GREEN"}))'
_RED = 'import json,sys; open(sys.argv[1]); print(json.dumps({"result":"RED","reason":"no"}))'


# ---- registry integrity -------------------------------------------------------------------
def test_registry_loads_stub_manifest():
    reg = ExecutorRegistry.load(STUB_MANIFEST)
    assert "stub-green" in reg.keys() and "stub-red" in reg.keys()


def test_registry_verify_payload_ok():
    ExecutorRegistry.load(STUB_MANIFEST).verify_payload("stub-green")  # no raise


def test_registry_digest_mismatch_refuses(tmp_path):
    reg = _make_registry(tmp_path, oracle_body=_GREEN, corrupt_digest=True)
    with pytest.raises(RegistryError):
        reg.verify_payload("k")


def test_registry_tampered_payload_after_load_refuses(tmp_path):
    reg = _make_registry(tmp_path, oracle_body=_GREEN, payload="original")
    (tmp_path / "asp" / "p.lp").write_text("TAMPERED")  # swap payload after the manifest pinned it
    with pytest.raises(RegistryError):
        reg.verify_payload("k")


# ---- oracle sandbox: fail-closed ----------------------------------------------------------
def test_oracle_green_and_red():
    reg = ExecutorRegistry.load(STUB_MANIFEST)
    assert run_oracle("stub-green", reg)["result"] == "GREEN"
    assert run_oracle("stub-red", reg)["result"] == "RED"


def test_oracle_nonzero_exit_is_error(tmp_path):
    reg = _make_registry(tmp_path, oracle_body='import sys; sys.exit(3)')
    assert run_oracle("k", reg)["result"] == "ERROR"


def test_oracle_non_json_is_error(tmp_path):
    reg = _make_registry(tmp_path, oracle_body='print("not json at all")')
    assert run_oracle("k", reg)["result"] == "ERROR"


def test_oracle_multiline_is_error(tmp_path):
    reg = _make_registry(tmp_path, oracle_body='print("{}"); print("{}")')
    assert run_oracle("k", reg)["result"] == "ERROR"


def test_oracle_wrong_result_value_is_error(tmp_path):
    reg = _make_registry(tmp_path, oracle_body='import json; print(json.dumps({"result":"MAYBE"}))')
    assert run_oracle("k", reg)["result"] == "ERROR"


def test_oracle_digest_mismatch_is_error_not_run(tmp_path):
    reg = _make_registry(tmp_path, oracle_body=_GREEN, corrupt_digest=True)
    assert run_oracle("k", reg)["result"] == "ERROR"


# ---- attach (arm) -------------------------------------------------------------------------
def test_attach_arms_without_changing_formality_or_identity():
    reg = ExecutorRegistry.load(STUB_MANIFEST)
    edge = _mined_edge()
    armed = attach_executor(edge, "stub-green", reg)
    assert armed["formality"] == "fuzzy"                      # formality UNCHANGED
    assert armed["directness"] == "script" and armed["strictness"] == "hard"
    assert armed["executor"]["ref"] == "stub-green" and armed["executor_verified"] is False
    from ralph_portable.causal_edges import edge_identity
    assert edge_identity(armed) == edge_identity(edge)       # identity preserved


def test_attach_rejects_binds_mismatch():
    reg = ExecutorRegistry.load(STUB_MANIFEST)
    edge = _mined_edge(cause="unrelated_cause", effect="unrelated_effect")
    with pytest.raises(ValueError):
        attach_executor(edge, "stub-green", reg)  # encoding binds pin/build, not the edge's pair


# ---- the RUNG FIREWALL (BB #775) ----------------------------------------------------------
def _to_evidential(store, ident):
    for _ in range(6):
        store.record_evidence(ident, surprise(0.34, True))
    store.apply_promotion(ident, store.record_evidence(ident, surprise(0.34, True)))


def test_operational_confirms_cap_at_evidential():
    store = InMemoryCausalEdgeStore()
    ident = store.put(_mined_edge())
    _to_evidential(store, ident)
    assert store.get(ident)["formality"] == "evidential"
    # keep piling operational confirms — must NEVER reach formal without an oracle proof
    for _ in range(20):
        prop = store.record_evidence(ident, surprise(0.67, True))
        store.apply_promotion(ident, prop)
    assert store.get(ident)["formality"] == "evidential", "repetition alone must not mint formal"


def test_evidential_to_formal_requires_oracle_proof():
    reg = ExecutorRegistry.load(STUB_MANIFEST)
    store = InMemoryCausalEdgeStore()
    ident = store.put(_mined_edge())
    _to_evidential(store, ident)
    store.attach_executor(ident, "stub-green", reg)  # armed but UNVERIFIED
    prop = store.record_evidence(ident, surprise(0.67, True))
    assert prop["action"] == "hold" and "requires a verified formal executor" in prop["reason"]
    # a GREEN proof unlocks it
    res = store.verify_and_record(ident, "stub-green", reg)
    assert res["oracle"]["result"] == "GREEN" and store.get(ident)["executor_verified"] is True
    store.apply_promotion(ident, res["proposal"])
    e = store.get(ident)
    assert e["formality"] == "formal" and is_guaranteed(e) and edge_certainty(e) == 1.0


def test_apply_promotion_refuses_formal_without_verified_executor():
    store = InMemoryCausalEdgeStore()
    ident = store.put(_mined_edge())
    _to_evidential(store, ident)
    # hand-craft a bogus promote proposal to formal; store must refuse (belt-and-suspenders)
    with pytest.raises(ValueError):
        store.apply_promotion(ident, {"action": "promote", "from": "evidential", "to": "formal"})


def test_red_oracle_records_no_support_and_no_verification():
    reg = ExecutorRegistry.load(STUB_MANIFEST)
    store = InMemoryCausalEdgeStore()
    edge = _mined_edge(cause="skip_tests", effect="ship_safely")
    ident = store.put(edge)
    _to_evidential(store, ident)
    support_before = store.get(ident)["support_count"]
    res = store.verify_and_record(ident, "stub-red", reg)
    assert res["oracle"]["result"] == "RED"
    assert store.get(ident).get("executor_verified") is not True
    assert store.get(ident)["support_count"] == support_before, "a RED proof must not earn support"


# ---- the LOOP FIREWALL: the running loop can never arm/verify/promote ----------------------
def test_loop_cannot_reach_formal_actuation():
    """The mirror of Slice-3's test_E. The autonomous loop's modules must NOT reference any
    formal-actuation function — arm/verify/promote are operator/CI-only, off the hot path — so
    'I do not act on principles' stays literally true even with the formal layer present."""
    import ast as _ast
    loop_side = ["runner/loop.py", "runner/causal_sync.py", "ralph_portable/principle_mining.py"]
    banned = ("attach_executor", "verify_and_record", "apply_promotion", "run_oracle",
              "formal_proof_evidence", "ralph_portable.formal", "oracle_harness", "executor_registry")
    offenders = []
    for rel in loop_side:
        text = (REPO / rel).read_text()
        for token in banned:
            if token in text:
                offenders.append(f"{rel} references {token}")
        # Stronger than a string scan: AST import-graph — no loop-side module may import the formal
        # package by any form (import / from-import), so actuation is unreachable via imports too.
        for node in _ast.walk(_ast.parse(text)):
            mods = []
            if isinstance(node, _ast.Import):
                mods = [n.name for n in node.names]
            elif isinstance(node, _ast.ImportFrom):
                mods = [node.module or ""]
            if any(m.startswith("ralph_portable.formal") for m in mods):
                offenders.append(f"{rel} imports the formal package")
    assert not offenders, f"loop path must not reach formal actuation: {offenders}"


def test_oracle_reason_is_capped_no_exfil(tmp_path):
    # An oracle's `reason` is untrusted; a huge/secret-bearing reason must be hard-capped before it
    # reaches the evidence record (review MED: reason was an exfil channel into the edge/UI).
    big = "S" * 5000
    reg = _make_registry(tmp_path, oracle_body=f'import json; print(json.dumps({{"result":"GREEN","reason":"{big}"}}))')
    res = run_oracle("k", reg)
    assert res["result"] == "GREEN"
    assert res["reason"] is not None and len(res["reason"]) <= 200, "oracle reason must be hard-capped"
    from ralph_portable.causal_edges import formal_proof_evidence
    ev = formal_proof_evidence(res)
    assert len(ev["reason_untrusted"]) <= 200


def test_registry_rejects_path_escape(tmp_path):
    # A manifest pointing an oracle/payload OUTSIDE the registry root must be refused (review LOW):
    (tmp_path / "asp").mkdir()
    (tmp_path / "asp" / "p.lp").write_text("x")
    outside = tmp_path.parent / "evil_oracle.py"
    outside.write_text('import json; print(json.dumps({"result":"GREEN"}))')
    import hashlib as _h
    manifest = {"version": 1, "assets": [{
        "key": "k", "kind": "constraint", "lang": "asp", "payload": "asp/p.lp",
        "digest": _h.sha256((tmp_path / "asp" / "p.lp").read_bytes()).hexdigest(),
        "oracle": "../evil_oracle.py",  # escapes the registry root
        "oracle_digest": _h.sha256(outside.read_bytes()).hexdigest(),
        "binds": {"cause": "a", "effect": "b"}}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    reg = ExecutorRegistry.load(tmp_path / "manifest.json")
    with pytest.raises(RegistryError):
        reg.verify_payload("k")  # path escape -> refused, the outside oracle never runs
