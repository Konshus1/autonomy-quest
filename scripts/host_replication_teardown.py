#!/usr/bin/env python3
"""Host-side replica teardown (#4834 replication Step 1) — the REVERSIBLE safety rail.

Reaps a replica stack stood up by the host broker: removes its containers, its
project-namespaced volumes and networks (the ``docker compose down -v`` effect), and the
generated secret/ports/manifest files. Enumeration + teardown are by compose-project label
(``aq-replica-*``), so nothing is orphaned even if the generated files were already deleted.

HOST-ONLY. Like the stand-up, this touches the docker daemon and must never run inside a
guest container.

Usage:
    scripts/host_replication_teardown.py --list
    scripts/host_replication_teardown.py --project aq-replica-<id>
    scripts/host_replication_teardown.py --all      # every aq-replica-* stack
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ralph_portable.host_replica_stack import (  # noqa: E402  (HOST-ONLY docker step)
    list_replica_projects,
    teardown_all_replicas,
    teardown_replica,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Host replica teardown (reversible rail)")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--project", help="Tear down one replica project (aq-replica-<id>).")
    g.add_argument("--all", action="store_true", help="Tear down ALL aq-replica-* stacks.")
    g.add_argument("--list", action="store_true", help="List live replica projects and exit.")
    parser.add_argument("--repo-root", type=Path, default=ROOT,
                        help="Repo root for the optional `compose down -v` pass.")
    args = parser.parse_args(argv)

    if args.list:
        print(json.dumps({"replicas": list_replica_projects()}, indent=2))
        return 0

    if args.all:
        results = teardown_all_replicas(repo_root=args.repo_root)
        print(json.dumps({"ok": all(r.get("ok") for r in results), "results": results}, indent=2))
        return 0 if all(r.get("ok") for r in results) else 1

    result = teardown_replica(args.project, repo_root=args.repo_root)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
