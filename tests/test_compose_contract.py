from __future__ import annotations

import ast
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_compose_separates_migration_governance_and_untrusted_app_principals():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert set(compose["services"]) == {"postgres", "migrate", "governance", "evaluator", "app"}
    postgres = compose["services"]["postgres"]
    assert "image" in postgres and "@sha256:" in postgres["image"]
    assert "build" not in postgres
    assert postgres.get("ports", []) == []  # 5432 is internal by default
    assert set(compose["services"]["app"]["ports"]) == {"127.0.0.1:8080:8080", "127.0.0.1:8090:8090"}
    assert compose["services"]["governance"]["ports"] == ["127.0.0.1:8091:8090"]
    app_env = compose["services"]["app"]["environment"]
    assert set(k for k in app_env if "DB_URL" in k) == {"AQ_DB_URL", "AQ_ACT_DB_URL"}
    assert {k for k in app_env if k.startswith("AQ_GOVERNANCE")} == {
        "AQ_GOVERNANCE_URL", "AQ_GOVERNANCE_DECISION_TOKEN"}
    assert "AQ_GOVERNANCE_EVIDENCE_TOKEN" not in app_env
    assert "AQ_GOVERNANCE_TOKEN" not in app_env
    assert not any("MIGRATION" in k for k in app_env)
    governance_env = compose["services"]["governance"]["environment"]
    evaluator_env = compose["services"]["evaluator"]["environment"]
    assert "AQ_GOVERNANCE_DB_URL" in governance_env
    assert "AQ_EVALUATOR_TOKEN" not in governance_env
    assert "aq_governance:" in governance_env["AQ_GOVERNANCE_DB_URL"]
    assert "aq_evaluator:" in evaluator_env["AQ_GOVERNANCE_DB_URL"]
    assert "AQ_EVALUATOR_TOKEN" in evaluator_env
    assert "AQ_GOVERNANCE_TOKEN" not in evaluator_env
    assert set(compose["services"]["migrate"]["environment"]) == {
        "AQ_DB_URL", "AQ_LOOP_DB_PASSWORD", "AQ_ACTOR_DB_PASSWORD",
        "AQ_GOVERNANCE_DB_PASSWORD", "AQ_EVALUATOR_DB_PASSWORD"}


def test_public_dockerfile_does_not_compile_age_or_copy_credentials():
    text = (ROOT / "container/Dockerfile").read_text().lower()
    assert "make -c /tmp/age" not in text
    assert "git clone" not in text
    assert "auth.json" not in text
    assert "copy . " not in text


def test_seed_is_exactly_python_default_dimensions_and_zero_principles():
    tree = ast.parse((ROOT / "ralph_portable/frame_expansion.py").read_text())
    defaults = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "DEFAULT_DIMENSIONS"
            for target in node.targets
        ):
            defaults = ast.literal_eval(node.value)
            break
    assert defaults is not None and len(defaults) == 21
    seed = (ROOT / "schema/seed_structure.sql").read_text()
    assert seed.count("INSERT INTO ralph_frame_dimensions") == 21
    for dimension in defaults:
        assert json.dumps(dimension, separators=(",", ":")) in seed
    assert "INSERT INTO causal_principle" not in seed


def test_default_workflow_is_versioned_and_deterministic():
    workflow = yaml.safe_load((ROOT / "workflows/default/v1/workflow.yaml").read_text())
    assert workflow["name"] == "default"
    assert workflow["version"] == 1
    assert workflow["deterministic"] is True
    assert [stage["id"] for stage in workflow["stages"]] == [
        "observe", "decide", "act", "reflect", "learn"
    ]


def test_flagship_business_seed_is_structure_only_and_measure_is_runnable():
    seed = (ROOT / "schema/010_business_benchmark.sql").read_text().lower()
    assert "create table if not exists customers" in seed
    assert "create table if not exists subscriptions" in seed
    assert "insert into customers" not in seed
    assert "insert into subscriptions" not in seed

    flagship = yaml.safe_load(
        (ROOT / "templates/running-a-business/instance.yaml").read_text()
    )
    mission = flagship["mission"]
    assert mission["objective"] == "Get to 20 paying customers by the end of Q3"
    assert mission["measure"] == {
        "what": "count of active paying customers",
        "where": "select count(distinct customer_id) from subscriptions where status='active'",
        "target": 20,
        "goal": "reach_and_maintain",
    }
    assert mission["boundaries"]["must_ask_first"] == [
        "a plan whose expected expense is over $3"
    ]


def test_clean_compose_image_carries_the_flagship_instance():
    dockerfile = (ROOT / "container/Dockerfile").read_text()
    assert (
        "COPY templates/running-a-business/instance.yaml /app/instance.yaml"
        in dockerfile
    )
    healthcheck = " ".join(
        yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["postgres"]
        ["healthcheck"]["test"]
    )
    assert "plan_spend_reservation" in healthcheck
    assert "public.subscriptions" in healthcheck
    # Fresh-seed facts are one-shot publish checks, not recurring health invariants.
    assert "count(*) from causal_principle" not in healthcheck
    assert "count(*) from ralph_frame_dimensions" not in healthcheck
    assert "count(distinct customer_id)" not in healthcheck


def test_one_shot_owner_reapplies_schema_and_surfaces_outlive_loop():
    entrypoint = (ROOT / "container/app-entrypoint.sh").read_text()
    assert "apply_migrations.py" not in entrypoint
    assert 'wait -n "$STATUS_PID" "$MGMT_PID"' in entrypoint
    assert 'wait -n "$STATUS_PID" "$MGMT_PID" ${LOOP_PID' not in entrypoint
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    assert compose["services"]["migrate"]["command"] == ["python", "/app/scripts/apply_migrations.py"]
    assert compose["services"]["app"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert compose["services"]["app"]["environment"]["AQ_LOOP_AUTORESTART"] == "${AQ_LOOP_AUTORESTART:-1}"
    runtime = (ROOT / "schema/012_loop_runtime.sql").read_text().lower()
    assert "create table if not exists loop_runtime" in runtime


def test_app_container_is_nonroot_and_keeps_codex_tool_sandbox():
    dockerfile = (ROOT / "container/Dockerfile").read_text()
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    app = compose["services"]["app"]
    assert "USER aq" in dockerfile
    assert "chown -R root:root /app" in dockerfile
    assert "chmod -R a-w /app" in dockerfile
    assert app["environment"]["AQ_ACT_WORKSPACE"] == "/workspace"
    assert "aq-workspace:/workspace" in app["volumes"]
    assert app["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in app["security_opt"]
    assert "seccomp=unconfined" in app["security_opt"]
    assert "dangerously-bypass-approvals-and-sandbox" not in (ROOT / "runner/executor.py").read_text()


def test_acquisition_lifecycle_and_learning_provenance_are_db_enforced():
    lifecycle = (ROOT / "schema/015_acquisition_lifecycle.sql").read_text()
    evidence = (ROOT / "schema/016_learning_evidence_provenance.sql").read_text()
    assert "work_acquisition_state_guard" in lifecycle
    assert "acquisition_work_state_guard" in lifecycle
    assert "cannot be done while acquisition is open" in lifecycle
    assert "evidence_kind='verified_evidence' OR confidence < 1.0" in evidence
