"""Reusable import firewall (#615 regression guard).

Generalizes the one-off `test_loop_cannot_reach_formal_actuation` check into a reusable assertion:
a set of HOT-PATH modules (the autonomous mission loop) must never reach a set of FORBIDDEN
actuation surfaces — by import OR by name reference. It's the mirror of the causal-autonomy
invariant: the running loop can consult/propose, but never ACTUATE (promote a formal principle,
execute a replication, broker a fleet). Actuation stays operator/CI-only, off the hot path.

Static + AST-based (no execution), so it's safe to run in CI on every change. It catches the common
regressions (a new `import ralph_portable.formal`, a stray `store.apply_promotion(...)` call added to
the loop). It is NOT a full data-flow proof — a determined author routing through `getattr`/reflection
could evade it — but combined with the store methods' own operator/CI-only guards it makes an
accidental hot-path actuation loud and CI-visible.
"""
from __future__ import annotations

import ast
import pathlib


def forbidden_references(files: list[str | pathlib.Path], *, import_prefixes: tuple[str, ...],
                         names: tuple[str, ...], repo_root: str | pathlib.Path) -> list[str]:
    """Return a list of violations (empty == clean). A violation is either:
      - a hot-path file IMPORTS a module whose dotted path starts with a forbidden prefix, or
      - a hot-path file textually REFERENCES a forbidden actuation name.
    """
    root = pathlib.Path(repo_root)
    violations: list[str] = []
    for rel in files:
        path = root / rel
        text = path.read_text()
        # 1) AST imports (import X / from X import ...)
        for node in ast.walk(ast.parse(text)):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for m in mods:
                if any(m.startswith(p) for p in import_prefixes):
                    violations.append(f"{rel} imports forbidden module {m!r}")
        # 2) textual actuation-name references (a call/attr slipped in)
        for token in names:
            if token in text:
                violations.append(f"{rel} references forbidden actuation name {token!r}")
        # 3) forbidden import PATH as a literal string — catches dynamic imports that dodge the AST
        #    channel, e.g. importlib.import_module("ralph_portable.formal.oracle_harness"). A hot-path
        #    file containing that literal is suspicious in any context; only deliberate string-splitting
        #    evades now (documented limitation — the guard is a tripwire for accidents, not an adversary).
        for prefix in import_prefixes:
            if prefix in text:
                violations.append(f"{rel} references forbidden import path {prefix!r} as text")
    return violations
