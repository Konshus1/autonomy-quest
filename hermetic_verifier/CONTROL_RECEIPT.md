# Hermetic verifier acceptance receipt

Run on Docker Engine 28.3.2 from branch `task/close-loop-enforced` at commit `c42eff3`
(pipe-delivered census + published-exploit regressions).

Exact reproduction command from repository root:

```sh
python hermetic_verifier/prove_controls.py
```

Observed receipt:

```text
[1/3] building digest-pinned verifier image
[2/3] hermetic negative + positive control
[3/3] naive credentialed runner contrast
HERMETIC_CONTROL_PASS candidate=334bac387f1a840497aa9818146a6a24664ab67c
ATTACK_RESULTS={"actuator_socket_credential": "BLOCKED", "control_plane_write": "BLOCKED", "git_push": "BLOCKED", "network_egress": "BLOCKED", "outside_write": "BLOCKED", "proc1_environment_secret": "BLOCKED"}
HONEST_CONTROL=2 passed, 0 skipped
SIGNED_VERDICT=verified exact repository+SHA
FORGED_CENSUS=FAIL actuator=REJECTED collected=0 passed=0
PRECREATED_CENSUS=IGNORED verdict=FAIL
NO_TESTS=FAIL collected=0 passed=0
PUBLISHED_EXPLOITS_FAIL=atexit-fd-write,recorder-mutation,plugin-unregister -> FAIL actuator=REJECTED
KNOWN_LIMITATION=determined sessionstart-unregister + single-record forge -> PASS actuator=ACCEPTED (census STILL FORGEABLE) ; see HERMETIC_CENSUS_LIMITATION.md
ATTACK_RESULTS={"actuator_socket_credential": "ALLOWED", "control_plane_write": "ALLOWED", "git_push": "ALLOWED", "network_egress": "ALLOWED", "outside_write": "ALLOWED", "proc1_environment_secret": "ALLOWED"}
NAIVE_SIDE_EFFECTS=git,control-plane,actuator,outside-write,network exact-content verified
```

## Red-first evidence: published exploits go PASS-accepted -> FAIL-rejected

Against the unchanged vulnerable verifier at `eb16a1d`, the published `atexit` fd-rewrite forged a
signed, actuator-accepted `PASS` with zero real tests:

```text
VERDICT=PASS  RESULT={"census_valid": true, "clean": true, "collected_count": 1, "passed_count": 1, ...}
```

After the pipe-delivered / single-record census (`c42eff3`), all three published exploits fail closed
and the actuator rejects them (`PUBLISHED_EXPLOITS_FAIL` line above): the `atexit` write can only
append to an unseekable pipe, the recorder module global is gone, and a configure-time unregister is a
no-op against a not-yet-registered plugin.

## KNOWN LIMITATION: the census is still forgeable

The census is NOT unforgeable. The `KNOWN_LIMITATION` line above is live evidence: a determined
candidate that unregisters the trusted plugin at `pytest_sessionstart` and writes a single forged
record to the inherited census pipe still earns `verdict=PASS` with `ACCEPT: signed PASS matches every
approved authorization binding`. The plugin runs in the candidate's own pytest process at the candidate
uid, and this non-root, cap-dropped, no-new-privileges sandbox cannot drop the candidate to a separate
uid (`setuid` returns `EPERM`, verified live). See `HERMETIC_CENSUS_LIMITATION.md` for the exact attack
and what a sound design requires. The powerlessness guarantee (the six-capability contrast) is sound;
test authenticity against an adversarial candidate is not.

Repository regression receipt from the same worktree:

```text
python -m pytest -q
532 passed, 49 skipped, 4 warnings
```

The 49 integration skips pre-exist in the general repository suite. The security acceptance command
above has its own exact `2 passed, 0 skipped` assertion; a skip there is failure, never pass.

Close-loop shadow receipt from `scripts/run_close_loop_shadow.sh` (public actuator stays inert):

```text
gate={"approved": true, "reason": "approved"}
hermetic_authorization={"accepted": true, "reason": "accepted"}
public_actuator={"reason": "public_interlock_pending", "status": "refused"}
shadow_actuator={"mode": "shadow", "status": "would_approve"}
```
