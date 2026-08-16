#!/usr/bin/env python3
"""Host-side gated-deploy broker — W1a gated deploy pathway (task #6087).

Guest PROPOSES a deploy request JSON (stdin or --request-file). This HOST process is the only thing
that executes it: it runs the flag gate, the authority-envelope ``deploy`` gate (escalates to a human
unless the operator opened it), the exact-SHA check (request vs the SHA actually built), then deploys
via the configured adapter and prints a receipt. It NEVER hands a guest the deploy credential — ``az``
authenticates from the host's own login; nothing credential-shaped crosses this boundary.

  # gated deploy (escalates unless the operator opened the `deploy` gate in instance.yaml)
  AQ_DEPLOY_BRIDGE=1 python scripts/host_deploy_broker.py \
      --request-file req.json --built-sha <the SHA you actually built> \
      --adapter-app <aca-app> --adapter-resource-group <rg>

  # roll back the previous revision recorded in a receipt
  AQ_DEPLOY_BRIDGE=1 python scripts/host_deploy_broker.py --rollback-receipt receipt.json

This script imports the HOST-ONLY deploy executor + adapters (``ralph_portable.host_deploy_stack``),
so — like ``host_replication_broker`` — it is the positive control that proves those modules live on
the host side of the import firewall, never in the guest closure.
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

from ralph_portable.host_deploy_stack import (  # noqa: E402  (HOST-ONLY deploy step)
    RecordedImageResolver,
    execute_deploy,
    rollback_deploy,
)


def _load_envelope(path: Path | None):
    """Resolve the operator authority envelope from a YAML/JSON file's ``authority_envelope:`` map, or
    None (=> fully-gated DEFAULT => escalate). The operator OPENS the deploy gate by setting
    ``deploy: open`` there."""
    if path is None:
        return None
    import yaml  # local import: the broker is host-only tooling

    from runner.authority_envelope import from_config

    raw = yaml.safe_load(path.read_text()) or {}
    section = raw.get("authority_envelope") if isinstance(raw, dict) else None
    return from_config(section)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Host gated-deploy broker (W1a, task #6087)")
    parser.add_argument("--request-file", type=Path, help="deploy request JSON (else stdin)")
    parser.add_argument("--built-sha", help="GROUND-TRUTH SHA actually built/stamped (exact-SHA check)")
    parser.add_argument("--envelope-file", type=Path,
                        help="instance.yaml (or any YAML) whose authority_envelope: opens/gates 'deploy'")
    parser.add_argument("--adapter-app", help="ACA app name (for the aca adapter)")
    parser.add_argument("--adapter-resource-group", help="ACA resource group (for the aca adapter)")
    parser.add_argument("--image-records", type=Path,
                        help="HOST build-receipt JSON {git_sha: {image: <registry/repo@sha256:...>, "
                        "digest: <64hex>}} — the host's OWN canonical image per SHA (the guest never "
                        "names the image). Required for an image adapter (aca).")
    parser.add_argument("--rollback-receipt", type=Path,
                        help="a deploy receipt JSON to ROLL BACK to its previous revision")
    args = parser.parse_args(argv)

    adapter_config: dict[str, str] = {}
    if args.adapter_app:
        adapter_config["app"] = args.adapter_app
    if args.adapter_resource_group:
        adapter_config["resource_group"] = args.adapter_resource_group

    if args.rollback_receipt:
        receipt = json.loads(args.rollback_receipt.read_text())
        # A rollback is a deploy-class live change -> envelope-gated (escalates unless 'deploy' opened).
        result = rollback_deploy(receipt, envelope=_load_envelope(args.envelope_file),
                                 adapter_config=adapter_config)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 4

    packet = json.loads(args.request_file.read_text()) if args.request_file else json.load(sys.stdin)
    if not args.built_sha:
        print(json.dumps({"ok": False, "status": "invalid",
                          "reason": "--built-sha is required (the SHA you actually built)"}, indent=2))
        return 2
    # HOST-canonical image resolver: the host's own recorded image per git SHA (never the guest's).
    resolver = None
    if args.image_records:
        resolver = RecordedImageResolver(json.loads(args.image_records.read_text()))
    result = execute_deploy(
        packet, built_sha=args.built_sha, envelope=_load_envelope(args.envelope_file),
        adapter_config=adapter_config, image_resolver=resolver)
    print(json.dumps(result, indent=2, sort_keys=True))
    # exit 0 only on a real deploy; escalation / mismatch / disabled are non-zero (host-visible).
    return 0 if result.get("deployed") else 5


if __name__ == "__main__":
    raise SystemExit(main())
