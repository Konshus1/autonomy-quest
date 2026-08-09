import copy, importlib.util, itertools, sys
from datetime import datetime
spec=importlib.util.spec_from_file_location("candidate",sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
reqs=[
 {"id":"badge","type":"badge","allowed":["staff"]},
 {"id":"training","type":"training","name":"lab"},
 {"id":"escort","type":"escort"},
 {"id":"hours","type":"window","weekdays":[0],"start":"22:00","end":"02:00"},
 {"id":"closed","type":"closure","start":datetime(2025,1,7,0),"end":datetime(2025,1,7,2)},
 {"id":"emergency","type":"emergency_override","overrides":["closed","escort"]}]
svc=m.AccessService(reqs)
def request(**kw):
 d={"badge":"staff","training":{"lab":datetime(2025,2,1)},"escort":True,"entrance":"north","timestamp":datetime(2025,1,6,23),"activity":"work"}; d.update(kw); return d
assert svc.evaluate(request())=={"allowed":True,"explanation":[]}
# The after-midnight portion belongs to Monday and two failures are reported.
r=request(timestamp=datetime(2025,1,7,1),escort=False)
assert svc.evaluate(r)=={"allowed":False,"explanation":["closed: temporarily closed","escort: escort required"]}
assert svc.evaluate(r,emergency=True)=={"allowed":True,"explanation":["emergency: emergency override of closed,escort"]}
assert svc.affected_by_emergency(r)==["closed","escort"]
expired=request(training={"lab":datetime(2025,1,6,22,59)})
assert svc.evaluate(expired)=={"allowed":False,"explanation":["training: training lab expired or missing"]}
outside=request(timestamp=datetime(2025,1,7,3))
assert "hours: outside access window" in svc.evaluate(outside)["explanation"]
# Inclusive expiry remains valid.
edge=request(training={"lab":datetime(2025,1,6,23)})
assert svc.evaluate(edge)["allowed"]
# Scalar, batch, hypothetical, and no mutation.
bad=request(badge="visitor"); before=copy.deepcopy(bad)
assert svc.evaluate_batch([bad,r])==[svc.evaluate(bad),svc.evaluate(r)]
assert svc.evaluate_with(bad,{"badge":"staff"})["allowed"]
assert bad==before
# Requirement configuration order cannot matter.
base=svc.evaluate(r)
for perm in (list(reversed(reqs)), reqs[2:]+reqs[:2]):
 assert m.AccessService(perm).evaluate(r)==base
assert reqs==copy.deepcopy(reqs)
print("ok")
