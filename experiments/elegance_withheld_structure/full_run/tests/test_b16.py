import importlib.util, pathlib, sys, unittest, copy
SOLUTION = pathlib.Path(sys.argv.pop()).resolve()
spec = importlib.util.spec_from_file_location("solution", SOLUTION)
solution = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = solution
spec.loader.exec_module(solution)

from datetime import date

class GardenTests(unittest.TestCase):
    def fixture(self):
        proposal = {
          "beds": {"b1":{"neighbors":["b2"],"accessible":False}, "b2":{"neighbors":["b1"],"accessible":True}},
          "crops": {
            "tom":{"family":"nightshade","plant":[date(2024,3,1),date(2024,3,31)],"harvest":[date(2024,3,1),date(2024,6,30)],"incompatible_families":["brassica"],"required_predecessor":None},
            "bean":{"family":"legume","plant":[date(2024,3,1),date(2024,4,30)],"harvest":[date(2024,3,1),date(2024,6,30)],"incompatible_families":[],"required_predecessor":"pea"},
            "cab":{"family":"brassica","plant":[date(2024,3,1),date(2024,4,30)],"harvest":[date(2024,3,1),date(2024,6,30)],"incompatible_families":[],"required_predecessor":None}},
          "assignments": [
            {"id":"a","bed":"b1","crop":"tom","plant":date(2024,3,1),"harvest":date(2024,3,20),"volunteer":"v1","locked":True},
            {"id":"b","bed":"b1","crop":"bean","plant":date(2024,3,15),"harvest":date(2024,4,1),"volunteer":"v2","locked":False},
            {"id":"c","bed":"b2","crop":"cab","plant":date(2024,3,10),"harvest":date(2024,3,25),"volunteer":"v2","locked":False},
            {"id":"d","bed":"b2","crop":"cab","plant":date(2024,2,20),"harvest":date(2024,2,25),"volunteer":"v2","locked":False}],
          "volunteers":{"v1":{"needs_accessible":True},"v2":{"needs_accessible":False}},
          "previous_crops":{"b1":["rye"],"b2":["pea"]}}
        observations=[{"kind":"bed_unavailable","bed":"b1","start":date(2024,3,10),"end":date(2024,3,12)}]
        return proposal, observations

    def test_complete_diagnostics_and_views(self):
        p, obs=self.fixture(); before=copy.deepcopy((p,obs))
        r=solution.plan_season(p,obs,date(2025,1,1))
        self.assertEqual((p,obs),before)
        self.assertEqual(set(r), {"issues","work_cards","volunteer_views","bed_calendar","next_season"})
        got={(x["code"],tuple(x["assignments"]),x["bed"]) for x in r["issues"]}
        expected={
          ("OCCUPANCY_OVERLAP",("a","b"),"b1"),
          ("NEIGHBOR_CONFLICT",("a","c"),"b1"),
          ("PREDECESSOR_MISSING",("b",),"b1"),
          ("INACCESSIBLE_ASSIGNMENT",("a",),"b1"),
          ("BED_UNAVAILABLE",("a",),"b1"),
          ("OUTSIDE_PLANTING_WINDOW",("d",),"b2"),
          ("OUTSIDE_HARVEST_WINDOW",("d",),"b2")}
        self.assertEqual(got,expected)
        self.assertEqual(r["issues"],sorted(r["issues"],key=lambda x:(x["code"],x["bed"],x["assignments"])))
        self.assertEqual(len(r["work_cards"]),8)
        self.assertEqual(r["work_cards"],sorted(r["work_cards"],key=lambda x:(x["date"],x["kind"],x["assignment"])))
        self.assertEqual(r["volunteer_views"]["v1"],[x for x in r["work_cards"] if x["volunteer"]=="v1"])
        self.assertEqual([x["assignment"] for x in r["bed_calendar"]["b1"]],["a","b"])

    def test_next_season_and_mapping_order_independence(self):
        p,obs=self.fixture(); r=solution.plan_season(p,obs,date(2025,1,1))
        n={x["assignment"]:x for x in r["next_season"]}
        self.assertEqual(n["a"],{"assignment":"a","bed":"b1","crop":"tom","plant":date(2024,3,1),"harvest":date(2024,3,20),"volunteer":"v1","locked":True,"automatic_changes":[]})
        self.assertEqual((n["b"]["plant"],n["b"]["harvest"],n["b"]["automatic_changes"]),(date(2025,3,15),date(2025,4,1),["plant","harvest"]))
        q=copy.deepcopy(p)
        for key in ("beds","crops","volunteers","previous_crops"):
            q[key]=dict(reversed(list(q[key].items())))
        self.assertEqual(r,solution.plan_season(q,list(reversed(obs)),date(2025,1,1)))

if __name__ == "__main__": unittest.main()
