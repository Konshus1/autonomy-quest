# Interview 11 — Validation ladder (Ralph-control pack)

*Only if `ralph_control.enabled`.*

## Doctrine to explain

Workers must propose the **highest feasible** validation level:

1. **UI test** (web or mobile) if the change is UI-testable
2. else **API test** if an API surface can prove intent
3. else **unit tests** must exist

Evaluator checks:

- tests cover the **business intent / context** of the work
- tests **actually pass** on the worker's worktree

Pairs that clear that bar may join an **integration cohort** on a shared integration branch with a live FE/BE/DB environment. Failures loop rework→evaluator until max attempts, then the worker's **manager** decides continue vs escalate to human review.

### Cohort → main merge gate

After the cohort's integration tests pass, **managers** do a cursory review. Merge to main is
**manager-gated** — not operator-gated by default.

The manager is a coding agent. On clear pass it approves the merge. On close calls or uncertainty
it may escalate to a human instead of approving. After approve: merge integration→main, close
worktrees, retire worker/evaluator sessions.

## Record

```yaml
ralph_control:
  validation:
    ladder: [ui, api, unit]      # preference order; do not invert
    require_intent_adequacy: true
    integration_cohort: true
    max_rework_attempts: 3
    merge_to_main: manager_gated  # manager coding-agent approves or escalates to human
```

## Honesty note (implementation gap vs intent)

TalkingBack Ralph today is still largely human/propose-only for merge-to-main (BB #1268). The
**intended** Autonomy Quest / Ralph-control doctrine is manager-gated as above. Do not claim the
TB production path already implements manager-gated auto-merge until that code exists.
