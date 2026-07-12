#!/usr/bin/env python3
"""aq — the CLI.

    ./aq.py once      turn the loop exactly one time
    ./aq.py forever   turn it forever (this is the system's actual life)
"""
import logging, os, sys

from runner.config import Instance
from runner.db import Db
from runner.executor import build as build_executor
from runner.loop import Loop

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "once"

    inst = Instance.load("instance.yaml")          # raises Unaimed if there is no mission
    db = Db(os.environ["AQ_DB_URL"], graph=inst.datastore.get("graph", "age"))
    loop = Loop(inst, db, build_executor(inst))    # subscription or api — the interview decided

    if cmd == "forever":
        loop.forever()
        return 0

    c = loop.cycle()
    if c is None:
        print("\n-> no work this cycle (nothing worth doing, or parked for a human)")
        return 0
    print(f"\n-> run #{c.run_id} complete. LEARNED: {c.learned}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
