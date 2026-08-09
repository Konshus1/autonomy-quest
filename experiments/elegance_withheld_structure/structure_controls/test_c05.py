#!/usr/bin/env python3
import copy, importlib.util, pathlib, sys
p=pathlib.Path(sys.argv[1]); spec=importlib.util.spec_from_file_location("candidate",p); m=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m)
msg={"subject":"Q4 secret", "body":"Account alpha owes alpha", "account":"alpha"}; before=copy.deepcopy(msg)
r=m.make_renderer(m.base_renderer,[m.subject(),m.redact(["account"]),m.legal("Terms"),m.record("legal-added")])
out=r(msg)
assert out=={"text":"Subject: Q4 secret\nAccount [REDACTED] owes [REDACTED]\nTerms","metadata":["legal-added"]},out
assert msg==before
# order matters in the documented way: redaction before subject does not alter later subject.
msg2={"subject":"alpha","body":"alpha","account":"alpha"}
out2=m.make_renderer(m.base_renderer,[m.redact(["account"]),m.subject()])(msg2)
assert out2["text"]=="Subject: alpha\n[REDACTED]",out2
# Equivalent duplicates are ignored, including separately constructed values.
out3=m.make_renderer(m.base_renderer,[m.legal("L"),m.legal("L"),m.record("x"),m.record("x")])({"subject":"s","body":"b"})
assert out3=={"text":"b\nL","metadata":["x"]},out3
# Existing capabilities work with a user-supplied renderer and do not mutate its returned object.
seed={"text":"CUSTOM alpha","metadata":["seed"]}
def custom(message): return seed
out4=m.make_renderer(custom,[m.redact(["account"]),m.record("r")])({"account":"alpha"})
assert out4=={"text":"CUSTOM [REDACTED]","metadata":["seed","r"]},out4
assert seed=={"text":"CUSTOM alpha","metadata":["seed"]}
# Unknown/malformed capability is rejected during construction.
try: m.make_renderer(m.base_renderer,[object()])
except (TypeError,ValueError): pass
else: raise AssertionError("malformed capability accepted")
print("PASS: C05 pilot correctness")
