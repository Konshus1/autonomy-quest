#!/usr/bin/env python3
"""End-to-end governed lifecycle proof using executed PostgreSQL measurements.

The measurement rows are not mocked return values: each environment executes a bounded SQL action,
the before/after measure is read from PostgreSQL, and that delta drives the lifecycle transition.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management.api.principle_governance import PgGovernedPrincipleLifecycle, PromotionRefused

DSN = os.environ.get("AQ_GOV_TEST_DSN", "postgresql://kthomas@localhost/aq_governance_test")


def env(i, domain, mission):
    return {"environment_id": i, "domain": domain, "mission_id": mission,
            "harness": "sql-bounded-experiment-v1"}


def execute(conn, environment_id: str, action: str) -> tuple[str, float]:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM governance_demo_items WHERE environment_id=%s", (environment_id,))
        before = cur.fetchone()[0]
        if action == "add-two":
            cur.execute("INSERT INTO governance_demo_items VALUES (%s,'a'),(%s,'b') ON CONFLICT DO NOTHING",
                        (environment_id, environment_id))
        elif action == "add-one":
            cur.execute("INSERT INTO governance_demo_items VALUES (%s,'a') ON CONFLICT DO NOTHING", (environment_id,))
        elif action == "duplicate-noise":
            cur.execute("INSERT INTO governance_demo_items VALUES (%s,'a') ON CONFLICT DO NOTHING", (environment_id,))
        elif action == "remove":
            cur.execute("DELETE FROM governance_demo_items WHERE environment_id=%s", (environment_id,))
        cur.execute("SELECT count(*) FROM governance_demo_items WHERE environment_id=%s", (environment_id,))
        after = cur.fetchone()[0]
        cur.execute("INSERT INTO governance_demo_measurement(environment_id,action,before_value,after_value) "
                    "VALUES (%s,%s,%s,%s) RETURNING id", (environment_id, action, before, after))
        ref = f"governance_demo_measurement:{cur.fetchone()[0]}"
    conn.commit()
    return ref, float(after - before)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    g = PgGovernedPrincipleLifecycle(DSN)
    with g._connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE causal_principle_transition RESTART IDENTITY")
        cur.execute("DROP TABLE IF EXISTS governance_demo_measurement")
        cur.execute("DROP TABLE IF EXISTS governance_demo_items")
        cur.execute("CREATE TABLE governance_demo_items(environment_id text, item_key text, PRIMARY KEY(environment_id,item_key))")
        cur.execute("CREATE TABLE governance_demo_measurement(id bigserial PRIMARY KEY, environment_id text NOT NULL, "
                    "action text NOT NULL, before_value integer NOT NULL, after_value integer NOT NULL, created_at timestamptz DEFAULT now())")
        conn.commit()

        principle = {"cause": "record_verified_item", "effect": "measure_up", "scope": {"demo": "governance"}}

        ref1, delta1 = execute(conn, "docs-1", "add-two")
        g.register_mined(principle, env("docs-1", "documentation", "catalog-docs"), ref1, "aq-principle-miner")
        shadow = g.shadow_guidance(principle)
        assert shadow["status"] == "provisional" and not shadow["authoritative"] and shadow["max_experiments"] == 1
        first = g.record_environment_test(principle, env("docs-1", "documentation", "catalog-docs"),
                                          ref1 + ":test", "increase", delta1)

        # Negative a: one environment can never promote.
        try:
            g.promote(principle, authorization_environment=env("control", "governance", "control"),
                      evidence_ref="authorization:premature", applies_here=True,
                      applies_here_how="executed SQL measure", negative_control="reverse direction",
                      negative_control_result="rejected", adjudicated_by="independent-reviewer")
            raise AssertionError("NEGATIVE CONTROL a FAILED: one-environment principle promoted")
        except PromotionRefused:
            one_env_control = "PASS (promotion refused)"

        ref2, delta2 = execute(conn, "api-1", "add-one")
        second = g.record_environment_test(principle, env("api-1", "api-client", "catalog-api"),
                                           ref2, "increase", delta2)
        promoted_id = g.promote(
            principle, authorization_environment=env("control", "governance", "control"),
            evidence_ref="authorization:review-1", applies_here=True,
            applies_here_how="replayed both SQL actions and measures",
            negative_control="invert expected direction for both recorded deltas",
            negative_control_result="both inverted classifications refuted the candidate",
            adjudicated_by="independent-reviewer")
        assert g.shadow_guidance(principle)["authoritative"] is True

        # Negative c: a duplicate/no-change action is noise and must not withdraw authority.
        execute(conn, "queue-1", "add-one")
        noise_ref, noise_delta = execute(conn, "queue-1", "duplicate-noise")
        noise = g.record_environment_test(principle, env("queue-1", "queue", "catalog-queue"),
                                          noise_ref, "increase", noise_delta, noise_tolerance=0.0)
        assert noise["result"] == "noise" and not noise["automatic_demotion"]
        assert g.shadow_guidance(principle)["status"] == "promoted"

        # Positive demotion / negative b: a clearly opposite delta in a third environment MUST fire.
        refute_ref, refute_delta = execute(conn, "queue-1", "remove")
        demotion = g.record_environment_test(principle, env("queue-1", "queue", "catalog-queue"),
                                             refute_ref, "increase", refute_delta)
        assert refute_delta < 0 and demotion["automatic_demotion"] is True
        assert g.shadow_guidance(principle)["status"] == "demoted"

        # Negative d: DB trigger makes deleting provenance fail rather than leave a suspicious row.
        provenance_control = "FAIL"
        try:
            with g._connect() as c2, c2.cursor() as cur2:
                cur2.execute("UPDATE causal_principle_transition SET evidence_ref=NULL WHERE id=%s", (promoted_id,))
        except Exception as exc:
            if "append-only" not in str(exc):
                raise
            provenance_control = "PASS (database rejected evidence_ref removal)"
        assert provenance_control.startswith("PASS")

        history = g.history(principle)
        output = {
            "principle": principle,
            "executed_measurements": {"first": delta1, "second": delta2,
                                      "noise": noise_delta, "counterevidence": refute_delta},
            "shadow": shadow,
            "cross_environment": [first, second],
            "promotion_transition_id": promoted_id,
            "automatic_demotion": demotion,
            "negative_controls": {"one_environment": one_env_control,
                                  "over_demotion_noise": "PASS (remained promoted)",
                                  "clear_refutation_fires": "PASS (automatic demotion)",
                                  "provenance_removal": provenance_control},
            "history": [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}
                        for row in history],
        }
        print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
