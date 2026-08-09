import copy,importlib.util,pathlib,sys
p=pathlib.Path(sys.argv[1]); sp=importlib.util.spec_from_file_location("candidate",p); m=importlib.util.module_from_spec(sp); sys.modules[sp.name]=m; sp.loader.exec_module(m)
o={"subtotal_cents":10001}; before=copy.deepcopy(o)
out=m.make_pricer(m.base_pricer,[m.discount(1000),m.tax(500),m.fee(7),m.audit("priced")])(o)
assert out=={"cents":9457,"trace":["priced"]} and o==before,out
# Order and floor matter.
a=m.make_pricer(m.base_pricer,[m.fee(1),m.discount(5000)])({"subtotal_cents":3})
b=m.make_pricer(m.base_pricer,[m.discount(5000),m.fee(1)])({"subtotal_cents":3})
assert a["cents"]==2 and b["cents"]==2
out3=m.make_pricer(m.base_pricer,[m.fee(2),m.fee(2),m.audit("x"),m.audit("x")])({"subtotal_cents":5}); assert out3=={"cents":7,"trace":["x"]},out3
seed={"cents":10,"trace":["seed"]}
def custom(x): return seed
out4=m.make_pricer(custom,[m.tax(1000),m.audit("q")])({}); assert out4=={"cents":11,"trace":["seed","q"]} and seed=={"cents":10,"trace":["seed"]}
try:m.make_pricer(m.base_pricer,[object()])
except (TypeError,ValueError):pass
else:raise AssertionError("bad capability accepted")
print("PASS: S03")
