# Minimal “Try” build — safety checklist (6087 WS4)

Draft for review. Not a ship ticket. The public/minimal edition is safe **because capability is absent**, not because a flag is off.

Use this before any public “try” cut. Every box is fail-closed: unchecked = do not ship.

## Structural absences (not flag-gated)

- [ ] Cannot spend money (no Stripe/live payment keys, no spend action in the try image).
- [ ] Cannot contact real people (no outbound email/SMS/social send path).
- [ ] Cannot publish externally (no social publisher, no public git push from the guest).
- [ ] Cannot deploy to a customer environment.
- [ ] Cannot write outside its sandbox volume/DB.
- [ ] Cannot stand up a real replica (`straight_copy` / `copy_with_modifications` host broker not in the try image; no docker on the guest).
- [ ] Authority / envelope / execute-to-learn / comms-import flags are **not present** in the public tree (not merely default-off).

## Honesty of the page and README

- [ ] Says it is a limited evaluation build.
- [ ] Names what Full / Stay-current add (enforced envelope, real-world gates, bounded replication).
- [ ] Does not imply the free build is production-safe for real autonomous action.
- [ ] Does not say “nothing is charged” on a page that also sells a paid SKU. Free is free; paid buttons name the price they charge.
- [ ] No private module path or enablement flag documented as “just turn this on.”

## Receipts before any outward “try” post

- [ ] A clean clone of the public tree, no private remotes required, reaches a sandbox loop.
- [ ] A try-mission measure is a real DB read, and fabricating a row is refused or structurally impossible.
- [ ] 2632 + Kevin have signed the explainer copy (nothing outward without that gate).

## Explicitly not this checklist

Live-loop enablement on `aq-demo-parent` / `aq-seed-c2f4834`, child stand-up, and public merge of private authority commits. Those stay Kevin decisions (#31).
