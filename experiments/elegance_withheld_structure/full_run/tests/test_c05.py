import importlib.util, pathlib, sys, unittest, copy
SOLUTION = pathlib.Path(sys.argv.pop()).resolve()
spec = importlib.util.spec_from_file_location("solution", SOLUTION)
solution = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = solution
spec.loader.exec_module(solution)

from dataclasses import FrozenInstanceError
class Spy:
    def __init__(self): self.calls=[]
    def render(self,message):
        self.calls.append(message)
        return f"{message['name']}|{message['secret']}"
class PresentationTests(unittest.TestCase):
    def test_combination_order_redaction_and_immutability(self):
        caps=[{"kind":"legal","text":"Terms B"},{"kind":"subject","text":"Z"},{"kind":"redact","fields":["secret"]},{"kind":"metadata","key":"trace","value":3},{"kind":"subject","text":"A"},{"kind":"legal","text":"Terms A"},{"kind":"redact","fields":["secret"]},{"kind":"subject","text":"A"}]
        msg={"name":"N","secret":"raw"}; original=copy.deepcopy(msg); spy=Spy()
        a=solution.present(msg,spy,caps); b=solution.present(msg,Spy(),list(reversed(caps)))
        self.assertEqual(a,b)
        self.assertEqual(a.text,"Subject: A\nSubject: Z\nN|[REDACTED]\nTerms A\nTerms B")
        self.assertEqual(a.metadata,{"trace":3}); self.assertEqual(msg,original)
        self.assertEqual(len(spy.calls),1); self.assertIsNot(spy.calls[0],msg)
        with self.assertRaises((FrozenInstanceError,AttributeError)): a.text="x"
    def test_custom_renderer_duplicates_and_prevalidation(self):
        class Custom:
            def __init__(self): self.n=0
            def render(self,m): self.n+=1; return "custom:"+str(sorted(m))
        c=Custom(); r=solution.present({"x":1},c,[{"kind":"metadata","key":"a","value":1},{"kind":"metadata","key":"a","value":1}])
        self.assertEqual(c.n,1); self.assertEqual(r.metadata,{"a":1})
        for bad in ([{"kind":"unknown"}],[{"kind":"metadata","key":"a","value":1},{"kind":"metadata","key":"a","value":2}],[{"kind":"redact","fields":"x"}]):
            c=Custom()
            with self.assertRaises(ValueError): solution.present({"x":1},c,bad)
            self.assertEqual(c.n,0)
if __name__ == "__main__": unittest.main()
