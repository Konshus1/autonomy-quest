from types import SimpleNamespace

from runner.config import Measure
from runner.loop import _mission_goal_reached


class Db:
    def __init__(self, target=None):
        self.target = target

    def read_scalar(self, query):
        return self.target


def inst(measure):
    return SimpleNamespace(mission=SimpleNamespace(measure=measure))


def test_reach_and_maintain_uses_declared_target_not_any_improvement():
    measure = Measure("coverage", "select 9", target=10, goal="reach_and_maintain")
    assert _mission_goal_reached(inst(measure), Db(), 9, productive=True) is False
    assert _mission_goal_reached(inst(measure), Db(), 10, productive=True) is True


def test_live_target_query_is_resolved_at_outcome_time():
    measure = Measure("coverage", "select 10", target_query="select count(*)", goal="reach_and_maintain")
    assert _mission_goal_reached(inst(measure), Db(target=11), 10, productive=True) is False
    assert _mission_goal_reached(inst(measure), Db(target=10), 10, productive=True) is True


def test_maximize_mission_uses_objective_productivity_as_its_goal_unit():
    measure = Measure("revenue", "select revenue", goal="maximize")
    assert _mission_goal_reached(inst(measure), Db(), 11, productive=True) is True
    assert _mission_goal_reached(inst(measure), Db(), 11, productive=False) is False
