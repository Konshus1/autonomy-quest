#!/usr/bin/env python3
"""Host-side replication broker (Task #4407).

Guest proposes JSON on stdin or --request-file.
Host decides approval (operator vs AQ_REPLICATION_AUTO_APPROVE OVERRIDE),
then records that the HOST executes (this process never grants guest docker.sock).

Two --execute paths:
- With --source-workspace and --dest-root: a REAL safe host-side copy that
  materializes a new instance dir (workspace copy + mission/modifications +
  provenance manifest). No docker, no docker.sock, no container spin-up.
- Without them: dry-run — advances state and plans the host actions only.

Container spin-up remains a later host-only step (BB #2144 / #3981).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root without install.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ralph_portable.host_replication_executor import (  # noqa: E402
    execute_replication_copy,
)
from ralph_portable.replication_request import (  # noqa: E402
    OVERRIDE_ENV,
    advance_host_execution,
    initial_status_for_host,
    override_enabled,
    validate_host_execution_record,
    validate_replication_request,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Host replication broker")
    parser.add_argument("--request-file", type=Path, help="JSON request path")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Advance to host_executing and emit planned host actions (still no docker.sock to guest)",
    )
    parser.add_argument(
        "--operator-approve",
        action="store_true",
        help="Operator explicitly approved (ignored if OVERRIDE already auto-approved)",
    )
    parser.add_argument(
        "--source-workspace",
        type=Path,
        help="Host directory to copy (the mission workspace). With --dest-root and "
        "--execute, performs a REAL safe host-side copy (no docker.sock, no container).",
    )
    parser.add_argument(
        "--dest-root",
        type=Path,
        help="Parent directory under which the new instance dir is created (real copy).",
    )
    parser.add_argument(
        "--allow-existing-dest",
        action="store_true",
        help="Allow overwriting an existing destination instance dir (default: refuse).",
    )
    args = parser.parse_args(argv)

    if args.request_file:
        packet = json.loads(args.request_file.read_text())
    else:
        packet = json.load(sys.stdin)

    errors = validate_replication_request(packet)
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, indent=2))
        return 2

    status = initial_status_for_host()
    if status == "awaiting_operator" and args.operator_approve:
        status = "approved"

    result: dict = {
        "ok": True,
        "override_env": OVERRIDE_ENV,
        "override_enabled": override_enabled(),
        "status": status,
        "executed_by": None,
        "host_actions_planned": [],
        "note": "Guest proposes only; host performs replication.",
    }

    if status == "awaiting_operator":
        result["ok"] = False
        result["errors"] = [
            "awaiting_operator: set operator approve or host OVERRIDE "
            f"{OVERRIDE_ENV}=1"
        ]
        print(json.dumps(result, indent=2))
        return 3

    if args.execute:
        # Real safe host-side copy when both a source workspace and a dest root are
        # given; otherwise stay in dry-run (plan the host actions, do not materialize).
        if args.source_workspace and args.dest_root:
            record = execute_replication_copy(
                packet,
                status=status,
                source_workspace=args.source_workspace,
                dest_root=args.dest_root,
                allow_existing_dest=args.allow_existing_dest,
            )
            result.update(record)
            result["mode_of_execution"] = "host_copy"
            print(json.dumps(result, indent=2))
            return 0 if record.get("ok") else 4

        # Dry-run: advance state and plan the host actions (container spin-up remains
        # a later, host-only step — not performed here and never via guest docker.sock).
        status = advance_host_execution(status)
        actions = [
            "allocate_instance_id",
            "provision_volume",
            f"clone_mode:{packet['mode']}",
            "apply_mission",
        ]
        if packet["mode"] == "copy_with_modifications":
            actions.append("apply_modifications")
        actions.append("start_container_or_native_instance")
        record = {
            "executed_by": "host",
            "status": status,
            "host_action": ",".join(actions),
        }
        rec_errors = validate_host_execution_record(record)
        if rec_errors:
            print(json.dumps({"ok": False, "errors": rec_errors}, indent=2))
            return 2
        result["status"] = status
        result["executed_by"] = "host"
        result["host_actions_planned"] = actions
        result["mode_of_execution"] = "dry_run_plan"

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
