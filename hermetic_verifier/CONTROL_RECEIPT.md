# Hermetic verifier acceptance receipt

Run at `2026-08-11T15:02:25Z` on Docker Engine 28.3.2 from branch
`task/close-loop-enforced` at `ccc102007b0d749dc7b84f1e41f743857b64f758`.

Exact reproduction command from repository root:

```sh
python hermetic_verifier/prove_controls.py
```

Observed receipt:

```text
[1/3] building digest-pinned verifier image
[2/3] hermetic negative + positive control
[3/3] naive credentialed runner contrast
HERMETIC_CONTROL_PASS candidate=3748ea6c97878f9e869642f983509ed742389885
ATTACK_RESULTS={"actuator_socket_credential": "BLOCKED", "control_plane_write": "BLOCKED", "git_push": "BLOCKED", "network_egress": "BLOCKED", "outside_write": "BLOCKED", "proc1_environment_secret": "BLOCKED"}
HONEST_CONTROL=2 passed, 0 skipped
SIGNED_VERDICT=verified exact repository+SHA
FORGED_CENSUS=FAIL actuator=REJECTED collected=0 passed=0
PRECREATED_CENSUS=IGNORED verdict=FAIL
NO_TESTS=FAIL collected=0 passed=0
ATTACK_RESULTS={"actuator_socket_credential": "ALLOWED", "control_plane_write": "ALLOWED", "git_push": "ALLOWED", "network_egress": "ALLOWED", "outside_write": "ALLOWED", "proc1_environment_secret": "ALLOWED"}
NAIVE_SIDE_EFFECTS=git,control-plane,actuator,outside-write,network exact-content verified
```

Red-first exploit evidence from regression commit `8400e97` against the unchanged vulnerable
verifier at `d1e8657`:

```text
FORGED_CENSUS_RED: zero-test conftest earned authorization: verifier_rc=0 verdict=PASS passed=1 actuator_rc=0
1 passed in 0.01s
no tests ran in 0.00s
AQ_HERMETIC_RESULT:{"passed_count": 1, "skipped_count": 0}
ACCEPT: signed PASS matches every approved authorization binding
```

The candidate SHA above is the proof script's disposable fixture commit and changes between runs
because Git records a new commit timestamp. The script rejects any missing/wrong line and separately
inspects exact Git, table-row, socket receipt, source/outside file, and HTTP receipt content.

Repository regression receipt from the same worktree:

```text
python -m pytest -q
527 passed, 49 skipped, 4 warnings in 14.09s
```

Those 49 integration skips pre-exist in the general repository suite. The security acceptance command
above has its own exact `2 passed, 0 skipped` assertion; a skip there is failure, never pass.

Close-loop shadow receipt from `scripts/run_close_loop_shadow.sh`:

```text
gate={"approved": true, "reason": "approved"}
hermetic_authorization={"accepted": true, "reason": "accepted"}
public_actuator={"reason": "public_interlock_pending", "status": "refused"}
shadow_actuator={"mode": "shadow", "status": "would_approve"}
receipt_hash=94cc36a0ced4bf1f04fa226c256bcce3573463190e3f1e9117282e88349b23d1
```
