"""tbagents bridge: DEFAULT-OFF, REDACTED, ONE-WAY, HOST-ONLY (#4834 comms Phase 5, design §5-E/§10).

The bridge is an inert operator-view tool. These tests prove: it never contacts tbagents by default;
it redacts secrets/tokens/host-local ports/URLs/paths; it mirrors only the operator-view channel set
(never the command plane); and it is one-way (read/mirror, no write-back). Its host-only import
posture is proven separately in tests/test_import_firewall.py.
"""

from __future__ import annotations

from scripts import host_tbagents_bridge as bridge


def test_default_off_and_fail_closed_without_token():
    assert bridge.bridge_enabled({}) is False
    # A truthy flag without a service token stays OFF (fail-closed: no token, no mirror).
    assert bridge.bridge_enabled({"AQ_TBAGENTS_BRIDGE": "1"}) is False
    assert bridge.bridge_enabled({"AQ_TBAGENTS_BRIDGE": "1", "INTERNAL_SERVICE_TOKEN": "t"}) is True
    assert bridge.bridge_enabled({"AQ_TBAGENTS_BRIDGE": "0", "INTERNAL_SERVICE_TOKEN": "t"}) is False


def test_only_operator_view_channels_are_mirrored():
    envs = [
        {"id": "1", "kind": "status.report", "channel": "instance/a/local",
         "trust": "untrusted_claim", "payload": {"text": "ok"}},
        {"id": "2", "kind": "experiment.result", "channel": "lineage/p/experiments",
         "trust": "untrusted_claim", "payload": {"summary": "done"}},
        # The command plane is NEVER mirrored.
        {"id": "3", "kind": "work.request", "channel": "instance/a/inbox", "payload": {"goal": "x"}},
        {"id": "4", "kind": "receipt", "channel": "lineage/p/status", "payload": {"disposition": "stored"}},
    ]
    mirror = bridge.select_mirror(envs)
    mirrored_ids = {m["aq_envelope_id"] for m in mirror}
    assert mirrored_ids == {"1", "2"}  # work.request + receipt excluded


def test_redaction_strips_secrets_ports_urls_paths():
    env = {"id": "9", "kind": "status.report", "channel": "instance/a/local",
           "trust": "untrusted_claim", "payload": {
               "text": "see http://127.0.0.1:8090/x with Bearer abcdefটtoken and /Users/k/secret.txt",
               "auth_token": "supersecret", "app_mgmt_port": 8091,
               "nested": {"password": "p", "note": "localhost:5432 down"}}}
    rec = bridge.redact_envelope(env)
    p = rec["payload"]
    # Sensitive keys are DROPPED entirely.
    assert "auth_token" not in p and "app_mgmt_port" not in p
    assert "password" not in p["nested"]
    # Free text is scrubbed: no url, no host:port, no absolute path leaks.
    assert "http://127.0.0.1:8090" not in p["text"]
    assert "127.0.0.1:8090" not in p["text"]
    assert "/Users/k/secret.txt" not in p["text"]
    assert "localhost:5432" not in p["nested"]["note"]
    # It carries the non-authoritative mirror note.
    assert "NOT authoritative" in rec["mirror_note"]


def test_broadened_redaction_catches_the_review_leak_strings():
    """Finding 2 red-first cases: the exact strings the reviewer showed leaking must now be scrubbed —
    a NON-localhost IPv4:port, a short pw=value secret, and an AWS-key-shaped credential."""
    leaky = "db at 10.0.0.5:5432 and secret pw=hunter2 and key AKIAIOSFODNN7EXAMPLE99"
    out = bridge.scrub_text(leaky)
    for leak in ("10.0.0.5", "5432", "hunter2", "AKIAIOSFODNN7EXAMPLE99"):
        assert leak not in out, f"redaction leaked {leak!r}: {out!r}"


def test_redaction_catches_generic_secret_kv_and_dsn():
    for leaky, secret in (
        ("password=s3cr3tValue123", "s3cr3tValue123"),
        ("token: abcdEFGHijklMNOP", "abcdEFGHijklMNOP"),
        ("api_key=AKIA1234567890ABCD", "AKIA1234567890ABCD"),
        ("connect postgres://user:pw@10.1.2.3:5432/db", "10.1.2.3"),
    ):
        out = bridge.scrub_text(leaky)
        assert secret not in out, f"leaked {secret!r} from {leaky!r}: {out!r}"


def test_digest_references_survive_redaction():
    # A sha256 digest is safe evidence and must NOT be scrubbed as a token.
    digest = "sha256:" + "a" * 64
    env = {"id": "d", "kind": "experiment.result", "channel": "lineage/p/experiments",
           "trust": "untrusted_claim", "payload": {"summary": "r",
                                                    "artifact_refs": [{"digest": digest}]}}
    rec = bridge.redact_envelope(env)
    assert rec["payload"]["artifact_refs"][0]["digest"] == digest


def test_bridge_is_read_only_no_write_back_surface():
    # The pure core is a projection: it exposes select_mirror/redact_envelope and NO function that
    # writes into an AQ store/queue. This is the structural one-way guarantee.
    public = {n for n in dir(bridge) if not n.startswith("_")}
    for writer in ("create_envelope", "create_work", "set_verification", "set_import",
                   "relay_envelope", "add_replication", "add_merge"):
        assert writer not in public, f"the bridge must have no AQ write path ({writer})"
