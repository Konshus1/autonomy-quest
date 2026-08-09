import copy, importlib.util, json, os, subprocess, sys
from pathlib import Path

solution = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("candidate", solution)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sections = [
 {"title":"Overview <unsafe>", "items":[
   {"kind":"paragraph", "text":"Public <unsafe> & useful"},
   {"kind":"measurement", "label":"Depth", "value":12.5, "unit":"m"},
   {"kind":"reference", "source":"s", "text":"Survey"},
   {"kind":"warning", "text":"Restricted <warning>", "clearance":"admin"}]},
 {"title":"Restricted only", "items":[
   {"kind":"reference", "source":"s", "text":"Same source", "clearance":"admin"},
   {"kind":"image", "path":"x&y.png", "alt":"scan <one>", "clearance":"admin"}]}
]
sources={"s":{"title":"Source & One", "url":"https://e/x?a=1&b=2"}}
original=copy.deepcopy((sections,sources)); report=m.Report(sections,sources)
public_expected="""## Overview <unsafe>
Public <unsafe> & useful
Depth: 12.5 m
Survey [1]

References:
[1] Source & One — https://e/x?a=1&b=2"""
admin_expected="""## Overview <unsafe>
Public <unsafe> & useful
Depth: 12.5 m
Survey [1]
WARNING: Restricted <warning>

## Restricted only
Same source [1]
[Image: scan <one>] (x&y.png)

References:
[1] Source & One — https://e/x?a=1&b=2"""
assert m.render(report,"text","public") == public_expected
assert m.render(report,"text","admin") == admin_expected
h=m.render(report,"html","admin")
assert "&lt;unsafe&gt;" in h and "&lt;warning&gt;" in h and "scan &lt;one&gt;" in h
assert 'src="x&amp;y.png"' in h and 'href="https://e/x?a=1&amp;b=2"' in h
assert h.count(">[1]</a>") == 2 and '<ol class="references">' in h
j=json.loads(m.render(report,"json","public"))
assert [s["title"] for s in j["sections"]] == ["Overview <unsafe>"]
measurement=next(i for i in j["sections"][0]["items"] if i["kind"]=="measurement")
assert measurement["value"] == 12.5 and measurement["unit"] == "m"
assert j["references"] == [{"id":"s","number":1,"title":"Source & One","url":"https://e/x?a=1&b=2"}]
broken=m.Report([{"title":"X","items":[{"kind":"reference","source":"z","text":"bad"},{"kind":"reference","source":"a","text":"bad"}]}],{})
assert m.validate(broken)==["missing source: a","missing source: z"]
assert (sections,sources)==original
for bad in [("xml","public"),("text","staff")]:
 try: m.render(report,*bad)
 except ValueError: pass
 else: raise AssertionError("unknown render choice must fail")
code=r"""import importlib.util,sys
p=sys.argv[1]; s=importlib.util.spec_from_file_location('x',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
r=m.Report([{'title':'T','items':[{'kind':'reference','source':'b','text':'B'},{'kind':'reference','source':'a','text':'A'},{'kind':'reference','source':'b','text':'B2'}]}],{'a':{'title':'A','url':'uA'},'b':{'title':'B','url':'uB'}})
print(m.render(r,'text','public'),end='')"""
outs=[]
for seed in ("1","987"):
 env=dict(os.environ, PYTHONHASHSEED=seed)
 outs.append(subprocess.check_output([sys.executable,"-c",code,str(solution)],env=env,text=True))
assert outs[0]==outs[1] and "B [1]" in outs[0] and "A [2]" in outs[0] and "B2 [1]" in outs[0]
print("ok")
