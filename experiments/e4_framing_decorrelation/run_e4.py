"""E4 runner. See PREREGISTRATION.md (committed before this ran).
Usage: uv run --with anthropic python run_e4.py
Writes raw_results.json (every call's full text) next to this file."""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import anthropic

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = "claude-haiku-4-5"
TEMP = 1.0
SOLVE_TOKENS = 1400
RECON_TOKENS = 1600

client = anthropic.Anthropic(max_retries=6)
WORKERS = 2

PROBLEMS = json.load(open(os.path.join(HERE, "problems.json")))["problems"]

GENERIC = (
    "Solve this problem with careful step-by-step reasoning. Work through it "
    "methodically, check the assumptions you make along the way, verify your "
    "calculation before committing to it, and make sure your final answer "
    "actually follows from the reasoning you wrote."
)
FRAMINGS = {
    "F1-enumerate": (
        "Solve this problem by modeling the full sample space explicitly. "
        "Enumerate the elementary outcomes or states consistent with all the "
        "stated conditions, assign each its probability, and compute the answer "
        "by direct counting and summation over that enumeration."
    ),
    "F2-frequentist": (
        "Solve this problem by imagining it run as a physical experiment 10000 "
        "times. Track what happens across the repetitions, being very careful "
        "about which repetitions actually get counted or observed under the "
        "stated conditions, then estimate the answer from those frequencies."
    ),
    "F3-algebraic": (
        "Solve this problem by defining random variables and events precisely "
        "in formal notation. Write the target quantity as a formal expression "
        "(a conditional probability or expectation) and manipulate it "
        "symbolically using the laws of probability until it is a number."
    ),
}
ANSWER_FMT = (
    "\n\nEnd your response with a line of exactly this form:\nFINAL: <number>"
    "\nwhere <number> is a plain decimal number (no fractions, no units)."
)


def call(prompt, max_tokens):
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        temperature=TEMP,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if b.type == "text"), resp.usage


def extract(text):
    m = re.findall(r"FINAL:\s*\$?(-?\d+(?:\.\d+)?(?:e-?\d+)?)", text)
    return float(m[-1]) if m else None


def correct(ans, truth):
    return ans is not None and abs(ans - truth) <= max(0.005, 0.01 * abs(truth))


def solve_job(job):
    arm, pid, slot, framing_name, framing_text, ptext = job
    prompt = f"{framing_text}\n\nProblem: {ptext}{ANSWER_FMT}"
    text, usage = call(prompt, SOLVE_TOKENS)
    return {
        "arm": arm, "problem": pid, "slot": slot, "framing": framing_name,
        "text": text, "answer": extract(text),
        "in_tok": usage.input_tokens, "out_tok": usage.output_tokens,
    }


def main():
    jobs = []
    for p in PROBLEMS:
        for slot in range(3):
            jobs.append(("A", p["id"], slot, "generic", GENERIC, p["text"]))
        for slot, (fname, ftext) in enumerate(FRAMINGS.items()):
            jobs.append(("B", p["id"], slot, fname, ftext, p["text"]))

    print(f"solve calls: {len(jobs)}")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        solves = list(ex.map(solve_job, jobs))

    # reconciliation
    by_key = {}
    for s in solves:
        by_key.setdefault((s["arm"], s["problem"]), []).append(s)

    def recon_job(item):
        (arm, pid), attempts = item
        attempts = sorted(attempts, key=lambda s: s["slot"])
        ptext = next(p["text"] for p in PROBLEMS if p["id"] == pid)
        block = "\n\n".join(
            f"--- Attempt {i+1} ---\n{a['text']}" for i, a in enumerate(attempts)
        )
        prompt = (
            f"Problem: {ptext}\n\nThree independent attempts at this problem are "
            f"shown below.\n\n{block}\n\nYour task: identify any disagreements "
            "between the attempts, in their final answers or in load-bearing "
            "reasoning steps. Explain WHY they disagree. Decide which reasoning "
            "is actually correct, then give your own final answer."
            + ANSWER_FMT
        )
        text, usage = call(prompt, RECON_TOKENS)
        return {
            "arm": arm, "problem": pid, "type": "reconciliation",
            "text": text, "answer": extract(text),
            "in_tok": usage.input_tokens, "out_tok": usage.output_tokens,
        }

    print(f"reconciliation calls: {len(by_key)}")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        recons = list(ex.map(recon_job, by_key.items()))

    out = {"model": MODEL, "temperature": TEMP, "solves": solves, "recons": recons}
    with open(os.path.join(HERE, "raw_results.json"), "w") as f:
        json.dump(out, f, indent=1)

    tot_in = sum(x["in_tok"] for x in solves + recons)
    tot_out = sum(x["out_tok"] for x in solves + recons)
    cost = tot_in / 1e6 * 1.0 + tot_out / 1e6 * 5.0
    print(f"tokens in={tot_in} out={tot_out}  est cost=${cost:.2f}")

    # quick scoring
    truths = {p["id"]: p["truth"] for p in PROBLEMS}
    for arm in "AB":
        fin = [r for r in recons if r["arm"] == arm]
        acc = sum(correct(r["answer"], truths[r["problem"]]) for r in fin)
        print(f"Arm {arm} final accuracy: {acc}/12")


if __name__ == "__main__":
    sys.exit(main())
