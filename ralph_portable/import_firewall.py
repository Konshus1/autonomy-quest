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
from collections import deque


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


def _module_to_files(dotted: str, root: pathlib.Path) -> list[pathlib.Path]:
    """First-party files a dotted module name could resolve to (``x.py`` and ``x/__init__.py``)."""
    base = root / pathlib.Path(*dotted.split("."))
    out: list[pathlib.Path] = []
    module_file = base.with_suffix(".py")
    if module_file.is_file():
        out.append(module_file)
    package_init = base / "__init__.py"
    if package_init.is_file():
        out.append(package_init)
    return out


def _imported_dotted_names(rel: pathlib.Path, root: pathlib.Path) -> set[str]:
    """Every dotted module name a file imports, with relative imports resolved to absolute.

    For ``from X import Y`` both ``X`` (the module) and ``X.Y`` (Y-as-submodule) are emitted, so a
    ``from pkg import submodule`` reaches ``pkg/submodule.py`` — the resolver keeps only names that
    map to a real first-party file.
    """
    tree = ast.parse((root / rel).read_text())
    parts = rel.with_suffix("").parts
    pkg_parts = list(parts[:-1])  # the package this file lives in (drops the module/__init__ name)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:  # relative: from . / .. import ...
                trim = node.level - 1
                base_parts = pkg_parts[: len(pkg_parts) - trim] if trim <= len(pkg_parts) else []
                mod = ".".join([*base_parts, node.module]) if node.module else ".".join(base_parts)
            else:
                mod = node.module or ""
            if mod:
                names.add(mod)
            for alias in node.names:
                combined = ".".join(p for p in (mod, alias.name) if p)
                if combined:
                    names.add(combined)
    return names


def transitive_import_closure(
    entrypoints: list[str | pathlib.Path], *, repo_root: str | pathlib.Path,
) -> set[str]:
    """The TRANSITIVE set of first-party files reachable by import from ``entrypoints``.

    A breadth-first walk of the in-repo import graph: each file's imports are resolved to repo
    files (``_module_to_files``), which are then walked in turn. Only files that exist under
    ``repo_root`` are followed (third-party/stdlib imports are ignored — they cannot reach a
    host-only module). Returns POSIX-style repo-relative paths, including the entrypoints.

    This is the honest guest-reachable closure: the set of modules NORMAL guest execution can reach
    by following imports from the entrypoints. Proving the docker step is outside it shows the guest
    never CALLS it on any ordinary path (the import-reach layer of the firewall). It is not — and
    cannot be — a proof that a host module physically shipped in the guest image is un-importable by
    arbitrary code; that residual gap is closed by the independent runtime layer (no docker CLI, no
    ``/var/run/docker.sock`` in the guest), asserted in ``tests/test_import_firewall.py``.
    """
    root = pathlib.Path(repo_root)
    seen: set[str] = set()
    queue: deque[pathlib.Path] = deque()
    for ep in entrypoints:
        rel = pathlib.Path(ep)
        if (root / rel).is_file():
            queue.append(rel)
    while queue:
        rel = queue.popleft()
        key = rel.as_posix()
        if key in seen:
            continue
        seen.add(key)
        for dotted in _imported_dotted_names(rel, root):
            for target in _module_to_files(dotted, root):
                trel = target.relative_to(root)
                if trel.as_posix() not in seen:
                    queue.append(trel)
    return seen
