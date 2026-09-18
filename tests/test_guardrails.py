"""Tests for deterministic guardrails and input validation."""

import pytest
from app.guardrails import (
    normalize_hours,
    sanitize_directive_interpretation,
    validate_and_guardrail_directives,
)
from app.schemas import BatteryInput, DirectiveType


def test_normalize_hours():
    # Out of order with duplicates and out-of-range values
    raw = [15, 2, 14, 2, 24, -1, "13", "abc"]
    res = normalize_hours(raw)
    assert res == [2, 13, 14, 15]


def test_guardrail_solar_reduction_bounds():
    battery = BatteryInput(
        capacity_kwh=200,
        initial_energy_kwh=100,
        minimum_energy_kwh=40,
        max_charge_kwh_per_hour=50,
        max_discharge_kwh_per_hour=50,
    )

    # Factor exceeds 1.0 -> should clamp to 1.0
    raw = {
        "directive_type": "solar_reduction",
        "structured_adjustment": {"hours": [12, 13], "factor": 1.5},
        "explanation": "Test factor",
    }
    sanitized = sanitize_directive_interpretation(raw, 0, "Test note", battery)
    assert sanitized.applies is True
    assert sanitized.directive_type == DirectiveType.SOLAR_REDUCTION
    assert sanitized.structured_adjustment["factor"] == 1.0
    assert sanitized.structured_adjustment["hours"] == [12, 13]


def test_guardrail_reserve_bounds():
    battery = BatteryInput(
        capacity_kwh=200,
        initial_energy_kwh=100,
        minimum_energy_kwh=40,
        max_charge_kwh_per_hour=50,
        max_discharge_kwh_per_hour=50,
    )

    # Reserve exceeds battery capacity -> clamps to capacity
    raw = {
        "directive_type": "minimum_battery_reserve",
        "structured_adjustment": {"hours": [18, 19], "minimum_energy_kwh": 350},
        "explanation": "Too high reserve",
    }
    sanitized = sanitize_directive_interpretation(raw, 0, "Test note", battery)
    assert sanitized.applies is True
    assert sanitized.structured_adjustment["minimum_energy_kwh"] == 200.0


def test_guardrail_no_op_enforces_null():
    battery = BatteryInput(
        capacity_kwh=200,
        initial_energy_kwh=100,
        minimum_energy_kwh=40,
        max_charge_kwh_per_hour=50,
        max_discharge_kwh_per_hour=50,
    )

    raw = {
        "directive_type": "no_op",
        "structured_adjustment": {"hours": [1, 2]},
        "explanation": "Irrelevant note",
    }
    sanitized = sanitize_directive_interpretation(raw, 0, "Test note", battery)
    assert sanitized.applies is False
    assert sanitized.directive_type == DirectiveType.NO_OP
    assert sanitized.structured_adjustment is None


def test_guardrail_unknown_directive_falls_back():
    raw = {
        "directive_type": "unknown_future_directive",
        "structured_adjustment": {"foo": "bar"},
    }
    sanitized = sanitize_directive_interpretation(raw, 0, "Invalid note")
    assert sanitized.applies is False
    assert sanitized.directive_type == DirectiveType.NO_OP
    assert sanitized.structured_adjustment is None
