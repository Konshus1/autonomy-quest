"""E4 ground-truth verification. Every problem's answer is verified here by
Monte-Carlo simulation (or exact enumeration) BEFORE any model is called.
Run: python3 verify_answers.py  -> prints PASS/FAIL per problem."""
import random
import itertools

random.seed(4834)
N = 400_000
results = {}

# 1 bus-wait: Poisson arrivals rate 4/hr; random rider's expected wait (min).
# Memorylessness => 15 min. Simulate: exponential gaps, size-biased interval.
gaps = [random.expovariate(4) for _ in range(N)]
t_end = sum(gaps)
# pick uniform random times, find wait to next bus
times = []
acc = 0
arrivals = []
for g in gaps:
    acc += g
    arrivals.append(acc)
import bisect
waits = []
for _ in range(50_000):
    t = random.uniform(0, t_end * 0.99)
    i = bisect.bisect_left(arrivals, t)
    waits.append((arrivals[i] - t) * 60)
results["bus-wait"] = (sum(waits) / len(waits), 15.0)

# 2 monty-fall: host slips, opens a random unpicked door which HAPPENS to show
# a goat; switching wins with p=1/2 (condition on goat revealed).
wins = tot = 0
for _ in range(N):
    car = random.randrange(3)
    pick = random.randrange(3)
    opened = random.choice([d for d in range(3) if d != pick])
    if opened == car:
        continue  # condition: goat was revealed
    tot += 1
    switch = next(d for d in range(3) if d not in (pick, opened))
    wins += switch == car
results["monty-fall"] = (wins / tot, 0.5)

# 3 tuesday-boy: two children, uniform sex+weekday; families with at least one
# Tuesday-born boy; P(two boys) = 13/27.
num = den = 0
for _ in range(N):
    kids = [(random.random() < 0.5, random.randrange(7)) for _ in range(2)]
    if any(boy and day == 2 for boy, day in kids):
        den += 1
        num += all(boy for boy, _ in kids)
results["tuesday-boy"] = (num / den, 13 / 27)

# 4 class-size: sizes 10,20,90; expected class size of a random STUDENT.
sizes = [10] * 10 + [20] * 20 + [90] * 90
results["class-size"] = (sum(sizes) / len(sizes), 8600 / 120)

# 5 waiting-HH: expected flips to first see HH.
tot = 0
for _ in range(200_000):
    seq = ""
    while not seq.endswith("HH"):
        seq += random.choice("HT")
    tot += len(seq)
results["waiting-HH"] = (tot / 200_000, 6.0)

# 6 secretary-4: n=4, skip first 1 then take first candidate better than all seen.
wins = 0
for _ in range(N):
    perm = list(range(4))
    random.shuffle(perm)  # perm[i] = rank (3=best)
    best_seen = perm[0]
    chosen = perm[-1]
    for x in perm[1:]:
        if x > best_seen:
            chosen = x
            break
    wins += chosen == 3
results["secretary-4"] = (wins / N, 11 / 24)

# 7 stpete-10: payout 2^k if first head on flip k<=10, else 0.
tot = 0
for _ in range(N):
    for k in range(1, 11):
        if random.random() < 0.5:
            tot += 2 ** k
            break
results["stpete-10"] = (tot / N, 10.0)

# 8 gamblers-ruin: p=0.6, start 2, target 5. P(reach 5 before 0).
wins = 0
for _ in range(N):
    x = 2
    while 0 < x < 5:
        x += 1 if random.random() < 0.6 else -1
    wins += x == 5
results["gamblers-ruin"] = (wins / N, 135 / 211)

# 9 die-6-all-even: roll until first 6; condition on ALL rolls even; E[#rolls].
tot = cnt = 0
for _ in range(2_000_000):
    rolls = []
    while True:
        r = random.randrange(1, 7)
        rolls.append(r)
        if r == 6:
            break
    if all(r % 2 == 0 for r in rolls):
        cnt += 1
        tot += len(rolls)
results["die-6-all-even"] = (tot / cnt, 1.5)

# 10 family-boys: each family has kids until first boy; fraction of boys in population.
boys = kids = 0
for _ in range(N):
    while True:
        kids += 1
        if random.random() < 0.5:
            boys += 1
            break
results["family-boys"] = (boys / kids, 0.5)

# 11 double-test: prevalence 1%, two independent tests, sens 90%, spec 90%,
# both positive; P(disease).
num = den = 0
for _ in range(N * 3):
    d = random.random() < 0.01
    t1 = (random.random() < 0.9) if d else (random.random() < 0.1)
    t2 = (random.random() < 0.9) if d else (random.random() < 0.1)
    if t1 and t2:
        den += 1
        num += d
results["double-test"] = (num / den, 0.45)

# 12 series-length: best-of-7, even teams; expected number of games.
tot = 0
for _ in range(N):
    a = b = 0
    g = 0
    while a < 4 and b < 4:
        g += 1
        if random.random() < 0.5:
            a += 1
        else:
            b += 1
    tot += g
results["series-length"] = (tot / N, 5.8125)

print(f"{'problem':<16}{'simulated':>12}{'expected':>12}  verdict")
ok = True
for k, (sim, exp) in results.items():
    good = abs(sim - exp) / max(abs(exp), 1e-9) < 0.02
    ok &= good
    print(f"{k:<16}{sim:>12.4f}{exp:>12.4f}  {'PASS' if good else 'FAIL'}")
print("ALL PASS" if ok else "SOME FAILED")
