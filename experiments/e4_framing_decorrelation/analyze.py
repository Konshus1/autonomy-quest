"""E4 analysis per PREREGISTRATION.md measures 1-5."""
import json
import os
from collections import defaultdict
from itertools import combinations

HERE = os.path.dirname(os.path.abspath(__file__))
raw = json.load(open(os.path.join(HERE, "raw_results.json")))
probs = json.load(open(os.path.join(HERE, "problems.json")))["problems"]
truths = {p["id"]: p["truth"] for p in probs}


def correct(ans, truth):
    return ans is not None and abs(ans - truth) <= max(0.005, 0.01 * abs(truth))


solves = defaultdict(list)   # (arm, pid) -> [solve dicts]
for s in raw["solves"]:
    solves[(s["arm"], s["problem"])].append(s)
finals = {(r["arm"], r["problem"]): r for r in raw["recons"]}

print("=" * 100)
print(f"{'problem':<16}", end="")
for arm in "AB":
    print(f"| Arm {arm}: s1 s2 s3 -> final (answers)", " " * 8, end="")
print()
patterns = {"A": defaultdict(int), "B": defaultdict(int)}
same_wrong = {"A": [0, 0], "B": [0, 0]}  # [all-wrong count, same-wrong-number count]
mcnemar = [0, 0]  # b: A right B wrong, c: A wrong B right
disagree_final = {"A": defaultdict(lambda: [0, 0]), "B": defaultdict(lambda: [0, 0])}

for p in probs:
    pid = p["id"]
    row = f"{pid:<16}"
    fc = {}
    for arm in "AB":
        ss = sorted(solves[(arm, pid)], key=lambda s: s["slot"])
        cs = [correct(s["answer"], truths[pid]) for s in ss]
        f = finals[(arm, pid)]
        fcor = correct(f["answer"], truths[pid])
        fc[arm] = fcor
        n = sum(cs)
        pat = "unan-right" if n == 3 else ("unan-wrong" if n == 0 else "mixed")
        patterns[arm][pat] += 1
        if n == 0:
            same_wrong[arm][0] += 1
            answers = [s["answer"] for s in ss]
            if len({round(a, 4) if a is not None else None for a in answers}) == 1:
                same_wrong[arm][1] += 1
        # disagreement in ANSWERS (not correctness): distinct rounded answers
        distinct = len({round(s["answer"], 4) if s["answer"] is not None else None for s in ss})
        key = "unanimous" if distinct == 1 else "disagreed"
        disagree_final[arm][key][0] += fcor
        disagree_final[arm][key][1] += 1
        marks = "".join("Y" if c else "n" for c in cs)
        ansstr = ",".join("-" if s["answer"] is None else f"{s['answer']:.3g}" for s in ss)
        row += f"| {marks} -> {'Y' if fcor else 'N'} ({ansstr})".ljust(42)
    if fc["A"] and not fc["B"]:
        mcnemar[0] += 1
    if fc["B"] and not fc["A"]:
        mcnemar[1] += 1
    print(row)

print("=" * 100)
for arm in "AB":
    fin_acc = sum(
        correct(finals[(arm, p['id'])]["answer"], truths[p["id"]]) for p in probs
    )
    ss = [s for k, v in solves.items() if k[0] == arm for s in v]
    samp_acc = sum(correct(s["answer"], truths[s["problem"]]) for s in ss)
    print(f"\nArm {arm}: final {fin_acc}/12, samples {samp_acc}/36, patterns {dict(patterns[arm])}")
    aw, sw = same_wrong[arm]
    print(f"  all-3-wrong problems: {aw}; of those, same wrong NUMBER: {sw}")
    # pairwise correctness agreement
    agree = tot = 0
    for p in probs:
        cs = [correct(s["answer"], truths[p["id"]]) for s in
              sorted(solves[(arm, p["id"])], key=lambda s: s["slot"])]
        for a, b in combinations(cs, 2):
            agree += a == b
            tot += 1
    print(f"  pairwise correctness agreement: {agree}/{tot} = {agree/tot:.2f}")
    for key in ("unanimous", "disagreed"):
        c, n = disagree_final[arm][key]
        print(f"  final correct | samples {key}: {c}/{n}")

# per-framing accuracy (arm B)
print("\nPer-framing sample accuracy (Arm B):")
by_f = defaultdict(lambda: [0, 0])
for s in raw["solves"]:
    if s["arm"] == "B":
        by_f[s["framing"]][0] += correct(s["answer"], truths[s["problem"]])
        by_f[s["framing"]][1] += 1
for f, (c, n) in sorted(by_f.items()):
    print(f"  {f}: {c}/{n}")
print(f"\nMcNemar: A-right/B-wrong = {mcnemar[0]}, A-wrong/B-right = {mcnemar[1]}")
