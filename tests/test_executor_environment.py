from runner.executor import _agent_env


def test_actor_environment_is_allowlisted_and_maps_only_restricted_db_principal():
    source = {
        "PATH": "/bin", "HOME": "/actor", "CODEX_HOME": "/auth",
        "HTTPS_PROXY": "http://proxy", "AQ_ACT_DB_URL": "postgresql://aq_actor@db/aq",
        "AQ_DB_URL": "postgresql://aq_loop:secret@db/aq",
        "AQ_GOVERNANCE_DB_URL": "postgresql://aq_governance:secret@db/aq",
        "AQ_GOVERNANCE_EVIDENCE_TOKEN": "evidence-secret",
        "AQ_GOVERNANCE_TOKEN": "promotion-secret",
        "AQ_GOVERNANCE_DECISION_TOKEN": "decision-secret",
        "AQ_GOVERNANCE_URL": "http://governance:8090",
        "AQ_INSTANCE_ID": "urn:uuid:11111111-1111-4111-8111-111111111111",
        "AQ_EVALUATOR_URL": "http://evaluator:8090",
        "AQ_EVALUATOR_TRIGGER_TOKEN": "trigger-secret",
        "OPENAI_API_KEY": "metered-secret",
        "FUTURE_AUTHORITY_CREDENTIAL": "new-secret",
    }
    child = _agent_env(source)
    assert child == {
        "PATH": "/bin", "HOME": "/actor", "CODEX_HOME": "/auth",
        "HTTPS_PROXY": "http://proxy", "AQ_DB_URL": "postgresql://aq_actor@db/aq",
        "NO_COLOR": "1",
    }
    assert all("secret" not in value for value in child.values())


def test_auth_probe_receives_no_actor_or_lifecycle_database_credential():
    child = _agent_env({"PATH": "/bin", "AQ_ACT_DB_URL": "actor", "AQ_DB_URL": "loop",
                        "AQ_GOVERNANCE_TOKEN": "govern"}, include_actor_db=False)
    assert child == {"PATH": "/bin", "NO_COLOR": "1"}
