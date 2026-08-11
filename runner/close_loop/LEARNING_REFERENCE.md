# Learning-reference propagation & grounding verification

The grounding anchor for the close-the-loop **grounded-evaluator gate**
(`docs/design/close-loop-grounded-evaluator-gate.md`). It closes the single wire
the design names: a close-the-loop work product must carry a **learning
reference** — typed links to the prior experiences that generated the change — so
the evaluator's strongest check has something to retrieve and verify.

This is a **clean library the evaluator calls**. It contains no LLM judge, arms
nothing, and is not wired into the live loop. Module:
`runner/close_loop/learning_reference.py`. Tests:
`tests/test_close_loop_learning_reference.py`.

## What it reuses (extends, not reinvents)

- `runner.consultants.self_correction.ReferenceEvent` / `ReferenceKind` — the
  reference-event vocabulary; `ExperienceCitation.for_reference_event(...)` builds
  a citation directly from one.
- `runner.close_loop.hashing.canonical_digest` / `canonical_json` — the same
  domain-separated SHA-256 machinery the intent/observation/mission hashes use.
- The **governance grounding** model (`schema/022`, `schema/025`): a learning is
  valid only against evidence owned by a **separate principal** the candidate
  cannot author — here `aq_evaluator`, a human corrector, etc. This library is
  that same move applied to a work product instead of a causal principle.

## Half 1 — Propagation (durable + tamper-evident)

`LearningReference(candidate_principal, citations)` is the durable structured
record emitted onto the work product (intended for the close-loop work row /
`task_work_link`, and/or a git commit trailer).

Reference payload (canonical JSON, schema `aq-close-loop/learning-reference/v1`):

```json
{
  "schema": "aq-close-loop/learning-reference/v1",
  "candidate_principal": "aq_worker:candidate-7",
  "citations": [
    {"reference_type": "correction",         "reference_id": "corr-1", "owning_principal": "human:kevin"},
    {"reference_type": "grounded_principle", "reference_id": "prin-9", "owning_principal": "aq_evaluator"}
  ]
}
```

`reference_type` ∈ `{correction, reference_event, grounded_principle}`. Citations
are de-duplicated and canonically sorted, so the digest is independent of
authoring order.

**Tamper-evidence** binds the reference digest into the task's existing
intent/lineage integrity:

```
reference_digest = SHA256("aq-close-loop/learning-reference/v1" ‖ canonical_json(payload))
lineage_hash     = SHA256("aq-close-loop/learning-lineage/v1"   ‖ {intent_hash, reference_digest})
```

`lineage_hash(intent_hash)` is what the work row / commit trailer records at
emission. Altering *any* citation changes `reference_digest` → changes
`lineage_hash` → `verify_lineage_integrity(reference, intent_hash, stored)` returns
`False`. It also binds `intent_hash`, so rebasing the reference onto a different
admitted intent is detected too.

**Git commit trailers** carry the same record so the immutable git object also
witnesses it:

```
Learning-Reference: <base64 canonical-json payload>
Learning-Reference-Digest: <64-hex reference_digest>
Learning-Lineage: <64-hex lineage_hash>
```

`parse_commit_trailers(message)` returns `None` for an ordinary (non-learning)
commit, reconstructs the `LearningReference` from the payload otherwise, and
**fails closed** (`LearningReferenceError`) on a present-but-malformed payload.
A verifier recomputes the digest/lineage from the reconstructed reference; a
forged payload line no longer matches the (stale) `Learning-Lineage` trailer.

## Half 2 — Retrieval + verification

```python
verify_learning_reference(reference, store, *, authenticated_candidate_principal) -> GroundingVerification
```

`store: ExperienceStore` is the system's own record (the real impl reads the
grounding ledger under a principal the candidate does not control;
`InMemoryExperienceStore` is the test/reference impl). Per citation, fail-closed:

| Check | Failing status | Meaning |
|---|---|---|
| (0) authentic self-identity | `CANDIDATE_IDENTITY_MISMATCH` | reference's self-declared `candidate_principal` ≠ authenticated identity (whole reference) |
| (i) exists | `DANGLING` | store cannot resolve `(type, id)` |
| (ii) separate principal | `SAME_PRINCIPAL` | store's authoritative owner **is** the authenticated candidate |
| (iii) honest provenance | `CLAIMED_OWNER_MISMATCH` | citation's claimed owner ≠ store's authoritative owner |
| all pass | `GROUNDED` | record returned in `grounded_experiences` for the evaluator |

The store's `owning_principal` is authoritative — never the candidate's claim.
Overall `GroundingVerdict`:

- `GROUNDED` — every citation grounded; `grounded_experiences` returned for the
  evaluator to judge semantically (this library renders no semantic verdict).
- `UNGROUNDED` — at least one citation rejected (`reason_codes()` lists them).
- `NO_REFERENCE` — empty reference: an ordinary non-learning change, *"no
  grounding to check"*, **not an error** (design fallback).
- `CANDIDATE_IDENTITY_MISMATCH` — the self-declared author disagrees with the
  authenticated identity: a provenance lie about *who the candidate is*.

### The self-grounding forge, and why the authenticated identity is required (F1)

A de-correlated review demonstrated a live forge against the first cut, and this
is the fix. The separate-principal check must not compare the store owner to
`reference.candidate_principal` — that field rides in the **candidate's own**
commit-trailer payload, so the candidate would be authoring *"who the candidate
is."* It cites its own store record (owner `P`), sets `citation.owning_principal
= P` (provenance passes), and sets `reference.candidate_principal` to a lexical
variant of `P` (trailing space / different case / an NFD/combining-Unicode form).
A raw `==` sees `P == P-variant → False`, `SAME_PRINCIPAL` never fires, and the
self-owned learning grounds itself.

Closed by two changes:

1. **`authenticated_candidate_principal` is a required keyword argument** supplied
   by the trusted evaluator context. The separate-principal check compares the
   store owner against *that* identity, never against the untrusted self-declared
   field. The self-declared `candidate_principal` is instead cross-checked against
   the authenticated identity; a disagreement is `CANDIDATE_IDENTITY_MISMATCH`.
2. **Every principal comparison is normalized** with `_canonical_principal`
   (`unicodedata.normalize("NFC", …).strip().casefold()`, extending the house
   `hashing._normalise` NFC form) so a lexical variant cannot slip between the
   self-identity and separate-principal checks. Normalization only ever folds two
   names to *equal*, i.e. errs toward "not independent" — fail-closed.

### Integration preconditions the evaluator MUST honor

- **F2 — trusted store.** `ExperienceStore` is a caller-wired `Protocol`; the
  `@runtime_checkable isinstance` guard only checks that a `fetch` attribute
  exists. **Security rests entirely on the evaluator passing a TRUSTED store**
  that reads `schema/022` + `schema/025` under a principal the candidate does not
  control. A store the candidate can influence voids the whole grounding
  guarantee. This library cannot and does not verify store trust.
- **F2 — authenticated identity.** Likewise, `authenticated_candidate_principal`
  must come from trusted evaluator context (the spawn record / lease owner), never
  from anything the candidate emits.
- **F3 — NO_REFERENCE is not a pass.** A candidate can omit citations to reach
  `NO_REFERENCE` and skip grounding. The library correctly does **not** treat
  `NO_REFERENCE` as grounded, but **the evaluator MUST reject `NO_REFERENCE` for
  any change that claims to be a learning** (correction-driven / principle-grounded
  / self-improvement). `NO_REFERENCE` is legitimate only for an ordinary
  non-learning change, which then falls back to the discriminate + artifact checks.

## Reproduce

```bash
cd /Users/kevincthomas/src/aq-wt-learnref
python3 -m pytest tests/test_close_loop_learning_reference.py -q   # 23 passed
python3 -m pytest -q                                                # full suite green
```

## Red-first evidence

Replacing `verify_learning_reference` with a plausible-default strawman
("a citation present in the reference is grounded"; tamper check always `True`)
fails exactly the load-bearing assertions and passes only the non-discriminating
ones:

```
FAILED  test_dangling_citation_is_rejected_as_ungrounded
FAILED  test_citation_owned_by_candidate_is_not_independently_grounded
FAILED  test_one_bad_citation_taints_the_whole_reference
FAILED  test_altering_a_citation_after_emission_breaks_the_lineage_hash
FAILED  test_tampered_trailer_payload_breaks_the_declared_lineage
FAILED  test_claimed_owner_that_disagrees_with_the_store_is_rejected
6 failed, 10 passed
```

The correct implementation: `16 passed`. Each test asserts on **content** (the
specific rejection status / returned records), not mere existence, so a
wrong-but-non-empty result does not pass.

**F1 self-grounding forge (post-review).** Reverting `verify_learning_reference`
to the vulnerable form (compare store owner to the self-declared
`reference.candidate_principal`, raw `==`, no authenticated identity) grants
`GROUNDED` to all three self-grounding spoofs and passes the two identity-lie
tests through:

```
FAILED  test_self_grounding_forge_via_identity_variant_is_rejected[trailing_space]
FAILED  test_self_grounding_forge_via_identity_variant_is_rejected[case]
FAILED  test_self_grounding_forge_via_identity_variant_is_rejected[unicode_nfd]
FAILED  test_separate_principal_check_ignores_self_declared_field
FAILED  test_genuinely_different_self_declared_identity_is_a_provenance_lie
```

Each spoof reaches `GroundingVerdict.GROUNDED` under the vulnerable logic (the
forge succeeds). The fix rejects all three as `SAME_PRINCIPAL` / `UNGROUNDED`
while an honest separate-principal citation of the same shape still grounds. Full
file with the fix: `23 passed`; full suite: `587 passed`.

## Scope / non-goals

- No LLM judge (the evaluator, built separately). This library only retrieves and
  verifies grounding, and hands back the independently-owned experiences.
- No live-loop wiring and nothing armed. `PUBLIC_MAIN_ACTUATOR_ENABLED` stays
  `False`.
