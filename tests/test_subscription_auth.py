from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from management.api import auth as auth_module
from runner.executor import SubscriptionExecutor


def _result(returncode: int, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_subscription_executor_requires_real_login_at_construction():
    """Negative control: installed binary + failed login must still raise."""
    with patch("runner.executor.shutil.which", return_value="/usr/bin/codex"), \
         patch("runner.executor.subprocess.run", return_value=_result(1, stderr="Not logged in")) as run:
        with pytest.raises(RuntimeError, match="not authenticated with an allowed subscription"):
            SubscriptionExecutor("codex")
    assert run.call_args.args[0] == ["codex", "login", "status"]


def test_subscription_executor_constructs_only_after_login_status_succeeds():
    with patch("runner.executor.shutil.which", return_value="/usr/bin/codex"), \
         patch("runner.executor.subprocess.run", return_value=_result(0, stderr="Logged in using ChatGPT")) as run:
        executor = SubscriptionExecutor("codex")
    assert executor.engine == "codex"



def test_subscription_executor_rejects_exit_zero_api_key_login():
    """Negative control: Codex API-key auth is valid login but forbidden metered billing."""
    with patch("runner.executor.shutil.which", return_value="/usr/bin/codex"), \
         patch("runner.executor.subprocess.run", return_value=_result(0, stderr="Logged in using an API key")):
        with pytest.raises(RuntimeError, match="API-key auth is rejected"):
            SubscriptionExecutor("codex")


def test_device_code_parser_accepts_real_codex_four_by_five_shape():
    match = auth_module._CODE.search("  HQE5-2MQ85  ")
    assert match is not None
    assert match.group(0) == "HQE5-2MQ85"


def test_auth_status_contract_never_contains_tokens(monkeypatch):
    monkeypatch.setattr(auth_module, "_login_status", lambda: (False, "disconnected"))
    login = auth_module.DeviceLogin(
        verification_url="https://auth.openai.com/codex/device",
        user_code="HQE5-2MQ85",
    )
    result = login.status()
    assert set(result) == {
        "state", "connected", "pending", "verification_url", "user_code", "error"
    }
    assert not any("token" in key.lower() for key in result)


def _strict_schema_errors(schema, path="$"):
    errors = []
    if schema.get("type") == "object":
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is not False:
            errors.append(f"{path}: additionalProperties must be false")
        if set(schema.get("required", [])) != set(props):
            errors.append(f"{path}: required must include every property exactly")
        for key, child in props.items():
            errors.extend(_strict_schema_errors(child, f"{path}.{key}"))
    if schema.get("type") == "array":
        errors.extend(_strict_schema_errors(schema.get("items", {}), f"{path}[]"))
    return errors


def test_codex_native_response_schemas_are_strict_compatible():
    from runner import prompts
    for name in ("DECIDE_SCHEMA", "ACT_SCHEMA", "REFLECT_SCHEMA", "EXPLORE_SCHEMA"):
        assert _strict_schema_errors(getattr(prompts, name)) == [], name
