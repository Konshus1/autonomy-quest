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
verdict: validation
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

## Downstream verdict: validation only

The downstream comparison did **not** earn a new-approach claim. Independently run baseline,
surface-source, and structural-source planner arms all proposed blocking or restricting the
peer-to-peer path. The structural analogue therefore did not surface a direction the other arms
missed.

It did earn the narrower **validation/ranking** result on this case. Two source-backed candidates
were frozen:

- `Q7`, from the surface-selected alert-dashboard case: leave topology unchanged, group alerts,
  and continue prioritized host-by-host cleanup;
- `M2`, from the structurally selected firebreak case: quarantine compromised machines, deny
  nonessential peer paths, and explicitly allow-list monitored payroll dependencies.

Surface retrieval ranked `Q7 > M2` (0.18703704 vs 0.03773585). Structural matching ranked
`M2 > Q7` (1.75 vs 0.0). A separate judge, blinded from retrieval regime and source identity,
ranked `M2 > R9 > Q7`, scored M2 4/4 and Q7 0/4, and marked only M2 viable. Its explicit
falsifier was evidence that the allow-listed dependencies cannot keep payroll operating or that
the controls cannot block the actual propagation path.

This is one controlled example, not a benchmark and not an outcome trial. The defensible claim is:

> On one frozen machine-extracted target graph, graph-based structural analogy ranked the viable
> candidate above the surface-selected candidate when surface and structure disagreed.

It does not establish that extraction is generally reliable, that the proposed action works in a
live network, or that analogy improves mission outcomes.

## Model-call and audit protocol

All five model calls used `openai-codex/gpt-5.6-sol` through the subscription RLM path; metered cost
was $0. The offline reproduction makes zero network calls. Files are frozen audit artifacts:

- `target.txt`: the only situation text shown to the extractor;
- `extractor_prompt.txt`: fixed schema, ontology, blinding, and no-intervention rule;
- `extracted_target.raw.json`: extractor output including verbatim evidence and rejected inferences;
- `human_reference.json`: post-freeze human reference graph;
- `planner_outputs.json`: identical-instruction baseline/surface/structural planner arms;
- `candidate_approaches.json`: candidate-to-source association;
- `blind_candidate_evaluation.json`: retrieval-blinded judgment and falsifier;
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
