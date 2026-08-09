import importlib.util, pathlib, sys, unittest, copy
SOLUTION = pathlib.Path(sys.argv.pop()).resolve()
spec = importlib.util.spec_from_file_location("solution", SOLUTION)
solution = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = solution
spec.loader.exec_module(solution)

class WorkspaceTests(unittest.TestCase):
    def data(self):
        return {"title":"Lab","items":[
          {"id":"s","kind":"section","title":"Start","items":[
            {"id":"n","kind":"note","title":"Memo","text":"Hello [[?m2]] world"},
            {"id":"g","kind":"group","title":"Nested","items":[{"id":"n2","kind":"note","title":"More","text":"One two [[?m1]]"}]}]},
          {"id":"r","kind":"reference","title":"Book","label":"B","word_count":7,"unresolved":["m2","m3"]}]}
    def test_aggregates_outline_and_copying(self):
        d=self.data(); w=solution.Workspace(d); d["title"]="changed"
        self.assertEqual(w.total_word_count(),11)
        self.assertEqual(w.unresolved_markers(),["m1","m2","m3"])
        self.assertEqual(w.outline(),"Lab\n  section: Start\n    note: Memo\n    group: Nested\n      note: More\n  reference: Book")
        exported=w.to_data(); exported["title"]="bad"
        self.assertEqual(w.to_data()["title"],"Lab")
    def test_move_and_atomic_rejections(self):
        w=solution.Workspace(self.data())
        w.move("r","g",0)
        self.assertEqual(w.to_data()["items"][0]["items"][1]["items"][0]["id"],"r")
        before=w.to_data()
        for args in [("s","g",None),("n","r",None),("missing","workspace",None),("n","workspace",99),(7,"workspace",None)]:
            with self.assertRaises(ValueError): w.move(*args)
            self.assertEqual(w.to_data(),before)
        w.move("n","workspace",0)
        self.assertEqual(w.to_data()["items"][0]["id"],"n")
if __name__ == "__main__": unittest.main()
