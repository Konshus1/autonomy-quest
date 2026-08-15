#!/usr/bin/env python3
"""aq — the CLI.

    ./aq.py once      turn the loop exactly one time
    ./aq.py forever   turn it forever (this is the system's actual life)
    ./aq.py ui        serve the window into it (http://localhost:8080)

    ./aq.py share export <file>       what we learned that might hold for a DIFFERENT mission
    ./aq.py share import <file>       receive another instance's learnings — STAGED, inert
    ./aq.py share review              adjudicate what arrived (nothing is adopted until you do)
    ./aq.py share promote <id> "why"  adopt it here — with a reason, and reversibly
    ./aq.py share reject  <id> "why"

    ./aq.py authority list             pending authority-change REQUESTS the actor emitted (Slice 3)
    ./aq.py authority approve <id> <who> ["note"]   record approval (a REVIEW record; see below)
    ./aq.py authority deny    <id> <who> ["note"]   record denial

    NOTE: an authority-change request is a PROPOSAL with zero authority. Recording 'approve' here does
    NOT open the gate — OPENING A GATE is editing instance.yaml's `authority_envelope:` to mark the
    class `open` (Slice 2, the sole open mechanism). This disposition is the human's review record
    around that decision. The actor's own process (aq_loop) cannot run these writes — the disposition
    table is grant-denied to it (schema/999), so it can never approve its own request.
"""
import logging, os, pathlib, sys

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

HERE = pathlib.Path(__file__).resolve().parent


def load_env() -> None:
    """Read .env ourselves.

    aq.py used to require the human to remember `set -a; . ./.env; set +a` — and if they didn't,
    even `./aq.py --help` died with a raw `KeyError: 'AQ_DB_URL'`. A traceback on --help is the
    worst possible first impression, and "you must source a file first" is not a design, it is a
    thing you forgot to build. The systemd unit had EnvironmentFile all along, so this only ever
    bit humans typing commands — which is exactly who you least want to punish.

    Existing environment variables WIN: an explicitly-exported value must never be silently
    overridden by a file.
    """
    f = HERE / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"

    # --help must NEVER touch the database. Answer it before anything can fail.
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    load_env()

    if "AQ_DB_URL" not in os.environ:
        print("AQ_DB_URL is not set, and there is no .env here.\n"
              "  Run ./install.sh first — it creates .env with the database URL.\n"
              f"  Looked in: {HERE / '.env'}", file=sys.stderr)
        return 78          # EX_CONFIG

    from runner.config import Instance
    from runner.db import Db
    from runner.executor import build as build_executor
    from runner.loop import Loop

    if cmd == "share":
        from runner import sharing
        sub = sys.argv[2] if len(sys.argv) > 2 else "review"
        inst = Instance.load("instance.yaml")
        db = Db(os.environ["AQ_DB_URL"], graph=inst.datastore.get("graph", "age"))

        if sub == "export":
            b = sharing.export(db, inst, sys.argv[3])
            print(f"exported {len(b['learnings'])} generalisable learning(s) -> {sys.argv[3]}")
            print("These are PROPOSALS. The receiving instance must judge them against its own mission.")
        elif sub == "import":
            r = sharing.import_bundle(db, sys.argv[3])
            print(f"from {r['from']} (mission: {r['their_mission']})")
            print(f"  staged: {r['staged']}   already had: {r['already_had']}")
            print(f"  {r['status']}")
        elif sub == "review":
            rows = db.staged_imports()
            if not rows:
                print("nothing staged.")
            for r in rows:
                print(f"\n#{r['id']}  from {r['origin_instance']}  (confidence {r['confidence']})")
                print(f"  THEIR MISSION: {r['origin_mission']}")
                print(f"  INSIGHT:       {r['insight']}")
                print(f"  THEIR EVIDENCE:{r['origin_evidence'][:160]}")
                print(f"  APPLIES WHEN:  {r['applies_when']}")
                print(f"  FALSIFIED BY:  {r['falsified_by']}")
                print(f"  -> ./aq.py share promote {r['id']} \"why this holds HERE\"  |  reject {r['id']} \"why not\"")
            print("\nStaged learnings are INERT. They influence nothing until promoted.")
        elif sub == "promote":
            lid = db.promote_import(int(sys.argv[3]), by="human", why=sys.argv[4])
            print(f"promoted -> local learning #{lid}. It now informs the loop, and can be rolled back.")
        elif sub == "reject":
            db.reject_import(int(sys.argv[3]), by="human", why=sys.argv[4])
            print("rejected. It stays on record with the reason — a rejection is a learning too.")
        else:
            print(f"unknown: share {sub}")
        return 0

    if cmd == "authority":
        # OPERATOR disposition surface for Slice-3 authority-change requests. Out-of-band and
        # human-invoked; it never opens a gate (that is the envelope-config edit) and is not reachable
        # from the actor's runtime (the disposition table is grant-denied to aq_loop, schema/999).
        sub = sys.argv[2] if len(sys.argv) > 2 else "list"
        inst = Instance.load("instance.yaml")
        db = Db(os.environ["AQ_DB_URL"], graph=inst.datastore.get("graph", "age"))
        if sub == "list":
            rows = db.authority_change_requests(status="pending")
            if not rows:
                print("no pending authority-change requests.")
            for r in rows:
                print(f"\n#{r['id']}  class={r['capability_class']}  reason={r['reason_kind']}  "
                      f"reversibility={r['reversibility']}  status={r['status']}")
                print(f"  WHY:    {r['rationale']}")
                print(f"  SCOPE:  {r['requested_scope']}")
                print(f"  BOUND:  {r['requested_bound']}")
                print(f"  DIGEST: {r['proposed_action_digest'][:16]}...  work #{r['work_id']}")
                print(f"  -> ./aq.py authority approve {r['id']} <you> \"why\"  |  deny {r['id']} <you> \"why\"")
            print("\nApproving RECORDS a decision; it does NOT open the gate. To open a gate, edit "
                  "instance.yaml `authority_envelope:` to mark the class `open`.")
        elif sub in ("approve", "deny"):
            rid = int(sys.argv[3])
            who = sys.argv[4]
            note = sys.argv[5] if len(sys.argv) > 5 else None
            disp = "approved" if sub == "approve" else "denied"
            ok = db.dispose_authority_change_request(rid, disposition=disp, disposed_by=who, note=note)
            if ok:
                print(f"recorded {disp} on request #{rid} by {who}.")
                if disp == "approved":
                    print("REMINDER: this did NOT open the gate. Open it by editing instance.yaml "
                          "`authority_envelope:` (set the class to `open`), then approve the parked work in the UI.")
            else:
                print(f"request #{rid} was already dispositioned (append-only; no change).")
        else:
            print(f"unknown: authority {sub}")
        return 0

    if cmd == "ui":
        from ui.server import serve
        serve(int(os.environ.get("AQ_UI_PORT", "8080")))
        return 0

    inst = Instance.load("instance.yaml")          # raises Unaimed if there is no mission
    db = Db(os.environ["AQ_DB_URL"], graph=inst.datastore.get("graph", "age"))
    loop = Loop(inst, db, build_executor(inst))    # subscription or api — the interview decided

    if cmd == "forever":
        loop.forever()
        return 0

    # A cycle that did NOT complete must NOT exit 0.
    #
    # `aq.py once` used to let RateLimited escape as a raw traceback — and the shell wrapper
    # around it reported EXIT: 0. So a cycle that never ran looked, to anything watching, exactly
    # like a cycle that succeeded. That is the precise lie this system exists to prevent, sitting
    # in its own CLI. Exit codes are an interface; a scheduler reads them and believes them.
    from runner.executor import AgentFailed, RateLimited
    from runner.budget import BudgetExceeded
    from runner.escalation import Hibernate
    try:
        c = loop.cycle()
    except RateLimited as e:
        print(f"\n-> RATE LIMITED. Not an error — the plan is used up for now.\n   {e}")
        print("   Nothing was lost. `./aq.py forever` waits this out and resumes automatically.")
        return 75          # EX_TEMPFAIL: try again later
    except Hibernate as e:
        print(f"\n-> HIBERNATING. The loop is stuck and has STOPPED SPENDING.\n   {e}")
        return 76
    except BudgetExceeded as e:
        print(f"\n-> BUDGET CAP REACHED. The loop has stopped.\n   {e}")
        return 77
    except AgentFailed as e:
        print(f"\n-> THE AGENT FAILED this cycle (recorded, not swallowed).\n   {e}")
        return 1

    if c is None:
        print("\n-> no work this cycle (nothing worth doing, or parked for a human)")
        return 0
    print(f"\n-> run #{c.run_id} complete. LEARNED: {c.learned}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
