"""Executor registry (FORMAL_LAYER_SPEC §2) — the formal encodings a peer supplies are DATA, not
inline code: a content-addressed, digest-pinned registry. An edge's executor `ref` is a registry
KEY, never code. Nothing is ever run unless its sha256 matches the pinned digest in the manifest —
a mismatch is a HARD ERROR and nothing runs (closes the "swap the payload after review" hole).

Trust root v1 = git: the manifest + payloads are committed to the repo, so review-of-diff is the
provenance. A detached signature over the manifest can be layered on later without changing callers.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

ENCODING_KINDS = ("constraint", "script")  # constraint = ASP, script = PDDL
_REQUIRED_FIELDS = ("key", "kind", "lang", "payload", "digest", "oracle", "oracle_digest", "binds")


class RegistryError(Exception):
    """Any registry integrity failure. Fail-closed: the caller must treat this as 'do not run'."""


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutorRegistry:
    def __init__(self, root: pathlib.Path, assets: dict[str, dict[str, Any]]) -> None:
        self.root = root
        self._assets = assets

    @classmethod
    def load(cls, manifest_path: str | pathlib.Path) -> "ExecutorRegistry":
        manifest_path = pathlib.Path(manifest_path)
        if not manifest_path.exists():
            raise RegistryError(f"manifest not found: {manifest_path}")
        root = manifest_path.parent
        try:
            data = json.loads(manifest_path.read_text())
        except json.JSONDecodeError as e:
            raise RegistryError(f"manifest is not valid JSON: {e}") from e
        assets: dict[str, dict[str, Any]] = {}
        for a in data.get("assets", []):
            for f in _REQUIRED_FIELDS:
                if f not in a:
                    raise RegistryError(f"asset {a.get('key','?')!r} missing required field {f!r}")
            key = a["key"]
            if key in assets:
                raise RegistryError(f"duplicate registry key {key!r}")
            if a["kind"] not in ENCODING_KINDS:
                raise RegistryError(f"asset {key!r} kind must be one of {ENCODING_KINDS}")
            if not isinstance(a["binds"], dict) or "cause" not in a["binds"] or "effect" not in a["binds"]:
                raise RegistryError(f"asset {key!r} binds must name a canonical cause + effect")
            assets[key] = a
        return cls(root, assets)

    def keys(self) -> list[str]:
        return sorted(self._assets)

    def resolve(self, key: str) -> dict[str, Any]:
        a = self._assets.get(key)
        if a is None:
            raise RegistryError(f"no registry asset with key {key!r}")
        return a

    def _contained(self, rel: str) -> pathlib.Path:
        """Resolve `rel` under the registry root and REQUIRE it stays inside it. A manifest path
        like ``../outside_oracle.py`` (an executable outside the reviewed encodings dir) is rejected —
        the registry must be self-contained so review-of-the-encodings-dir is the whole provenance."""
        root = self.root.resolve()
        p = (root / rel).resolve()
        if root != p and root not in p.parents:
            raise RegistryError(f"path {rel!r} escapes the registry root — refusing")
        return p

    def verify_payload(self, key: str) -> dict[str, Any]:
        """Recompute sha256 of BOTH the payload and its oracle vs the pinned digests. Any mismatch,
        missing file, or path escaping the registry root is a hard error — nothing downstream runs.
        Returns the asset on success."""
        a = self.resolve(key)
        payload = self._contained(a["payload"])
        oracle = self._contained(a["oracle"])
        if not payload.exists():
            raise RegistryError(f"payload file missing for {key!r}: {a['payload']}")
        if not oracle.exists():
            raise RegistryError(f"oracle file missing for {key!r}: {a['oracle']}")
        if _sha256(payload) != a["digest"]:
            raise RegistryError(f"payload digest MISMATCH for {key!r} — refusing to run")
        if _sha256(oracle) != a["oracle_digest"]:
            raise RegistryError(f"oracle digest MISMATCH for {key!r} — refusing to run")
        return a

    def payload_path(self, key: str) -> pathlib.Path:
        return self._contained(self.resolve(key)["payload"])

    def oracle_path(self, key: str) -> pathlib.Path:
        return self._contained(self.resolve(key)["oracle"])

    def binds(self, key: str) -> dict[str, str]:
        return dict(self.resolve(key)["binds"])
