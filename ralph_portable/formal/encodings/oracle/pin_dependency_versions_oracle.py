#!/usr/bin/env python3
"""REAL clingo-backed oracle for the pin_dependency_versions -> build_reproducible encoding.

Replaces the string-marker stub with a SEMANTIC proof: clingo actually solves the program
under three fact/constraint configurations and cross-checks SAT/UNSAT. One JSON line on
stdout, exit 0; GREEN iff all three checks match the causal-guarantee expectations.

Trust model: the oracle is untrusted content on the VERDICT channel (oracle_harness docs).
The harness sandboxes it (network denied + filesystem writes confined to a throwaway
workdir where the platform supports it; rlimit-only elsewhere) and treats anything other
than a clean deterministic GREEN as ERROR. This oracle is deliberately idempotent (the
harness runs it twice for a determinism check) and WRITE-FREE: the extra fact/constraint
programs are piped to clingo via STDIN (`clingo payload -`), so the oracle never touches
the filesystem outside reading the payload path it was given. This keeps it correct under
any sandbox profile (seatbelt/bwrap/rlimit-only) -- it does not depend on workdir-write
being allowed, which the harness's seatbelt profile does not in fact permit.

The causal guarantee under proof (three semantic conditions):
  (a) REALIZABLE  : payload + action(pin_dependency_versions)            is SAT
                     -- the scenario where the action is taken is consistent
  (b) ENTAILMENT  : payload + action + :- holds(build_reproducible)      is UNSAT
                     -- under the action, reproducibility is FORCED in every world
  (c) NON-VACUOUS : payload + :- holds(build_reproducible) (no action)   is SAT
                     -- WITHOUT the action, reproducibility is avoidable, so the guarantee
                        is earned by the action rather than baked into the model

A tampered or weakened encoding (e.g. the action rule dropped, the integrity constraint
removed, or build_reproducible forced unconditionally) fails exactly one of (a)/(b)/(c),
which is what makes the oracle actually discriminate. clingo is deterministic, so the
harness's rerun agrees unless the encoding is nondeterministic -- which would be a defect.

clingo exit codes are a bit-field: 10=SAT, 20=UNSAT, 30=SAT-under-enumeration. We trust the
VERDICT LINE clingo prints ("SATISFIABLE"/"UNSATISFIABLE") and require the exit code to be
one of the known-good values; any other exit with a verdict line is ERROR (a hostile/broken
clingo that prints "SATISFIABLE" while exiting 99 is not a proof). Note "UNSATISFIABLE"
contains the substring "SATISFIABLE", so the SAT check explicitly excludes the UNSAT case.
"""
import json
import os
import subprocess
import sys

# clingo is invoked with an absolute path because the harness scrubs PATH to /usr/bin:/bin
# (a hostile/buggy oracle must not reach the network or an arbitrary interpreter). We try a
# fixed candidate list of well-known install locations; on CI/Linux /usr/bin/clingo wins, on
# macOS Homebrew /opt/homebrew/bin/clingo wins. If none exists, we exit nonzero -> ERROR.
_CLINGO_CANDIDATES = (
    "/usr/bin/clingo",
    "/usr/local/bin/clingo",
    "/opt/homebrew/bin/clingo",
)


def _clingo() -> str:
    for c in _CLINGO_CANDIDATES:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return ""


def _sat_unsat(payload: str, check_text: str) -> str:
    """Run `clingo payload -` with check_text on stdin; return 'SAT' | 'UNSAT' | 'ERROR'.

    Piping the extra fact/constraint program via stdin keeps the oracle write-free (correct
    under any sandbox). Parse the verdict line and cross-check the exit code; any ambiguity,
    timeout, or non-known exit -> ERROR so the harness records nothing (uncertainty is never
    a proof).
    """
    args = [_clingo(), payload, "-", "-n", "0", "--stats=0"]
    try:
        r = subprocess.run(args, input=check_text, capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired:
        return "ERROR"
    except Exception:
        return "ERROR"
    text = (r.stdout or "") + (r.stderr or "")
    stdout_sat = "SATISFIABLE" in text and "UNSATISFIABLE" not in text
    stdout_unsat = "UNSATISFIABLE" in text and not stdout_sat
    rc = r.returncode
    rc_ok = rc in (10, 20, 30)
    if stdout_sat and rc_ok:
        return "SAT"
    if stdout_unsat and rc_ok:
        return "UNSAT"
    return "ERROR"


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"result": "RED", "reason": "oracle needs exactly one payload arg"}))
        return 0
    payload = sys.argv[1]
    if not os.path.isfile(payload):
        print(json.dumps({"result": "RED", "reason": "payload not found"}))
        return 0
    clingo = _clingo()
    if not clingo:
        # internal failure: cannot prove -> must surface as ERROR, not RED. Exit nonzero so
        # the harness (which treats nonzero exit as ERROR) records nothing.
        sys.stderr.write("clingo binary not found\n")
        return 3

    action_fact = "action(pin_dependency_versions).\n"
    forbid_rep = ":- holds(build_reproducible).\n"

    realizable = _sat_unsat(payload, action_fact)
    entailment = _sat_unsat(payload, action_fact + forbid_rep)
    non_vacuous = _sat_unsat(payload, forbid_rep)

    if realizable == "SAT" and entailment == "UNSAT" and non_vacuous == "SAT":
        print(json.dumps({"result": "GREEN",
                          "reason": "clingo: action realizable + forces reproducible + non-vacuous"}))
        return 0
    print(json.dumps({"result": "RED",
                      "reason": f"realizable={realizable} entailment={entailment} non_vacuous={non_vacuous}"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
