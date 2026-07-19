#!/usr/bin/env python3
"""Configuration checks used by verify.sh."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class MissionValidation:
    ok: bool
    code: str
    message: str


def _positive_number(value: Any) -> bool:
    try:
        number = float(value)
    except Exception:
        return False
    return math.isfinite(number) and number > 0


def validate_mission_config(doc: dict[str, Any]) -> MissionValidation:
    mission = doc.get("mission") or {}
    measure = mission.get("measure") or {}
    budget = doc.get("budget") or {}
    money = budget.get("money") or {}

    objective = str(mission.get("objective") or "").strip()
    metric = str(measure.get("what") or "").strip()
    if not objective or not metric:
        return MissionValidation(False, "missing_mission", "instance.yaml has no mission objective or measure")

    goal = measure.get("goal") or "reach_and_maintain"
    has_target = measure.get("target") is not None or bool(measure.get("target_query"))

    if goal == "reach_and_maintain":
        if not has_target:
            return MissionValidation(
                False,
                "missing_ceiling",
                "reach_and_maintain requires measure.target or measure.target_query",
            )
        return MissionValidation(True, "reach_ceiling", "reach_and_maintain has a target ceiling")

    if goal == "maximize":
        if not _positive_number(money.get("monthly_hard_usd")):
            return MissionValidation(
                False,
                "maximize_without_spend_cap",
                "goal:maximize requires a positive budget.money.monthly_hard_usd spend cap",
            )
        return MissionValidation(True, "maximize_spend_cap", "goal:maximize is bounded by a hard spend cap")

    return MissionValidation(
        False,
        "invalid_goal",
        "measure.goal must be reach_and_maintain or maximize",
    )


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "instance.yaml")
    doc = yaml.safe_load(path.read_text()) or {}
    result = validate_mission_config(doc)
    print(f"{'ok' if result.ok else 'missing'}|{result.code}|{result.message}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
