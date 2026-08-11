"""Immutable trusted verifier policy plus fail-closed exact-test closure helpers."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence
PASS="PASS"; HOLD="HOLD"
_SHA40=re.compile(r"^[0-9a-f]{40}$"); _SHA64=re.compile(r"^[0-9a-f]{64}$")
class PolicyError(ValueError): pass

def _strict(pairs):
    out={}
    for k,v in pairs:
        if k in out: raise PolicyError(f"duplicate JSON key: {k}")
        out[k]=v
    return out

def canonical_json(value: Any)->bytes:
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()

@dataclass(frozen=True, slots=True)
class CheckSpec:
    id:str; artifact:str; artifact_sha256:str; argv:tuple[str,...]; timeout_seconds:int
    def __post_init__(self):
        if not isinstance(self.id,str) or not self.id.strip(): raise PolicyError("invalid check id")
        art=self.artifact; pure=PurePosixPath(art) if isinstance(art,str) else PurePosixPath("..")
        if not art or pure.is_absolute() or any(part in {".",".."} for part in pure.parts): raise PolicyError("trusted artifact escapes root")
        if not isinstance(self.artifact_sha256,str) or not _SHA64.fullmatch(self.artifact_sha256): raise PolicyError("invalid artifact digest")
        expected=f"/opt/aq-trusted/checks/{art}"
        if not isinstance(self.argv,tuple) or self.argv[:3] != ("/usr/local/bin/python","-I",expected): raise PolicyError("argv must execute immutable trusted artifact")
        if any(arg == "/candidate" or arg.startswith("/candidate/") for arg in self.argv): raise PolicyError("candidate cannot select verifier artifacts")
        if not isinstance(self.timeout_seconds,int) or isinstance(self.timeout_seconds,bool) or not 1<=self.timeout_seconds<=900: raise PolicyError("invalid timeout")
    @classmethod
    def parse(cls,raw):
        keys={"id","artifact","artifact_sha256","argv","timeout_seconds"}
        if not isinstance(raw,dict) or set(raw)!=keys: raise PolicyError("check keys mismatch")
        if not isinstance(raw["id"],str) or not raw["id"]: raise PolicyError("invalid check id")
        art=raw["artifact"]; pure=PurePosixPath(art) if isinstance(art,str) else PurePosixPath("..")
        if not art or pure.is_absolute() or ".." in pure.parts: raise PolicyError("trusted artifact escapes root")
        if not isinstance(raw["artifact_sha256"],str) or not _SHA64.fullmatch(raw["artifact_sha256"]): raise PolicyError("invalid artifact digest")
        argv=raw["argv"]
        expected=f"/opt/aq-trusted/checks/{art}"
        if not isinstance(argv,list) or tuple(argv[:3]) != ("/usr/local/bin/python","-I",expected): raise PolicyError("argv must execute immutable trusted artifact")
        timeout=raw["timeout_seconds"]
        if not isinstance(timeout,int) or isinstance(timeout,bool) or not 1<=timeout<=900: raise PolicyError("invalid timeout")
        return cls(raw["id"],art,raw["artifact_sha256"],tuple(argv),timeout)
    def mapping(self): return {"id":self.id,"artifact":self.artifact,"artifact_sha256":self.artifact_sha256,"argv":list(self.argv),"timeout_seconds":self.timeout_seconds}

@dataclass(frozen=True, slots=True)
class VerifierPolicy:
    version:int; base_sha:str; candidate_sha:str; checks:tuple[CheckSpec,...]; digest:str
    def __post_init__(self):
        if self.version != 1: raise PolicyError("unsupported policy version")
        if not _SHA40.fullmatch(self.base_sha) or not _SHA40.fullmatch(self.candidate_sha): raise PolicyError("invalid SHA binding")
        if not isinstance(self.checks,tuple) or not self.checks or not all(isinstance(x,CheckSpec) for x in self.checks): raise PolicyError("required checks must be a non-empty immutable tuple")
        if len({x.id for x in self.checks}) != len(self.checks): raise PolicyError("duplicate required check IDs")
        expected=hashlib.sha256(canonical_json({"version":self.version,"base_sha":self.base_sha,"candidate_sha":self.candidate_sha,"checks":[x.mapping() for x in self.checks]})).hexdigest()
        if self.digest != expected: raise PolicyError("manifest digest does not match immutable manifest")
    @classmethod
    def load(cls,path,trusted_checks_root=None,*,candidate_root=None):
        source=Path(path).expanduser().resolve()
        if candidate_root is not None:
            candidate=Path(candidate_root).expanduser().resolve()
            if source == candidate or candidate in source.parents: raise PolicyError("policy must be external to candidate checkout")
        try: raw=json.loads(source.read_text(),object_pairs_hook=_strict)
        except PolicyError: raise
        except Exception as e: raise PolicyError(f"policy unreadable: {e}") from e
        if not isinstance(raw,dict) or set(raw)!={"version","base_sha","candidate_sha","checks"}: raise PolicyError("policy keys mismatch")
        if raw["version"]!=1: raise PolicyError("unsupported policy version")
        if not _SHA40.fullmatch(str(raw["base_sha"])) or not _SHA40.fullmatch(str(raw["candidate_sha"])): raise PolicyError("invalid SHA binding")
        if not isinstance(raw["checks"],list) or not raw["checks"]: raise PolicyError("required check set is empty")
        checks=tuple(CheckSpec.parse(x) for x in raw["checks"])
        if len({x.id for x in checks})!=len(checks): raise PolicyError("duplicate required check IDs")
        obj=cls(1,raw["base_sha"],raw["candidate_sha"],checks,hashlib.sha256(canonical_json(raw)).hexdigest())
        if trusted_checks_root is not None: obj.verify_artifacts(trusted_checks_root)
        return obj
    def verify_artifacts(self,root):
        root=Path(root).resolve(strict=True)
        for c in self.checks:
            p=(root/c.artifact)
            if p.is_symlink(): raise PolicyError("trusted artifact must not be symlink")
            try: resolved=p.resolve(strict=True)
            except OSError as e: raise PolicyError("trusted artifact missing") from e
            if root not in resolved.parents or not resolved.is_file(): raise PolicyError("trusted artifact escapes root")
            if hashlib.sha256(resolved.read_bytes()).hexdigest()!=c.artifact_sha256: raise PolicyError("trusted artifact digest mismatch")
    @property
    def required_ids(self): return tuple(x.id for x in self.checks)

@dataclass(frozen=True)
class TestObservation: test_id:str; outcome:str
@dataclass(frozen=True)
class PolicyDecision:
    decision:str; reason_codes:tuple[str,...]; tests:tuple[TestObservation,...]=()
    @property
    def passed(self): return self.decision==PASS

def hold(*codes,tests=()): return PolicyDecision(HOLD,tuple(dict.fromkeys(codes)) or ("verification_incomplete",),tuple(tests))
def evaluate_required_tests(required_test_ids:Sequence[str],observations:Iterable[TestObservation|Mapping[str,object]],*,exit_code:int,collected_count:int|None,malformed:bool=False):
    required=tuple(required_test_ids)
    if not required:return hold("manifest_empty")
    if len(set(required))!=len(required):return hold("manifest_malformed")
    parsed=[]
    for x in observations:
        try:
            o=x if isinstance(x,TestObservation) else TestObservation(str(x["id"]),str(x["outcome"]))
            if not o.test_id: raise ValueError
            parsed.append(o)
        except Exception: malformed=True
    reasons=[]
    if malformed or collected_count is None or collected_count<0:reasons.append("result_malformed")
    if collected_count==0:reasons.append("zero_tests_collected")
    for rid in required:
        found=[x for x in parsed if x.test_id==rid]
        if not found: reasons.append("required_test_missing")
        elif len(found)!=1: reasons.append("result_malformed")
        elif found[0].outcome=="skipped": reasons.append("required_test_skipped")
        elif found[0].outcome=="failed": reasons.append("required_test_failed")
        elif found[0].outcome!="passed": reasons.append("result_malformed")
    if exit_code!=0: reasons.append("verifier_nonzero_exit")
    return hold(*reasons,tests=parsed) if reasons else PolicyDecision(PASS,(),tuple(parsed))
