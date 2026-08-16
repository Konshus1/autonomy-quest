"""W1a gated deploy pathway (task #6087) — the HOST executor: gate + exact-SHA + receipt + rollback.

RED-FIRST, the load-bearing safety proofs:
  * a guest-emitted deploy request does NOT deploy by itself — the host executor ESCALATES when the
    `deploy` gate is closed, and the adapter is never asked to deploy (guest-never / host-executes);
  * an un-opened `deploy` gate escalates (no silent deploy) and emits a Slice-3 authority request;
  * exact-SHA is enforced host-side (a request for a different SHA than built fails closed);
  * the Azure credential is unreachable by the act sandbox (not in executor._AGENT_ENV_NAMES) and is
    guest-forbidden in the modification allowlist;
  * a mock adapter proves request -> host-validate -> deploy -> receipt -> rollback;
  * flag OFF => inert.
"""
from ralph_portable import deploy_adapters as da
from ralph_portable import host_deploy_stack as hds
from ralph_portable.deploy_request import FLAG

from runner.authority_envelope import CLASS_DEPLOY, DISPOSITION_OPEN, from_config


ON = {FLAG: "1"}
SHA = "a" * 40


def _packet(**extra):
    p = {
        "target": "aq-marketing-site",
        "adapter": "mock",
        "requested_sha": SHA,
        "artifact": {"git_sha": SHA, "build_context": "."},
        "requester_instance_id": "aq-parent-1",
        "mission_id": "mission-ship-site",
    }
    p.update(extra)
    return p


def _open_deploy_envelope():
    """An operator envelope that has OPENED the deploy gate."""
    return from_config({CLASS_DEPLOY: DISPOSITION_OPEN})


class _SpyAdapter(da.MockDeployAdapter):
    """A mock adapter that records whether deploy() was ever called."""

    def __init__(self):
        super().__init__()
        self.deploy_calls = 0

    def deploy(self, **kwargs):
        self.deploy_calls += 1
        return super().deploy(**kwargs)


# --- flag OFF => inert -------------------------------------------------------
def test_flag_off_is_inert() -> None:
    spy = _SpyAdapter()
    res = hds.execute_deploy(_packet(), built_sha=SHA, envelope=_open_deploy_envelope(),
                             adapter=spy, env={})
    assert res["status"] == hds.STATUS_DISABLED
    assert res["deployed"] is False
    assert spy.deploy_calls == 0


# --- RED: a guest request does not deploy itself; closed gate escalates -------
def test_closed_deploy_gate_escalates_and_does_not_deploy() -> None:
    spy = _SpyAdapter()
    # No envelope supplied => fully-gated DEFAULT => escalate.
    res = hds.execute_deploy(_packet(), built_sha=SHA, envelope=None, adapter=spy, env=ON)
    assert res["status"] == hds.STATUS_ESCALATED
    assert res["deployed"] is False
    assert spy.deploy_calls == 0                          # the host NEVER deployed
    # a Slice-3 authority-change request was emitted, naming the deploy class
    ar = res["authority_request"]
    assert CLASS_DEPLOY in ar["gated_classes"]
    assert ar["requests"] and ar["requests"][0]["capability_class"] == CLASS_DEPLOY
    assert ar["requests"][0]["internally_authorized"] is False   # never self-authorized


def test_deploy_gate_gated_even_with_explicit_gated_envelope() -> None:
    spy = _SpyAdapter()
    gated = from_config({CLASS_DEPLOY: "gated"})
    res = hds.execute_deploy(_packet(), built_sha=SHA, envelope=gated, adapter=spy, env=ON)
    assert res["status"] == hds.STATUS_ESCALATED and spy.deploy_calls == 0


# --- GREEN: opened gate deploys; receipt; rollback ---------------------------
def test_opened_gate_deploys_and_records_receipt() -> None:
    spy = _SpyAdapter()
    res = hds.execute_deploy(_packet(), built_sha=SHA, envelope=_open_deploy_envelope(),
                             adapter=spy, env=ON)
    assert res["status"] == hds.STATUS_DEPLOYED and res["deployed"] is True
    assert spy.deploy_calls == 1
    r = res["receipt"]
    assert r["deployed_sha"] == SHA
    assert r["target"] == "aq-marketing-site"
    assert r["revision"] and r["url"] == "https://aq-marketing-site.mock.internal"
    assert r["reversible"] is True and r["reversibility"] == hds.REVERSIBLE
    assert r["receipt_version"] == hds.RECEIPT_VERSION
    assert r["timestamp"]


def test_deploy_then_rollback_roundtrip() -> None:
    adapter = da.MockDeployAdapter()
    env = _open_deploy_envelope()
    first = hds.execute_deploy(_packet(), built_sha=SHA, envelope=env, adapter=adapter, env=ON)
    second_sha = "b" * 40
    second = hds.execute_deploy(
        _packet(requested_sha=second_sha, artifact={"git_sha": second_sha, "build_context": "."}),
        built_sha=second_sha, envelope=env, adapter=adapter, env=ON)
    assert adapter.current_revision(target="aq-marketing-site") == second["receipt"]["revision"]

    # roll the SECOND deploy back to the first revision recorded in its receipt
    rb = hds.rollback_deploy(second["receipt"], adapter=adapter, env=ON)
    assert rb["ok"] and rb["status"] == "rolled_back"
    assert rb["revision"] == first["receipt"]["revision"]
    assert adapter.current_revision(target="aq-marketing-site") == first["receipt"]["revision"]


# --- exact-SHA enforced host-side --------------------------------------------
def test_sha_mismatch_fails_closed() -> None:
    spy = _SpyAdapter()
    # request asks for SHA-a, but the host actually built SHA-b -> refuse, no deploy
    res = hds.execute_deploy(_packet(), built_sha="b" * 40, envelope=_open_deploy_envelope(),
                             adapter=spy, env=ON)
    assert res["status"] == hds.STATUS_SHA_MISMATCH
    assert res["deployed"] is False and spy.deploy_calls == 0


def test_non_sha_built_ref_fails_closed() -> None:
    spy = _SpyAdapter()
    res = hds.execute_deploy(_packet(), built_sha="latest", envelope=_open_deploy_envelope(),
                             adapter=spy, env=ON)
    assert res["status"] == hds.STATUS_SHA_MISMATCH and spy.deploy_calls == 0


def test_invalid_request_rejected_before_gate() -> None:
    spy = _SpyAdapter()
    bad = _packet(requested_sha="latest", artifact={"image": "reg/app:latest"})
    res = hds.execute_deploy(bad, built_sha="latest", envelope=_open_deploy_envelope(),
                             adapter=spy, env=ON)
    assert res["status"] == hds.STATUS_INVALID and spy.deploy_calls == 0


# --- reversibility classified honestly ---------------------------------------
def test_irreversible_when_adapter_lacks_rollback() -> None:
    class _NoRollback(da.MockDeployAdapter):
        supports_rollback = False

    res = hds.execute_deploy(_packet(), built_sha=SHA, envelope=_open_deploy_envelope(),
                             adapter=_NoRollback(), env=ON)
    assert res["receipt"]["reversibility"] == hds.IRREVERSIBLE_EXTERNAL
    # and rollback then refuses on that receipt
    rb = hds.rollback_deploy(res["receipt"], adapter=_NoRollback(), env=ON)
    assert rb["ok"] is False and rb["status"] == "rollback_unsupported"


# --- credential unreachable by the act sandbox + guest-forbidden -------------
def test_deploy_credential_absent_from_act_sandbox_env() -> None:
    from runner.executor import _AGENT_ENV_NAMES, _agent_env

    for name in ("AZURE_CLIENT_SECRET", "AQ_DEPLOY_AZURE_CREDENTIAL",
                 "AZURE_TENANT_ID", "AZURE_CLIENT_ID"):
        assert name not in _AGENT_ENV_NAMES, f"{name} must not be in the act sandbox allowlist"

    # And deny-by-default really strips it: an act env built from a source carrying the credential
    # never contains it.
    source = {"PATH": "/usr/bin", "AQ_DEPLOY_AZURE_CREDENTIAL": "super-secret",
              "AZURE_CLIENT_SECRET": "sp-secret"}
    child = _agent_env(source, include_actor_db=False)
    assert "AQ_DEPLOY_AZURE_CREDENTIAL" not in child
    assert "AZURE_CLIENT_SECRET" not in child


def test_deploy_credential_is_guest_forbidden_in_modification_allowlist() -> None:
    from ralph_portable.replication_modifications import forbidden_key_reason

    # A replica must never set a deploy credential on its child via the config-delta path.
    for key in ("AQ_DEPLOY_AZURE_CREDENTIAL", "AZURE_CLIENT_SECRET", "DEPLOY_TOKEN"):
        assert forbidden_key_reason(key) is not None, key
