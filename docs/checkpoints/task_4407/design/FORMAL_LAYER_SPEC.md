# DESIGN SPEC — Executable Formal Causal Principles into the Causal-Edge Model

*Parallel-worker deliverable (2026-08-02), verified by the living manager. Targets the ROADMAP items in WORKFLOW_MAP.md §8 (fuzzy→formal promotion + the ASP/PDDL layer). Peer supplier: hrf-2878 (15 ASP/clingo encodings + PDDL action-models + RED/GREEN oracles). Doctrine: BB #746/#764.*

## 0. Two invariants this must never break
1. **Immutable identity** — `edge_identity` binds only `(cause,effect,scope)`. Everything added is mutable STATE at that identity.
2. **Earned support** — `support_count` increments ONLY inside `record_evidence` on a `confirm`. No new code writes it directly.

## 1. The four state transitions of an edge
```
[MINED]      fuzzy/judgment/advisory,  executor.kind=judgment, pc=0.34
  │ (A) attach a vetted formal executor — sets directness/strictness + executor, NOT formality
[ARMED]      fuzzy/script/hard,        executor.kind=script|constraint, pc=0.34, executor_verified=False
  │ (B) operational confirms via the surprise loop → support_count climbs (earned)
[EVIDENTIAL] evidential, support ≥ promote_support
  │ (C) GREEN oracle proof on the executor → proof-evidence → evidential→formal
[FORMAL]     formal/script/hard, is_guaranteed=True, pc:=1.0 → plan_certainty scores step 1.0  (D)
```
**Doctrinal decision to ratify in BB before coding:** operational confirms can only carry an edge to `evidential`; the `evidential→formal` rung REQUIRES a passing formal oracle (statistical vs deductive evidence). This honors "formal = guaranteed" without letting mere repetition mint a guarantee.

New code lives in a new package `ralph_portable/formal/`; only two surgical edits touch `causal_edges.py` + `causal_edge_store.py`.

## 2. (a) Attaching a constraint/script executor
- hrf-2878 delivers a **signed, content-addressed registry**, never inline code: `ralph_portable/formal/encodings/manifest.json` (per-asset: `kind` constraint(ASP)|script(PDDL), `lang`, `payload` path, `digest` sha256, `oracle` path, `oracle_digest`, `binds` canonical cause/effect). Payloads under `encodings/{asp,pddl,oracle}/`.
- `executor_registry.py`: `load_registry`, `resolve`, `verify_payload` (recompute sha256 vs pinned digest — **mismatch → hard error, nothing runs**). The edge's executor `ref` is a `registry_key`, never code.
- `attach.py::attach_executor(edge, registry_key)` — pure; sets `executor={kind,ref=key,lang}`, `directness=script`/`strictness=hard`, `executor_verified=False`, `executor_provenance`; **formality UNCHANGED**; never touches identity or support. Legal vs `validate_causal_edge` (only `formal+judgment` is forbidden; `fuzzy+script` validates).
- `binds` names the canonical predicate an encoding formalizes; attach validates `binds` matches the edge's cause/effect so an encoding can't be stapled onto an unrelated edge.
- Stores gain `attach_executor(ident, registry_key)`; API `POST /api/causal/attach-executor` (operator/CI-auth, off the loop hot path). Add `executor_verified/executor_provenance/oracle_proof` to `_SYSTEM_MANAGED_EDGE_FIELDS`.

## 3. (b) A passing oracle → promotion evidence (through the SAME earned-support channel)
- `causal_edges.formal_proof_evidence(registry_key, oracle_result)` → a `confirm`-typed surprise record (`surprise_type="formal_oracle_proof"`, carries `registry_key`+`oracle_digest`). `record_evidence` appends it and (signal==confirm) increments support through the one sanctioned path; the wrapping store method sets `executor_verified=True`.
- Extend `propose_update`: at the `formal` rung, additionally require `executor_verified AND executor.kind in (script,constraint)`. Net gate to reach formal: `support ≥ promote_support` AND non-judgment executor AND `executor_verified`. `fuzzy→evidential` unchanged.
- Actuation: `apply_promotion(ident, proposal)` sets `formality=proposal.to`, and **on formal+script sets `predicted_certainty=1.0`** (required for the 1.0 score; justified only by the GREEN oracle). `verify_and_record(ident, registry_key)` runs the oracle and on GREEN records the proof; RED/ERROR record nothing (fail-closed).

## 4. (c) Oracle/clingo harness — the VERDICT boundary (NOT OS confinement)
> **Boundary honesty (adversarial-review MED, 2026-08-02):** this harness is fail-closed on the
> VERDICT channel (only a clean digest-verified deterministic GREEN records a proof), but it does
> NOT confine an oracle's CAPABILITIES — a digest-verified oracle runs with the operator's rights
> and can read/write files + reach the network. The trust root is the **digest-pinned, reviewed
> registry + operator/CI-only invocation**, not a jail. The oracle-supplied `reason` is untrusted
> and hard-capped (no exfil channel into evidence). RLIMIT_AS is unenforced on macOS. The nsjail/
> `--net none`/read-only-rootfs controls below are the *preferred* confinement (future work); the
> rlimits are the portable fallback. Public claims must say "rlimit-bounded, digest-pinned,
> review-rooted; verdict fail-closed; NOT filesystem/network-confined" — never "sandboxed" bare.

`ralph_portable/formal/oracle_harness.py::run_oracle(registry_key) -> {result: GREEN|RED|ERROR,...}`. Treat every payload as hostile. Non-negotiable controls:
1. **No arbitrary exec** — only manifest-present, digest-verified keys run; else ERROR.
2. **No shell ever** — `subprocess.run([bin,*argv], shell=False)`, minimal scrubbed env, absolute allow-listed binaries.
3. **Isolation, fail-closed** — preferred: nsjail/firejail/container, `--net none`, read-only rootfs, tmpfs workdir, non-root, seccomp default-deny. Fallback: `resource.setrlimit` (RLIMIT_CPU~5s, AS~512MB, FSIZE, NOFILE, NPROC anti-fork-bomb) + wall-clock SIGKILL. **If neither sandbox nor rlimits can be set → ERROR, refuse to run.**
4. **Timeouts layered** (clingo `--time-limit`, subprocess timeout, outer watchdog); timeout → ERROR.
5. **Bounded I/O**; oracle emits a single JSON line `{"result":"GREEN"|"RED"}`; anything else → ERROR.
6. **Determinism** (fixed seed / single model); differing rerun → ERROR.
7. **Off the hot path** — invoked ONLY by `POST /api/causal/verify` (operator/CI). `runner/loop.py` NEVER spawns clingo; the loop's causal calls stay pure read/record.
8. **Fail-closed mapping** — GREEN⇒evidence; RED⇒non-confirming note (never increments support); ERROR/timeout/sandbox-failure⇒nothing recorded.

## 5. (d) plan_certainty scores formal+script at 1.0
`is_guaranteed = formal AND script` → True once `apply_promotion` writes `formality=formal` on the armed (`directness=script`) edge. `edge_certainty = min(predicted_certainty, formality_cap=1.0)`; a mined edge has pc=0.34, so `apply_promotion` sets pc=1.0 on formal+script → `min(1.0,1.0)=1.0`. A high pc is unreachable via PUT (stripped) or on a fuzzy edge (capped 0.34) — only through `apply_promotion` after a GREEN oracle.

## 6. Triggers (loop stays read-only)
Mine (loop, best-effort, existing) · Arm/Verify/Promote = operator/CI, OFF the loop path. The autonomous loop keeps doing only consult(read-only)+record-surprise+mine — so "I don't act on principles yet" stays literally true until an operator formalizes them.

## 7. Identity + earned-support preservation (audit)
Add a store-level assertion `edge_identity(new)==ident` after attach/verify/promote. Grep-guard: `support_count =` assignments exist only in `record_evidence`. Extend `_SYSTEM_MANAGED_EDGE_FIELDS` with `executor_verified/executor_provenance/oracle_proof`; refuse an external PUT that sets `formality=formal` on an unverified edge (closes a laundering hole). Operational confirms (`planning_prediction_surprise`) vs formal proofs (`formal_oracle_proof`+registry_key+digest) are distinguishable in `evidence[]`.

## 8. Change manifest
New `ralph_portable/formal/`: `executor_registry.py`, `attach.py`, `oracle_harness.py`, `encodings/manifest.json`+payloads (from hrf-2878). Edit `causal_edges.py` (add `formal_proof_evidence`; extend `propose_update` formal rung). Edit both stores (`attach_executor`, `verify_and_record`, `apply_promotion` + identity assertion). Edit `app.py` (routes attach-executor/verify/promote, operator-auth; extend field-strip; PUT-formal guard). **No change to `runner/loop.py`, `causal_sync.py`, `principle_mining.py`.**

## 9. Contract questions for hrf-2878 (settle before coding)
1. Manifest schema + `binds` semantics + digest trust root/signing.
2. Oracle verdict protocol (single JSON line, exit-code, network-free, deterministic, within CPU/mem budget; RED carries a machine-readable reason).
3. **Ratify the rung doctrine** (§1) as a BB decision under #746 — it tightens a live gate.
4. `binds` granularity (specific identity vs predicate class).
