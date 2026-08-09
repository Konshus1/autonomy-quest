import importlib.util, pathlib, sys, unittest, copy
SOLUTION = pathlib.Path(sys.argv.pop()).resolve()
spec = importlib.util.spec_from_file_location("solution", SOLUTION)
solution = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = solution
spec.loader.exec_module(solution)

class Source:
    def __init__(self,a): self.a=a; self.calls=0
    def read_action(self): self.calls+=1; return self.a
class Sink:
    def __init__(self): self.screens=[]
    def display(self,s): self.screens.append(s)
class ConsoleTests(unittest.TestCase):
    def apps(self): return [{"id":"a2","label":"Afternoon"},{"id":"a1","label":"Morning"}]
    def test_session_flow_correction_and_errors(self):
        c=solution.CheckInConsole(self.apps())
        initial={"active":False,"contact_confirmed":None,"appointment_id":None,"completed":False}
        self.assertEqual(c.session,initial)
        bad=c.handle({"type":"choose_appointment","appointment_id":"a1"}); self.assertTrue(bad["messages"][0].startswith("Error:")); self.assertEqual(c.session,initial)
        self.assertEqual(c.handle({"type":"begin"}),{"prompt":"Confirm contact","messages":[],"choices":[]})
        s=c.handle({"type":"confirm_contact","confirmed":True})
        self.assertEqual(s,{"prompt":"Choose appointment","messages":[],"choices":self.apps()})
        before=copy.deepcopy(c.session); err=c.handle({"type":"choose_appointment","appointment_id":"nope"})
        self.assertTrue(err["messages"][0].startswith("Error:")); self.assertEqual(c.session,before)
        self.assertEqual(c.handle({"type":"choose_appointment","appointment_id":"a1"})["prompt"],"Review and complete")
        self.assertEqual(c.handle({"type":"correct","field":"appointment_id","value":None})["prompt"],"Choose appointment")
        c.handle({"type":"choose_appointment","appointment_id":"a2"})
        done=c.handle({"type":"complete"}); self.assertEqual(done,{"prompt":"Check-in complete","messages":["Checked in"],"choices":[]})
        self.assertEqual(c.session,{"active":False,"contact_confirmed":True,"appointment_id":"a2","completed":True})
    def test_abandon_malformed_and_io(self):
        src=Source({"type":"begin"}); sink=Sink(); c=solution.CheckInConsole(self.apps(),src,sink)
        screen=c.run_once(); self.assertIs(screen,sink.screens[0]); self.assertEqual((src.calls,len(sink.screens)),(1,1))
        before=copy.deepcopy(c.session); malformed=c.handle({"type":"confirm_contact","confirmed":"yes"})
        self.assertTrue(malformed["messages"][0].startswith("Error:")); self.assertEqual(c.session,before)
        abandoned=c.handle({"type":"abandon"}); self.assertEqual(abandoned,{"prompt":"Session abandoned","messages":["Check-in abandoned"],"choices":[]})
        self.assertEqual(c.session,{"active":False,"contact_confirmed":None,"appointment_id":None,"completed":False})
        with self.assertRaises(RuntimeError): solution.CheckInConsole(self.apps()).run_once()
if __name__ == "__main__": unittest.main()
