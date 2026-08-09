import copy,importlib.util,pathlib,sys
p=pathlib.Path(sys.argv[1]); sp=importlib.util.spec_from_file_location("candidate",p); m=importlib.util.module_from_spec(sp); sys.modules[sp.name]=m; sp.loader.exec_module(m)
r={"value":"alpha"}; before=copy.deepcopy(r)
out=m.make_processor(m.base_processor,[m.prefix("<"),m.replace("alpha","A"),m.suffix(">"),m.audit("done")])(r)
assert out=={"value":"<A>","trace":["done"]} and r==before,out
out2=m.make_processor(m.base_processor,[m.replace("a","x"),m.prefix("a")])({"value":"a"}); assert out2["value"]=="ax",out2
out3=m.make_processor(m.base_processor,[m.suffix("!"),m.suffix("!"),m.audit("x"),m.audit("x")])({"value":"v"}); assert out3=={"value":"v!","trace":["x"]},out3
seed={"value":"Z","trace":["seed"]}
def custom(x): return seed
out4=m.make_processor(custom,[m.prefix("P"),m.audit("q")])({}); assert out4=={"value":"PZ","trace":["seed","q"]} and seed=={"value":"Z","trace":["seed"]}
try:m.make_processor(m.base_processor,[object()])
except (TypeError,ValueError):pass
else:raise AssertionError("bad capability accepted")
print("PASS: S02")
