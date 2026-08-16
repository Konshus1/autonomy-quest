"""W1a gated deploy pathway (task #6087) — the pluggable deploy adapters.

The mock adapter proves deploy + rollback with no cloud. The ACA adapter's ``az containerapp`` command
construction is proven with an INJECTED runner (no real Azure, no credential), and we assert the
adapter never forwards a credential on the az argv.
"""
import pytest

from ralph_portable import deploy_adapters as da


# --- mock adapter: deploy + real rollback ------------------------------------
def test_mock_deploy_and_rollback() -> None:
    ad = da.MockDeployAdapter()
    assert ad.supports_rollback is True
    assert ad.current_revision(target="t") == ""

    first = ad.deploy(target="t", ref="reg/app:" + "a" * 40, sha="a" * 40)
    assert first.ok and first.revision and first.previous_revision == ""
    assert first.url == "https://t.mock.internal"

    second = ad.deploy(target="t", ref="reg/app:" + "b" * 40, sha="b" * 40)
    assert second.previous_revision == first.revision       # records what was live before
    assert ad.current_revision(target="t") == second.revision

    rb = ad.rollback(target="t", revision=first.revision)
    assert rb.ok and rb.revision == first.revision
    assert ad.current_revision(target="t") == first.revision


def test_mock_rollback_unknown_revision_fails() -> None:
    ad = da.MockDeployAdapter()
    ad.deploy(target="t", ref="x", sha="a" * 40)
    rb = ad.rollback(target="t", revision="never-existed")
    assert rb.ok is False


# --- ACA adapter: command construction with an injected runner ---------------
class _FakeAz:
    """Records az invocations and returns scripted stdout, so no real Azure is touched."""

    def __init__(self, revision="app--aaaaaaaa-1", fqdn="app.happy.azurecontainerapps.io"):
        self.calls: list[list[str]] = []
        self._revision = revision
        self._fqdn = fqdn

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        joined = " ".join(argv)
        if "revision list" in joined:
            return self._revision + "\n"
        if "ingress.fqdn" in joined:
            return self._fqdn + "\n"
        return ""


def test_aca_deploy_builds_containerapp_commands() -> None:
    fake = _FakeAz()
    ad = da.AcaDeployAdapter(app="myapp", resource_group="myrg", runner=fake)
    out = ad.deploy(target="prod", ref="reg/app@sha256:" + "c" * 64, sha="sha256:" + "c" * 64)

    assert out.ok
    assert out.url == "https://app.happy.azurecontainerapps.io"
    # an `az containerapp update --image ... --revision-suffix ...` was issued
    update = next(c for c in fake.calls if "update" in c)
    assert update[:2] == ["az", "containerapp"]
    assert "--image" in update and "reg/app@sha256:" + "c" * 64 in update
    assert "--revision-suffix" in update


def test_aca_never_forwards_a_credential() -> None:
    fake = _FakeAz()
    ad = da.AcaDeployAdapter(app="myapp", resource_group="myrg", runner=fake)
    ad.deploy(target="prod", ref="reg/app:" + "d" * 40, sha="d" * 40)
    # No az argv token ever looks like a credential/secret — az authenticates from the host login.
    for call in fake.calls:
        for tok in call:
            up = str(tok).upper()
            assert "SECRET" not in up and "PASSWORD" not in up and "CREDENTIAL" not in up
            assert "CLIENT_SECRET" not in up


def test_aca_rollback_activates_previous_revision() -> None:
    fake = _FakeAz()
    ad = da.AcaDeployAdapter(app="myapp", resource_group="myrg", runner=fake)
    rb = ad.rollback(target="prod", revision="app--prev-1")
    assert rb.ok
    activate = next(c for c in fake.calls if "activate" in c)
    assert "--revision" in activate and "app--prev-1" in activate


def test_aca_requires_concrete_app_and_rg() -> None:
    with pytest.raises(ValueError):
        da.AcaDeployAdapter(app="", resource_group="rg")


# --- factory: pluggable + fail-closed ----------------------------------------
def test_get_adapter_selects_by_name() -> None:
    assert isinstance(da.get_adapter("mock"), da.MockDeployAdapter)
    assert isinstance(
        da.get_adapter("aca", app="a", resource_group="r", runner=_FakeAz()), da.AcaDeployAdapter)


def test_get_adapter_unknown_fails_closed() -> None:
    with pytest.raises(ValueError):
        da.get_adapter("rogue-cloud")


def test_static_host_is_deferred_not_silent() -> None:
    ad = da.get_adapter("static_host")
    assert ad.supports_rollback is True
    with pytest.raises(NotImplementedError):
        ad.deploy(target="t", ref="x", sha="a" * 40)
