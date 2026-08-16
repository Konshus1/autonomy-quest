"""Enablement demonstration — the authority layer OPERATING in the live demo container.
Runs the REAL deployed code (image 647bf9b) with the five flags ON, against the real container env.
Prints concrete before/after evidence for the three behaviors Kevin asked to see."""
import os, json
from runner.authority_envelope import assess_action_envelope, from_config, DEFAULT_ENVELOPE

def line(): print("-" * 78)

print("FLAGS LIVE:", {k: os.environ.get(k) for k in
      ("AQ_AUTHORITY_ENVELOPE","AQ_GROUND_TRUTH_BLAST","AQ_SUBSTRATE_BINDING",
       "AQ_AUTHORITY_REQUESTS","AQ_REPLICATION_AUTHORITY_ENVELOPE")})
line()

# ---- 1. GATED BY DEFAULT: a spend-money action at model-blast-0 gates CATEGORICALLY -------------
print("[1] GATED BY DEFAULT (no envelope config = safe defaults)")
v_default = assess_action_envelope("record the customer and charge their card",
                                   DEFAULT_ENVELOPE, spends_money=True)
print("    action='...charge their card', spends_money=True, model numeric blast=0")
print("    -> GATED:", v_default.gated, "| classes:", list(v_default.gated_classes))
print("    verdict(JSON):", json.dumps(v_default.to_authority_policy()))
assert v_default.gated and "spend_money" in v_default.gated_classes, "expected categorical gate"
print("    ==> escalates to a human REGARDLESS of the model's numeric label. PASS")
line()

# ---- 2. OPERATOR OPENS ONE GATE (one config line) ----------------------------------------------
print("[2] OPERATOR OPENS THE spend_money GATE (authority_envelope: spend_money: open)")
opened = from_config({"spend_money": "open"})
v_opened = assess_action_envelope("record the customer and charge their card",
                                  opened, spends_money=True)
print("    same action, envelope opens spend_money -> GATED:", v_opened.gated,
      "| classes:", list(v_opened.gated_classes))
assert not v_opened.gated, "opened class should fall through to the numeric gate"
# ...but a DIFFERENT still-closed class (contact_human) on the SAME opened envelope still gates:
v_other = assess_action_envelope("email the customer directly", opened, touches_human=True)
print("    a still-closed class on the same envelope (touches_human) -> GATED:", v_other.gated,
      "| classes:", list(v_other.gated_classes))
assert v_other.gated and "contact_human" in v_other.gated_classes
print("    ==> operator opened ONE gate; the rest stay gated; actor never self-opens. PASS")
line()

# ---- 3. REPLICA INHERITS A NARROWED ENVELOPE (child <= authenticated parent) --------------------
print("[3] REPLICA INHERITS A NARROWED ENVELOPE (compounding-autonomy safeguard)")
from ralph_portable.replication_authority import resolve_child_envelope
# Parent was NARROWED: spend_money gated. Child REQUESTS to re-open it, with NO host widen token.
parent = from_config({"spend_money": "gated"})
req_no_token = {"dispositions": {"spend_money": "open"}}          # guest asks to widen, no auth
res = resolve_child_envelope(parent, req_no_token, host_env={})   # no host secret in env
child_disp = res.effective.disposition("spend_money")
print("    parent: spend_money=gated ; child requests spend_money=open, NO host token")
print("    -> child effective spend_money:", child_disp, "| stripped widen:", list(res.stripped_widen))
assert child_disp == "gated", "unauthorized widen must be stripped -> child stays gated"
assert "spend_money" in res.stripped_widen, "the unauthorized widen must be recorded as stripped"
# With a valid host widen token, the SAME request IS honored:
res2 = resolve_child_envelope(parent, {"dispositions": {"spend_money": "open"},
                                       "widen_authorization": "HOST-DEMO-TOKEN"},
                              host_env={"AQ_REPLICATION_WIDEN_TOKEN": "HOST-DEMO-TOKEN"})
print("    same request WITH a valid host token -> child spend_money:",
      res2.effective.disposition("spend_money"))
assert res2.effective.disposition("spend_money") == "open"
print("    ==> a child can NEVER silently be wider than its parent; only a HOST token widens. PASS")
line()
print("ALL THREE BEHAVIORS DEMONSTRATED LIVE ON IMAGE", open("/app/GIT_SHA").read().strip(),
      "WITH FLAGS ON.")
