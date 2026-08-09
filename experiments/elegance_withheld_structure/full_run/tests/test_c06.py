import importlib.util, pathlib, sys, unittest, copy
SOLUTION = pathlib.Path(sys.argv.pop()).resolve()
spec = importlib.util.spec_from_file_location("solution", SOLUTION)
solution = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = solution
spec.loader.exec_module(solution)

from datetime import datetime, timedelta
class Clock:
    def __init__(self): self.calls=0; self.base=datetime(2030,1,1,12)
    def __call__(self): self.calls+=1; return self.base+timedelta(minutes=self.calls)
class ExhibitTests(unittest.TestCase):
    def test_lifecycle_results_and_clock(self):
        clock=Clock(); e=solution.Exhibit("E7",clock)
        self.assertEqual((e.status,e.history),("draft",()))
        rejected=e.propose("open")
        self.assertEqual((rejected.accepted,rejected.reason,rejected.status,rejected.notices,rejected.timestamp),(False,"INVALID_FROM_DRAFT","draft",(),None))
        self.assertEqual(clock.calls,0); self.assertEqual(e.history,())
        expected=[("reserve","reserved"),("install","installed"),("open","open"),("close","closed"),("maintain","maintenance"),("open","open"),("retire","retired")]
        for action,status in expected:
            result=e.propose(action)
            self.assertTrue(result.accepted); self.assertEqual(result.reason,"ACCEPTED")
            self.assertEqual(result.status,status); self.assertEqual(result.notices,(action.upper()+":E7",)); self.assertIsNotNone(result.timestamp)
        self.assertEqual(clock.calls,len(expected)); self.assertEqual(len(e.history),len(expected)); self.assertEqual(e.status,"retired")
        before=e.history; r=e.propose("retire")
        self.assertFalse(r.accepted); self.assertEqual(r.reason,"INVALID_FROM_RETIRED"); self.assertEqual(e.history,before); self.assertEqual(clock.calls,len(expected))
    def test_unknown_is_atomic(self):
        clock=Clock(); e=solution.Exhibit("x",clock); before=(e.status,e.history)
        with self.assertRaises(ValueError): e.propose("explode")
        self.assertEqual((e.status,e.history),before); self.assertEqual(clock.calls,0)
if __name__ == "__main__": unittest.main()
