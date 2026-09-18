"""Tests for paraphrase robustness across all directive types.

Tests the canonical examples from Section 04.2 and Section 11.4 of the Problem Statement.
"""

import pytest
from app.llm_interpreter import local_semantic_parse_note
from app.schemas import BatteryInput, DirectiveType


@pytest.fixture
def sample_battery():
    return BatteryInput(
        capacity_kwh=200,
        initial_energy_kwh=100,
        minimum_energy_kwh=30,
        max_charge_kwh_per_hour=50,
        max_discharge_kwh_per_hour=50,
    )


def test_section_4_2_canonical_examples(sample_battery):
    # 1. Solar reduction
    res = local_semantic_parse_note("Solar output will drop to about 20% from 1 PM to 3 PM.", 0, sample_battery)
    assert res["applies"] is True
    assert res["directive_type"] == DirectiveType.SOLAR_REDUCTION
    assert res["structured_adjustment"]["hours"] == [13, 14]
    assert res["structured_adjustment"]["factor"] == 0.2

    # 2. No charge window
    res = local_semantic_parse_note("Do not charge the battery between 2 PM and 4 PM.", 1, sample_battery)
    assert res["applies"] is True
    assert res["directive_type"] == DirectiveType.NO_CHARGE_WINDOW
    assert res["structured_adjustment"]["hours"] == [14, 15]

    # 3. Minimum battery reserve
    res = local_semantic_parse_note("Keep at least 120 kWh in reserve from 6 PM until 9 PM.", 2, sample_battery)
    assert res["applies"] is True
    assert res["directive_type"] == DirectiveType.MINIMUM_BATTERY_RESERVE
    assert res["structured_adjustment"]["hours"] == [18, 19, 20]
    assert res["structured_adjustment"]["minimum_energy_kwh"] == 120.0

    # 4. No-op distractor
    res = local_semantic_parse_note("The cafeteria menu changes tomorrow.", 3, sample_battery)
    assert res["applies"] is False
    assert res["directive_type"] == DirectiveType.NO_OP
    assert res["structured_adjustment"] is None


def test_section_11_4_solar_paraphrase_equivalence(sample_battery):
    """Section 11.4 states all 3 notes mean the exact same solar_reduction directive."""
    notes = [
        "PV production will drop to about 20% between 13:00 and 15:00.",
        "Panel washing from one until three will leave roughly one-fifth of normal solar output.",
        "Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window.",
    ]
    for n in notes:
        res = local_semantic_parse_note(n, 0, sample_battery)
        assert res["applies"] is True
        assert res["directive_type"] == DirectiveType.SOLAR_REDUCTION
        assert res["structured_adjustment"]["hours"] == [13, 14], f"Failed hours for: {n}"
        assert abs(res["structured_adjustment"]["factor"] - 0.2) < 1e-3, f"Failed factor for: {n}"


def test_no_discharge_paraphrases(sample_battery):
    notes = [
        ("For protection testing, the battery must not discharge from 6 PM until 8 PM.", [18, 19]),
        ("Do not discharge the battery from 5 PM until 7 PM during relay testing.", [17, 18]),
        ("Battery discharging is unavailable from 1 PM to 3 PM.", [13, 14]),
    ]
    for note, expected_hours in notes:
        res = local_semantic_parse_note(note, 0, sample_battery)
        assert res["applies"] is True
        assert res["directive_type"] == DirectiveType.NO_DISCHARGE_WINDOW
        assert res["structured_adjustment"]["hours"] == expected_hours


def test_max_grid_paraphrases(sample_battery):
    notes = [
        ("From 6 PM until 9 PM, campus grid import must not exceed 155 kWh in any hour because the feeder is operating under a temporary limit.", [18, 19, 20], 155.0),
        ("The evening transformer limit is 180 kWh of grid import from 7 PM until 9 PM.", [19, 20], 180.0),
        ("Grid intake must stay at or below 190 kWh from 7 PM until 10 PM while the substation is constrained.", [19, 20, 21], 190.0),
    ]
    for note, expected_hours, expected_cap in notes:
        res = local_semantic_parse_note(note, 0, sample_battery)
        assert res["applies"] is True
        assert res["directive_type"] == DirectiveType.MAX_GRID_WINDOW
        assert res["structured_adjustment"]["hours"] == expected_hours
        assert res["structured_adjustment"]["max_grid_kwh"] == expected_cap


def test_reserve_percentage_paraphrases(sample_battery):
    note = "Keep at least 50% of the battery capacity stored in the battery from 6 PM until 9 PM for emergency operations."
    res = local_semantic_parse_note(note, 0, sample_battery)
    assert res["applies"] is True
    assert res["directive_type"] == DirectiveType.MINIMUM_BATTERY_RESERVE
    assert res["structured_adjustment"]["hours"] == [18, 19, 20]
    # 50% of 200 kWh = 100 kWh
    assert res["structured_adjustment"]["minimum_energy_kwh"] == 100.0
