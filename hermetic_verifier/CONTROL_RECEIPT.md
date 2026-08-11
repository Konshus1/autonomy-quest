# Hermetic verifier acceptance receipt

Run at `2026-08-11T07:57:03Z` on Docker Engine 28.3.2 from branch
`task/4834-hermetic-verifier`.

Exact reproduction command from repository root:

```sh
python hermetic_verifier/prove_controls.py
```

Observed receipt:

```text
[1/3] building digest-pinned verifier image
[2/3] hermetic negative + positive control
[3/3] naive credentialed runner contrast
HERMETIC_CONTROL_PASS candidate=9eca74a811ed0984130cf86f0241f66256b9b8e2
ATTACK_RESULTS={"actuator_socket_credential": "BLOCKED", "control_plane_write": "BLOCKED", "git_push": "BLOCKED", "network_egress": "BLOCKED", "outside_write": "BLOCKED"}
HONEST_CONTROL=2 passed, 0 skipped
SIGNED_VERDICT=verified exact repository+SHA
ATTACK_RESULTS={"actuator_socket_credential": "ALLOWED", "control_plane_write": "ALLOWED", "git_push": "ALLOWED", "network_egress": "ALLOWED", "outside_write": "ALLOWED"}
NAIVE_SIDE_EFFECTS=git,control-plane,actuator,outside-write,network exact-content verified
```

The candidate SHA above is the proof script's disposable fixture commit and changes between runs
because Git records a new commit timestamp. The script rejects any missing/wrong line and separately
inspects exact Git, table-row, socket receipt, source/outside file, and HTTP receipt content.

Repository regression receipt from the same worktree:

```text
python -m pytest -q
464 passed, 49 skipped, 4 warnings in 4.71s
```

Those 49 integration skips pre-exist in the general repository suite. The security acceptance command
above has its own exact `2 passed, 0 skipped` assertion; a skip there is failure, never pass.
