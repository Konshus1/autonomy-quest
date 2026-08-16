"""W1a gated deploy pathway (task #6087) — REVIEW FINDING HIGH #1, now FIXED (red -> green).

The de-correlated reviewer found that image exact-SHA was a SUBSTRING match: a guest could pass
``attacker.example.com/backdoor:release-<builtSHA>``, validate clean, pass requested==built, and the
HOST would deploy the attacker image while the receipt claimed the legit SHA. Within one class-level
``deploy: open`` grant the guest chose the bytes.

THE FIX (proven here): the request names a git SHA ONLY — a guest ``image`` ref is REJECTED at
validation, so the guest can never express the attack. And the HOST resolves the git SHA to ITS OWN
recorded, digest-pinned, registry-allowlisted image and deploys THAT; a resolved image that isn't
digest-pinned, isn't allowlisted, or whose digest doesn't match the host's recorded digest FAILS
CLOSED. No guest string ever reaches the deployed bytes.
"""
from ralph_portable import deploy_adapters as da
from ralph_portable import deploy_request as dr
from ralph_portable import host_deploy_stack as hds
from ralph_portable.deploy_request import FLAG

from runner.authority_envelope import CLASS_DEPLOY, DISPOSITION_OPEN, from_config

ON = {FLAG: "1"}
SHA = "a" * 40
GOOD_DIGEST = "b" * 64
GOOD_IMAGE = f"registry.example.com/aq/site@sha256:{GOOD_DIGEST}"
ALLOWLIST = ("registry.example.com/aq/",)


def _open_env():
    return from_config({CLASS_DEPLOY: DISPOSITION_OPEN})


class _FakeAz:
    def __init__(self, revision="site--aaaaaaaa-1", fqdn="site.happy.azurecontainerapps.io"):
        self.calls: list[list[str]] = []
        self._revision, self._fqdn = revision, fqdn

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        j = " ".join(argv)
        if "revision list" in j:
            return self._revision + "\n"
        if "ingress.fqdn" in j:
            return self._fqdn + "\n"
        return ""


def _aca(fake=None):
    return da.AcaDeployAdapter(app="site", resource_group="rg", runner=fake or _FakeAz())


# --- RED reproduced -> now GREEN: the guest can't even express an image ref ---
def test_arbitrary_registry_image_embedding_the_sha_is_rejected() -> None:
    # (the reviewer's exact attack packet: an attacker registry with the SHA as a cosmetic tag)
    evil = {
        "target": "aq-marketing-site", "adapter": "aca", "requested_sha": SHA,
        "artifact": {"image": "attacker.example.com/backdoor:release-" + SHA},
        "requester_instance_id": "x", "mission_id": "m",
    }
    errors = dr.validate_deploy_request(evil)
    assert errors != []                                        # SECURE: does not validate clean
    assert any("must not carry an 'image'" in e for e in errors)


def test_host_ignores_any_notion_of_a_guest_image_and_deploys_its_own() -> None:
    """Even end-to-end: a legit git-SHA request deploys the HOST's recorded image, not anything the
    guest could name — the deployed ref is the host's digest-pinned allowlisted image."""
    fake = _FakeAz()
    resolver = hds.RecordedImageResolver({SHA: {"image": GOOD_IMAGE, "digest": GOOD_DIGEST}})
    packet = {
        "target": "aq-marketing-site", "adapter": "aca", "requested_sha": SHA,
        "artifact": {"git_sha": SHA, "build_context": "."},
        "requester_instance_id": "aq-1", "mission_id": "m",
    }
    res = hds.execute_deploy(packet, built_sha=SHA, envelope=_open_env(), adapter=_aca(fake),
                             image_resolver=resolver, allowlist=ALLOWLIST, env=ON)
    assert res["status"] == hds.STATUS_DEPLOYED
    r = res["receipt"]
    assert r["deployed_ref"] == GOOD_IMAGE                     # the host's image, digest-pinned
    assert r["deployed_digest"] == GOOD_DIGEST
    assert r["deployed_sha"] == SHA
    # the exact digest-pinned image was handed to `az`, never a guest string
    update = next(c for c in fake.calls if "update" in c)
    assert GOOD_IMAGE in update


# --- host-side fail-closed branches ------------------------------------------
def test_image_adapter_without_resolver_fails_closed() -> None:
    packet = {"target": "t", "adapter": "aca", "requested_sha": SHA,
              "artifact": {"git_sha": SHA}, "requester_instance_id": "x", "mission_id": "m"}
    res = hds.execute_deploy(packet, built_sha=SHA, envelope=_open_env(), adapter=_aca(),
                             image_resolver=None, allowlist=ALLOWLIST, env=ON)
    assert res["status"] == hds.STATUS_IMAGE_UNRESOLVED


def test_no_recorded_image_for_sha_fails_closed() -> None:
    resolver = hds.RecordedImageResolver({"c" * 40: {"image": GOOD_IMAGE, "digest": GOOD_DIGEST}})
    packet = {"target": "t", "adapter": "aca", "requested_sha": SHA,
              "artifact": {"git_sha": SHA}, "requester_instance_id": "x", "mission_id": "m"}
    res = hds.execute_deploy(packet, built_sha=SHA, envelope=_open_env(), adapter=_aca(),
                             image_resolver=resolver, allowlist=ALLOWLIST, env=ON)
    assert res["status"] == hds.STATUS_IMAGE_UNRESOLVED


def test_non_digest_pinned_image_fails_closed() -> None:
    resolver = hds.RecordedImageResolver(
        {SHA: {"image": "registry.example.com/aq/site:sometag", "digest": GOOD_DIGEST}})
    packet = {"target": "t", "adapter": "aca", "requested_sha": SHA,
              "artifact": {"git_sha": SHA}, "requester_instance_id": "x", "mission_id": "m"}
    res = hds.execute_deploy(packet, built_sha=SHA, envelope=_open_env(), adapter=_aca(),
                             image_resolver=resolver, allowlist=ALLOWLIST, env=ON)
    assert res["status"] == hds.STATUS_IMAGE_UNRESOLVED


def test_non_allowlisted_registry_fails_closed() -> None:
    resolver = hds.RecordedImageResolver(
        {SHA: {"image": f"attacker.example.com/backdoor@sha256:{GOOD_DIGEST}", "digest": GOOD_DIGEST}})
    packet = {"target": "t", "adapter": "aca", "requested_sha": SHA,
              "artifact": {"git_sha": SHA}, "requester_instance_id": "x", "mission_id": "m"}
    res = hds.execute_deploy(packet, built_sha=SHA, envelope=_open_env(), adapter=_aca(),
                             image_resolver=resolver, allowlist=ALLOWLIST, env=ON)
    assert res["status"] == hds.STATUS_IMAGE_UNRESOLVED
    assert "allowlist" in res["reason"]


def test_digest_not_matching_recorded_fails_closed_as_sha_mismatch() -> None:
    # the image ref embeds a DIFFERENT digest than the host recorded for this SHA
    resolver = hds.RecordedImageResolver(
        {SHA: {"image": f"registry.example.com/aq/site@sha256:{'d' * 64}", "digest": GOOD_DIGEST}})
    packet = {"target": "t", "adapter": "aca", "requested_sha": SHA,
              "artifact": {"git_sha": SHA}, "requester_instance_id": "x", "mission_id": "m"}
    res = hds.execute_deploy(packet, built_sha=SHA, envelope=_open_env(), adapter=_aca(),
                             image_resolver=resolver, allowlist=ALLOWLIST, env=ON)
    assert res["status"] == hds.STATUS_SHA_MISMATCH


def test_lookalike_registry_host_does_not_sneak_past_allowlist() -> None:
    # Operator allowlists a registry HOST written naturally, WITHOUT a trailing slash.
    # A look-alike host that merely has the allowed host as a boundary-less string prefix
    # (registry.example.com  vs  registry.example.com.evil.io) must NOT pass the allowlist.
    host_allowlist = ("registry.example.com",)
    resolver = hds.RecordedImageResolver(
        {SHA: {"image": f"registry.example.com.evil.io/backdoor@sha256:{GOOD_DIGEST}",
               "digest": GOOD_DIGEST}})
    packet = {"target": "t", "adapter": "aca", "requested_sha": SHA,
              "artifact": {"git_sha": SHA}, "requester_instance_id": "x", "mission_id": "m"}
    res = hds.execute_deploy(packet, built_sha=SHA, envelope=_open_env(), adapter=_aca(),
                             image_resolver=resolver, allowlist=host_allowlist, env=ON)
    assert res["status"] == hds.STATUS_IMAGE_UNRESOLVED
    assert "allowlist" in res["reason"]


def test_legit_repo_under_allowlisted_host_still_deploys() -> None:
    # regression guard: a genuine repo UNDER the allowlisted host (boundary-correct) still passes.
    host_allowlist = ("registry.example.com",)
    resolver = hds.RecordedImageResolver({SHA: {"image": GOOD_IMAGE, "digest": GOOD_DIGEST}})
    packet = {"target": "t", "adapter": "aca", "requested_sha": SHA,
              "artifact": {"git_sha": SHA}, "requester_instance_id": "x", "mission_id": "m"}
    res = hds.execute_deploy(packet, built_sha=SHA, envelope=_open_env(), adapter=_aca(),
                             image_resolver=resolver, allowlist=host_allowlist, env=ON)
    assert res["status"] == hds.STATUS_DEPLOYED


def test_empty_allowlist_fails_closed() -> None:
    resolver = hds.RecordedImageResolver({SHA: {"image": GOOD_IMAGE, "digest": GOOD_DIGEST}})
    packet = {"target": "t", "adapter": "aca", "requested_sha": SHA,
              "artifact": {"git_sha": SHA}, "requester_instance_id": "x", "mission_id": "m"}
    res = hds.execute_deploy(packet, built_sha=SHA, envelope=_open_env(), adapter=_aca(),
                             image_resolver=resolver, allowlist=(), env=ON)
    assert res["status"] == hds.STATUS_IMAGE_UNRESOLVED
