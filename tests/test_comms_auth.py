"""Authentication, principal derivation, and ACL tests (#4834 comms Phase 0, design §8).

Proves: identity is derived from the credential (never a claimed field); an unknown credential is
rejected; the kind allowlist is enforced; and a principal cannot publish to another instance's
channel or directly target an out-of-lineage instance (cross-instance ACL denial).
"""

from __future__ import annotations

import pytest

from management.api.comms_auth import (
    AclDenied,
    CommsAuthenticator,
    Unauthenticated,
)


PARENT = "urn:uuid:parent"
SELF = "urn:uuid:self"
OTHER = "urn:uuid:other"


def _auth():
    return CommsAuthenticator(credentials={
        "tok-self": {
            "principal_id": f"instance:{SELF}", "origin_instance_id": SELF,
            "lineage": [SELF, PARENT], "kinds": ["status.report", "experiment.result"],
            "operator": False,
        },
        "tok-op": {
            "principal_id": "operator:local", "origin_instance_id": "operator",
            "lineage": [], "kinds": ["operator.message"], "operator": True,
        },
    })


class _Headers(dict):
    """Minimal case-insensitive-ish header shim for .get()."""


def _bearer(tok):
    return _Headers({"authorization": f"Bearer {tok}"})


# --- authentication + derivation --------------------------------------------
def test_valid_credential_derives_identity_from_the_token():
    p = _auth().authenticate(_bearer("tok-self"))
    assert p.origin_instance_id == SELF
    assert p.principal_id == f"instance:{SELF}"
    assert not p.is_operator


def test_missing_credential_is_unauthenticated():
    with pytest.raises(Unauthenticated):
        _auth().authenticate(_Headers({}))


def test_unknown_credential_is_unauthenticated():
    with pytest.raises(Unauthenticated):
        _auth().authenticate(_bearer("tok-bogus"))


def test_from_env_builds_the_instance_and_operator_credentials():
    auth = CommsAuthenticator.from_env({
        "AQ_INSTANCE_ID": SELF, "AQ_COMMS_INSTANCE_TOKEN": "itok",
        "AQ_COMMS_PARENT_INSTANCE_ID": PARENT, "AQ_COMMS_OPERATOR_TOKEN": "otok",
    })
    inst = auth.authenticate(_bearer("itok"))
    assert inst.origin_instance_id == SELF and PARENT in inst.lineage
    op = auth.authenticate(_bearer("otok"))
    assert op.is_operator


# --- ACL: allowed publishes --------------------------------------------------
def test_instance_may_publish_to_its_own_channel():
    auth = _auth()
    p = auth.authenticate(_bearer("tok-self"))
    auth.authorize_publish(p, channel=f"instance/{SELF}/local", kind="status.report")


def test_instance_may_publish_to_a_lineage_channel_of_its_parent():
    auth = _auth()
    p = auth.authenticate(_bearer("tok-self"))
    auth.authorize_publish(p, channel=f"lineage/{PARENT}/experiments", kind="experiment.result")


def test_operator_may_publish_operator_message_anywhere():
    auth = _auth()
    p = auth.authenticate(_bearer("tok-op"))
    auth.authorize_publish(p, channel=f"instance/{OTHER}/local", kind="operator.message")


# --- ACL: denials ------------------------------------------------------------
def test_cross_instance_channel_is_denied():
    auth = _auth()
    p = auth.authenticate(_bearer("tok-self"))
    with pytest.raises(AclDenied):
        auth.authorize_publish(p, channel=f"instance/{OTHER}/local", kind="status.report")


def test_out_of_lineage_channel_is_denied():
    auth = _auth()
    p = auth.authenticate(_bearer("tok-self"))
    with pytest.raises(AclDenied):
        auth.authorize_publish(p, channel=f"lineage/{OTHER}/experiments", kind="experiment.result")


def test_direct_target_outside_lineage_is_denied():
    auth = _auth()
    p = auth.authenticate(_bearer("tok-self"))
    with pytest.raises(AclDenied):
        auth.authorize_publish(p, channel=f"instance/{SELF}/local",
                               kind="status.report", target_instance_id=OTHER)


def test_kind_not_in_allowlist_is_denied():
    auth = _auth()
    p = auth.authenticate(_bearer("tok-self"))
    with pytest.raises(AclDenied):
        auth.authorize_publish(p, channel=f"instance/{SELF}/local", kind="operator.message")


def test_operator_cannot_send_non_operator_kinds():
    auth = _auth()
    p = auth.authenticate(_bearer("tok-op"))
    with pytest.raises(AclDenied):
        auth.authorize_publish(p, channel=f"instance/{SELF}/local", kind="work.request")


def test_bare_fleet_channel_requires_membership():
    auth = _auth()
    p = auth.authenticate(_bearer("tok-self"))
    with pytest.raises(AclDenied):
        auth.authorize_publish(p, channel="fleet/global", kind="status.report")
