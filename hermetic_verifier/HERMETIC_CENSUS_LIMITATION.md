# Hermetic census: what is still forgeable, and what a sound design requires

**Status: the trusted test census is NOT unforgeable. A determined zero-test candidate can still
obtain a signed, actuator-ACCEPTED `PASS`.** This document states precisely what remains forgeable,
why it cannot be closed inside the current sandbox model, and what a sound design would require. It
exists because the honest obligation is to document a known-forgeable census rather than ship a
fourth silently-forgeable one.

## The three published exploits are closed (regression-guarded)

The census is now delivered over an **anonymous pipe** instead of a candidate-writable seekable
file, the recorder is no longer a mutable module global, and PID 1 requires **exactly one**
well-formed record (missing / malformed / more-than-one all fail closed). This structurally closes:

1. **`atexit` fd-rewrite** — a pipe cannot be `lseek`/`ftruncate`'d (ESPIPE), so a late candidate
   write can only *append* a second record; the genuine record can never be rewound and overwritten.
   A one-record rule turns the append into a `>1 record` FAIL.
2. **Recorder-singleton mutation** (`verifier_census.recorder.passed = 1`) — the module global is
   gone; the attribute does not exist, so the import raises and the session fails closed.
3. **Naive plugin-unregister at `pytest_configure`** — hook ordering means the recorder is not yet
   registered when a candidate `pytest_configure` runs, so the unregister is a no-op and the genuine
   `collected=0` record is still emitted → FAIL.

`prove_controls.py` asserts all three as FAIL/REJECTED.

## What is still forgeable (verified live)

The trusted census plugin runs **inside the candidate's own pytest process, at the candidate's
uid**. Any file descriptor, module, or plugin object the plugin can reach, candidate code in the
same process can also reach. A determined candidate therefore:

1. waits until the recorder is definitely registered (`pytest_sessionstart`),
2. unregisters it through the plugin manager so the genuine record is never written, and
3. writes exactly one forged record to the inherited census pipe descriptor.

```python
# candidate conftest.py, zero real tests -> signed PASS, actuator ACCEPT
import os, json
def pytest_sessionstart(session):
    pm = session.config.pluginmanager
    for p in list(pm.get_plugins()):
        if type(p).__name__ == "CensusRecorder":
            pm.unregister(p)
def pytest_sessionfinish(session, exitstatus):
    fd = int(os.environ["AQ_VERIFIER_CENSUS_FD"])
    c = {"schema": "aq.pytest-census.v1", "sessionfinish_reached": True,
         "collected_count": 1, "passed_count": 1, "failed_count": 0, "skipped_count": 0,
         "error_count": 0, "internal_error": False, "clean": True}
    os.write(fd, json.dumps(c, sort_keys=True, separators=(",", ":")).encode() + b"\n")
```

Observed: `verdict=PASS passed=1 collected=1 census_valid=True`, and
`verify_verdict.py` prints `ACCEPT: signed PASS matches every approved authorization binding`.
`prove_controls.py` runs this exact attack and prints it under a `KNOWN_LIMITATION` banner so the
gap is never silent.

The forge does not even require the advertised `AQ_VERIFIER_CENSUS_FD`: the pipe write end is an
inherited descriptor discoverable through `/proc/self/fd`, so withholding the env var only adds
obscurity, not soundness.

## Why it cannot be closed in this sandbox model

The de-correlated reviewer's prescription — run the candidate pytest at a **lower uid** (65534)
than the census-owning supervisor (65532), and hand the census channel only to the supervisor uid —
is not achievable here, for two independent reasons:

1. **No uid boundary is available.** The sandbox is intentionally non-root (`--user 65532:65534`),
   `--cap-drop ALL`, `--security-opt no-new-privileges:true`. A process in it cannot `setuid` to any
   other uid — verified live: `setuid(65534)` and `setuid(0)` both return `EPERM` ("Operation not
   permitted"). Dropping the candidate to a different uid would require either starting the container
   as root or granting `CAP_SETUID`, both of which weaken the exact non-root / all-capabilities-
   dropped posture that the six-capability BLOCKED-in-sandbox contrast depends on, and that the
   security reviewer requires be preserved.

2. **Even with a uid boundary, the plugin shares the candidate's process.** pytest imports and
   executes candidate code (conftest, test modules) in the same process that runs the census plugin.
   Whatever uid that process has, the census authority and the candidate share it. There is no
   in-process secret (env, memory, module state, fd) that candidate code cannot read, and no fd the
   plugin can write that candidate code cannot also write. Running that process at 65534 instead of
   65532 does not separate the census authority from the candidate; it only relabels both.

The deeper reason is fundamental: **when the candidate supplies the code that runs its own tests, a
zero-test repository can perfectly emulate the observable behavior of a passing suite.** "Run my
tests and they pass" is indistinguishable, from outside the process, from "run my code that reports
and exits as if tests passed." The one OS-level signal a supervisor can trust — that the candidate
was *powerless* (no network, no push credential, no control-plane write, no outside write, no PID-1
secret) — is exactly what the six-capability contrast soundly proves. Test *authenticity* is a
different and, under this input model, unattainable guarantee.

## What a sound design would require

Any of the following would genuinely close the census forgery; each is a larger redesign than this
task's scope and changes the trust model or the sandbox contract:

- **Take test definition and execution away from the candidate.** The trusted layer, not candidate
  conftest/plugins, decides what a test is and runs it. Candidates supply data/fixtures, not
  in-process Python that runs in the census authority's process. This abandons "run the candidate's
  own pytest" as the contract.
- **A real privilege boundary with an out-of-process census authority.** A root (or `CAP_SETUID`)
  supervisor drops the candidate to a lower uid AND the census is produced by a process the candidate
  uid cannot influence. Note this still has to solve "collection imports candidate code," so it
  generally collapses into the first bullet; and it requires relaxing the non-root / cap-dropped
  posture, so the six-capability contrast would have to be re-established under the new model.
- **Attested, per-assertion evidence** that cannot be manufactured without executing real test logic
  (e.g. a trusted assertion library that emits signed evidence from a TEE / different trust domain).
  Out of scope for a Linux-container primitive.

## Recommended interim posture

Treat the hermetic verifier as a sound **powerlessness** gate (the six-capability contrast is real
and unforgeable) and an **anti-accident / anti-casual-forgery** test gate, not as a sound proof of
test authenticity against an adversarial candidate. Do not let a single hermetic `PASS` be the only
control on a high-consequence action from an untrusted candidate; pair it with provenance the
candidate cannot author (e.g. review of the actual diff, or a trusted-harness test run).
