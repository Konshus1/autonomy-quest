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

Pairs that clear that bar may join an **integration cohort** on a shared integration branch with a live FE/BE/DB environment. Failures loop rework→evaluator until max attempts, then the worker's manager decides continue vs operator review. Cohort pass → managers cursory review → merge policy (default: human/operator gated unless host enables a stronger gate).

## Record

```yaml
ralph_control:
  validation:
    ladder: [ui, api, unit]      # preference order; do not invert
    require_intent_adequacy: true
    integration_cohort: true
    max_rework_attempts: 3
    merge_to_main: operator_gated  # operator_gated | propose_only | (future) auto_if_configured
```

## Honesty note

Do not promise automatic merge-to-main unless the host/instance explicitly configures and implements it. TalkingBack Ralph today is still human-gated for that step.
