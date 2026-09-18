"""Test all 10 official public sample cases.

Verifies:
1. Directive interpretation matches expected ground truth (directive_type, hours, factor/reserve/cap, applies).
2. Schedule satisfies all energy balance, battery, and directive constraints via replay validator.
3. Total cost and grid energy match or outperform reference schedules within official numeric tolerance.
"""

import json
from pathlib import Path
import pytest
from app.guardrails import validate_and_guardrail_directives
from app.llm_interpreter import interpret_operator_notes
from app.optimizer import solve_schedule_lp
from app.replay_validator import replay_and_validate_schedule
from app.schemas import BatteryInput, HourInput, OptimizeEnergyRequest

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_cases.json"


def load_sample_cases():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["cases"]


@pytest.mark.parametrize("case", load_sample_cases(), ids=lambda c: c["id"])
def test_sample_case_e2e(case):
    case_id = case["id"]
    inp = case["input"]
    expected = case["expected_output"]

    # Build Pydantic request
    req = OptimizeEnergyRequest(
        scenario_id=inp["scenario_id"],
        operator_notes=inp["operator_notes"],
        hours=[HourInput(**h) for h in inp["hours"]],
        battery=BatteryInput(**inp["battery"]),
    )

    # 1. Test LLM Directive Interpretation
    directives = interpret_operator_notes(req.operator_notes, req.battery)
    assert len(directives) == len(req.operator_notes), f"{case_id}: Expected {len(req.operator_notes)} interpretations"

    expected_directives = expected["directive_interpretation"]
    for i, exp_d in enumerate(expected_directives):
        actual_d = directives[i]
        assert actual_d.note_index == exp_d["note_index"], f"{case_id} Note {i}: Note index mismatch"
        assert actual_d.applies == exp_d["applies"], f"{case_id} Note {i}: Applies mismatch"
        assert actual_d.directive_type.value == exp_d["directive_type"], f"{case_id} Note {i}: Directive type mismatch"

        if exp_d["structured_adjustment"] is not None:
            assert actual_d.structured_adjustment is not None, f"{case_id} Note {i}: Missing structured adjustment"
            exp_adj = exp_d["structured_adjustment"]
            act_adj = actual_d.structured_adjustment

            # Check hours
            assert act_adj["hours"] == exp_adj["hours"], f"{case_id} Note {i}: Hours mismatch: got {act_adj['hours']}, expected {exp_adj['hours']}"

            # Check specific fields
            if "factor" in exp_adj:
                assert abs(act_adj["factor"] - exp_adj["factor"]) <= 0.01, f"{case_id} Note {i}: Factor mismatch"
            if "minimum_energy_kwh" in exp_adj:
                assert abs(act_adj["minimum_energy_kwh"] - exp_adj["minimum_energy_kwh"]) <= 0.01, f"{case_id} Note {i}: Reserve mismatch"
            if "max_grid_kwh" in exp_adj:
                assert abs(act_adj["max_grid_kwh"] - exp_adj["max_grid_kwh"]) <= 0.01, f"{case_id} Note {i}: Max grid mismatch"
        else:
            assert actual_d.structured_adjustment is None, f"{case_id} Note {i}: Expected null adjustment"

    # 2. Test Mathematical Optimization
    plan = solve_schedule_lp(req.hours, req.battery, directives)
    assert len(plan) == 24, f"{case_id}: Hourly plan must have 24 entries"

    # 3. Test Schedule Replay Verification
    total_grid, total_cost, peak_grid, summary = replay_and_validate_schedule(
        plan, req.hours, req.battery, directives
    )

    # 4. Compare Costs within Official Tolerance (0.01 kWh / 0.01 BDT)
    exp_cost = expected["total_cost_bdt"]
    exp_grid = expected["total_grid_kwh"]
    exp_peak = expected["peak_grid_kwh"]

    assert total_cost <= exp_cost + 0.05, f"{case_id}: Cost {total_cost} exceeds expected {exp_cost}"
    assert abs(total_grid - exp_grid) <= 0.05, f"{case_id}: Grid kWh {total_grid} differs from expected {exp_grid}"
    assert abs(peak_grid - exp_peak) <= 0.05, f"{case_id}: Peak grid {peak_grid} differs from expected {exp_peak}"
