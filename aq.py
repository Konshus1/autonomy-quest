#!/usr/bin/env python3
"""aq — the CLI. `./aq.py loop --once` turns the loop exactly one time."""
import logging, os, sys
from runner.config import Instance
from runner.db import Db
from runner.gateway import Gateway
from runner.loop import Loop

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

def main() -> int:
    inst = Instance.load("instance.yaml")            # raises Unaimed if no mission
    db = Db(os.environ["AQ_DB_URL"], graph=inst.datastore.get("graph", "age"))
    gw = Gateway(inst.models)
    loop = Loop(inst, db, gw)
    c = loop.cycle()
    if c is None:
        print("\n-> cycle produced no work (nothing worth doing, or parked for a human)")
        return 0
    print(f"\n-> run #{c.run_id} complete. LEARNED: {c.learned}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
