import copy, importlib.util, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location("candidate",sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
defaults={"app":{"port":10,"debug":True,"old":"keep","names":["a"],"secret":"alpha","path":"/default"}}
project={"app":{"port":20,"old":"__DELETE__","names":["b","c"],"extra":1}}
user={"app":{"debug":"OFF","secret":"bravo"}}
environ={"app.port":"oops","app.path":"tmp/data","app.csv":"x, y,z","ghost":"boo"}
explicit={"app":{"port":30}}
schema={"app.port":"int","app.debug":"bool","app.old":"str","app.names":"list","app.secret":"str","app.path":"path","app.csv":"csv"}
args=(defaults,project,user,environ,explicit,schema); saved=copy.deepcopy(args)
r=m.load(*args,secrets=("app.secret",))
assert r.data=={"app":{"port":30,"debug":False,"names":["b","c"],"secret":"bravo","path":Path("tmp/data"),"csv":["x","y","z"]}}
assert r.get("app.port")==30 and r.get("app.old")==None and r.get("missing",7)==7
assert r.errors==["invalid app.port from environ: expected int","unknown key app.extra","unknown key ghost"]
assert r.explain("app.port")==[
 {"source":"defaults","status":"overridden","value":10},
 {"source":"project","status":"overridden","value":20},
 {"source":"environ","status":"invalid","value":"oops"},
 {"source":"explicit","status":"selected","value":30}]
assert r.explain("app.old")==[
 {"source":"defaults","status":"overridden","value":"keep"},
 {"source":"project","status":"removed","value":"__DELETE__"}]
secret=r.explain("app.secret")
assert secret==[{"source":"defaults","status":"overridden","value":"***"},{"source":"user","status":"selected","value":"***"}]
assert r.explain("app.never")==[]
assert args==saved
# An invalid highest-precedence candidate leaves the prior valid value selected.
r2=m.load({"n":4},{},None,{}, {"n":"bad"},{"n":"int"})
assert r2.get("n")==4 and r2.explain("n")==[{"source":"defaults","status":"selected","value":4},{"source":"explicit","status":"invalid","value":"bad"}]
print("ok")
