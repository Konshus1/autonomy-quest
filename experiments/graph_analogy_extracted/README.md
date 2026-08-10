# Machine-extracted graph analogy experiment

This is the extraction and downstream-usefulness continuation of task #4834. It uses one novel
plain-text ransomware situation. A subscription model extracted the target graph while blinded
from all source cases, transfer relations, and winner identity. The deterministic pure-Python
matcher then loaded that frozen JSON graph from disk and ran offline.

## Reproduce the verdict

```bash
uv run python experiments/graph_analogy_extracted/run_experiment.py \
  --output experiments/graph_analogy_extracted/receipt.json
uv run pytest \
  tests/test_structural_analogy_discrimination.py \
  tests/test_structural_analogy.py \
  tests/test_structural_analogy_extracted.py -q
```

The compact earning output is:

```text
verdict: neither (under sharpened baseline-absence criterion)
selected: wildfire_firebreak
rewired accepted: false
opaque accepted: true
no-good-source abstained: true
planner path interruption: baseline=true surface=true structural=true
new approach: false
structural candidate order: M2, Q7
surface candidate order: Q7, M2
blind judge: M2, R9, Q7; M2 viable=true, Q7 viable=false
```

The exact receipt contains scores, mappings, the projected plan, controls, the blinded judgment,
and its falsifier.

## What happened

The extractor emitted a connected two-edge graph from verbatim evidence:

```text
infected_laptop --propagation--> peer_machines --exposure--> payroll_computers
```

It emitted no response action. It explicitly rejected segmentation, firewalling, isolation, and
other unobserved interventions. The held-out human reference instead represented the first two
roles as `encryption_process` and `peer_connections`. This is real node-boundary noise, not an
exact extraction. The family topology still aligned with the human reference at score 1.75.

On the extracted graph Stage 2 rejected the same-domain malware scanner and selected the
cross-domain wildfire firebreak. The result survived the controls that matter:

- same surface record and relation-family multiset with endpoints rewired: rejected;
- every source label and predicate changed to opaque garbage: accepted with the same score;
- the only structurally accepted source removed: abstained rather than transferring.

## Downstream verdict under the sharpened criterion: neither

The extracted-graph run shows a real **structure-versus-surface disagreement**, but it does not
meet the sharpened existence-proof gate. Structural matching preferred M2, dependency-aware
peer-path containment, while surface retrieval preferred Q7, an alert dashboard followed by the
already-failed host-by-host approach. The blinded judge preferred M2 (4/4 versus 0/4).

However, the no-analogue baseline independently proposed blocking peer paths and preserving only
payroll dependencies. Structural retrieval therefore did not give this planner a useful element
it lacked, and the baseline was not unable to distinguish the viable direction. The earlier
"validation" label is retained only as a secondary surface-disagreement result, not the requested
planning-usefulness existence proof.

Five additional candidate cases were prospectively stopped at the baseline-absence gate. In every
case the no-analogue planner produced the exact hoped-for analogue element:

1. co-processed sacrificial witness cartridge;
2. recursive descendant-lease surrender before reopening a ledger range;
3. capture-recapture estimation of infections missed by two detectors;
4. physical trust-epoch rotation for an unenumerable cylinder recall;
5. Chao1 singleton/doubleton estimation of undiscovered fuzz failures.

Their target prompts and complete baseline outputs are frozen in `strict_existence_probes.json`.
They were not promoted into extraction/source experiments because doing so could not satisfy the
pre-registered control.

The honest result is therefore:

> Relational matching survived machine extraction and beat a lexical surface selector on one
> candidate judgment, but no tested case showed this strong baseline planner receiving a valid
> planning element it did not already produce. The sharpened existence proof remains unearned.

A passing case would require the structural arm alone to add a valid element absent from baseline,
surface, and random arms, or to rank a viable candidate that the no-analogue baseline genuinely
could not distinguish.

## Model-call and audit protocol

All ten model calls used `openai-codex/gpt-5.6-sol` through the subscription RLM path: five
core extraction/planning/judging calls and five baseline-absence probes. Metered cost was $0. The offline reproduction makes zero network calls. Files are frozen audit artifacts:

- `target.txt`: the only situation text shown to the extractor;
- `extractor_prompt.txt`: fixed schema, ontology, blinding, and no-intervention rule;
- `extracted_target.raw.json`: extractor output including verbatim evidence and rejected inferences;
- `human_reference.json`: post-freeze human reference graph;
- `planner_outputs.json`: identical-instruction baseline/surface/structural planner arms;
- `candidate_approaches.json`: candidate-to-source association;
- `blind_candidate_evaluation.json`: retrieval-blinded judgment and falsifier;
- `strict_existence_probes.json`: rejected target prompts and complete no-analogue outputs;
- `receipt.json`: executable result.

## Native file-backed, no-AGE path

`ralph_portable.structural_analogy` now exposes:

```python
from ralph_portable.structural_analogy import load_situation_file, load_situation_library_file

target = load_situation_file("extracted_target.raw.json")
sources = load_situation_library_file("corpus.json", key="sources")
```

The loader accepts evidence/audit fields without feeding them into matching. No Apache AGE,
database, NetworkX, network, or optional dependency is required.
