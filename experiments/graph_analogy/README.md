# Graph analogy discrimination experiment

This offline, zero-network experiment exercises the proposed replacement for surface-only frame
matching. It is advisory and unwired.

```bash
uv run python experiments/graph_analogy/run_demo.py --output experiments/graph_analogy/receipt.json
uv run pytest tests/test_structural_analogy_discrimination.py tests/test_structural_analogy.py -q
```

See [`docs/research/graph-constrained-analogy.md`](../../docs/research/graph-constrained-analogy.md)
for mechanism, claim ceiling, controls, and the unresolved graph-extraction problem.
