"""Pydantic schemas and enums for the GridWise optimization API.

Strictly complies with BUP CSE Fest 2026 Hackathon Preliminary Problem Statement:
Section 04 (Supported Directives), Section 07 (Request Schema), and Section 10 (Response Schema).
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator


# ------------------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------------------

class DirectiveType(str, Enum):
    """Allowed directive types per Problem Statement Section 04."""
    SOLAR_REDUCTION = "solar_reduction"
    MINIMUM_BATTERY_RESERVE = "minimum_battery_reserve"
    NO_CHARGE_WINDOW = "no_charge_window"
    NO_DISCHARGE_WINDOW = "no_discharge_window"
    MAX_GRID_WINDOW = "max_grid_window"
    NO_OP = "no_op"


class BatteryAction(str, Enum):
    """Allowed battery actions per Problem Statement Section 10.3."""
    CHARGE = "charge"
    DISCHARGE = "discharge"
    IDLE = "idle"


# ------------------------------------------------------------------------------
# Request Schemas (Section 07)
# ------------------------------------------------------------------------------

class HourInput(BaseModel):
    """Hourly forecast and tariff data."""
    hour: int = Field(..., ge=0, le=23, description="Unique integer from 0 to 23")
    demand_kwh: float = Field(..., ge=0.0, description="Campus demand that must be supplied in this hour")
    solar_kwh: float = Field(..., ge=0.0, description="Base solar energy available before adjustments")
    tariff_bdt_per_kwh: float = Field(..., ge=0.0, description="Grid electricity price for this hour")


class BatteryInput(BaseModel):
    """Battery energy storage system parameters."""
    capacity_kwh: float = Field(..., gt=0.0, description="Maximum energy the battery can store")
    initial_energy_kwh: float = Field(..., ge=0.0, description="Battery energy at the start of hour 0")
    minimum_energy_kwh: float = Field(..., ge=0.0, description="Base reserve level the battery must never go below")
    max_charge_kwh_per_hour: float = Field(..., ge=0.0, description="Maximum energy that may be added in one hour")
    max_discharge_kwh_per_hour: float = Field(..., ge=0.0, description="Maximum energy that may be removed in one hour")

    @model_validator(mode="after")
    def validate_bounds(self) -> "BatteryInput":
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError(f"initial_energy_kwh ({self.initial_energy_kwh}) cannot exceed capacity_kwh ({self.capacity_kwh})")
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError(f"minimum_energy_kwh ({self.minimum_energy_kwh}) cannot exceed capacity_kwh ({self.capacity_kwh})")
        if self.initial_energy_kwh < self.minimum_energy_kwh:
            raise ValueError(f"initial_energy_kwh ({self.initial_energy_kwh}) cannot be below minimum_energy_kwh ({self.minimum_energy_kwh})")
        return self


class OptimizeEnergyRequest(BaseModel):
    """POST /optimize-energy request schema."""
    scenario_id: str = Field(..., min_length=1, description="Unique synthetic scenario identifier")
    operator_notes: List[str] = Field(..., min_length=1, max_length=3, description="1 to 3 natural-language notes")
    hours: List[HourInput] = Field(..., min_length=24, max_length=24, description="Hourly entries for 0 to 23")
    battery: BatteryInput

    @field_validator("operator_notes")
    @classmethod
    def validate_operator_notes(cls, v: List[str]) -> List[str]:
        for idx, note in enumerate(v):
            if not note or not note.strip():
                raise ValueError(f"Operator note at index {idx} cannot be empty")
        return v

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, v: List[HourInput]) -> List[HourInput]:
        hours_seen = set()
        for item in v:
            if item.hour in hours_seen:
                raise ValueError(f"Duplicate hour {item.hour} found in hours array")
            hours_seen.add(item.hour)
        if hours_seen != set(range(24)):
            raise ValueError(f"Hours array must contain exactly hours 0 through 23, missing: {set(range(24)) - hours_seen}")
        # Return sorted by hour ascending
        return sorted(v, key=lambda h: h.hour)

    model_config = {
        "json_schema_extra": {
            "example": {
                "scenario_id": "SAMPLE-01",
                "operator_notes": [
                    "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
                    "The sports office moved next month's registration deadline."
                ],
                "hours": [
                    {"hour": 0, "demand_kwh": 90.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.0},
                    {"hour": 1, "demand_kwh": 85.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.0},
                    {"hour": 2, "demand_kwh": 80.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
                    {"hour": 3, "demand_kwh": 80.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
                    {"hour": 4, "demand_kwh": 85.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0},
                    {"hour": 5, "demand_kwh": 95.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 6.0},
                    {"hour": 6, "demand_kwh": 110.0, "solar_kwh": 5.0, "tariff_bdt_per_kwh": 8.0},
                    {"hour": 7, "demand_kwh": 130.0, "solar_kwh": 20.0, "tariff_bdt_per_kwh": 10.0},
                    {"hour": 8, "demand_kwh": 150.0, "solar_kwh": 50.0, "tariff_bdt_per_kwh": 12.0},
                    {"hour": 9, "demand_kwh": 165.0, "solar_kwh": 90.0, "tariff_bdt_per_kwh": 14.0},
                    {"hour": 10, "demand_kwh": 175.0, "solar_kwh": 130.0, "tariff_bdt_per_kwh": 16.0},
                    {"hour": 11, "demand_kwh": 180.0, "solar_kwh": 160.0, "tariff_bdt_per_kwh": 16.0},
                    {"hour": 12, "demand_kwh": 185.0, "solar_kwh": 180.0, "tariff_bdt_per_kwh": 15.0},
                    {"hour": 13, "demand_kwh": 180.0, "solar_kwh": 170.0, "tariff_bdt_per_kwh": 14.0},
                    {"hour": 14, "demand_kwh": 170.0, "solar_kwh": 140.0, "tariff_bdt_per_kwh": 13.0},
                    {"hour": 15, "demand_kwh": 165.0, "solar_kwh": 90.0, "tariff_bdt_per_kwh": 14.0},
                    {"hour": 16, "demand_kwh": 170.0, "solar_kwh": 45.0, "tariff_bdt_per_kwh": 18.0},
                    {"hour": 17, "demand_kwh": 185.0, "solar_kwh": 10.0, "tariff_bdt_per_kwh": 22.0},
                    {"hour": 18, "demand_kwh": 205.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 28.0},
                    {"hour": 19, "demand_kwh": 215.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 30.0},
                    {"hour": 20, "demand_kwh": 205.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 26.0},
                    {"hour": 21, "demand_kwh": 175.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 18.0},
                    {"hour": 22, "demand_kwh": 135.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 10.0},
                    {"hour": 23, "demand_kwh": 105.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 7.0}
                ],
                "battery": {
                    "capacity_kwh": 220.0,
                    "initial_energy_kwh": 110.0,
                    "minimum_energy_kwh": 40.0,
                    "max_charge_kwh_per_hour": 50.0,
                    "max_discharge_kwh_per_hour": 50.0
                }
            }
        }
    }


# ------------------------------------------------------------------------------
# Structured Adjustment Schemas (Section 04)
# ------------------------------------------------------------------------------

class SolarReductionAdjustment(BaseModel):
    """Remaining usable solar factor in [0.0, 1.0]. Example: 80% reduction -> factor=0.2."""
    hours: List[int] = Field(..., description="Unique integers 0-23 in ascending order")
    factor: float = Field(..., ge=0.0, le=1.0, description="Usable fraction remaining")


class MinimumBatteryReserveAdjustment(BaseModel):
    """Raised minimum battery reserve for listed hours."""
    hours: List[int] = Field(..., description="Unique integers 0-23 in ascending order")
    minimum_energy_kwh: float = Field(..., ge=0.0, description="Minimum reserve level in kWh")


class NoChargeAdjustment(BaseModel):
    """Hours during which battery charging is unavailable."""
    hours: List[int] = Field(..., description="Unique integers 0-23 in ascending order")


class NoDischargeAdjustment(BaseModel):
    """Hours during which battery discharging is unavailable."""
    hours: List[int] = Field(..., description="Unique integers 0-23 in ascending order")


class MaxGridAdjustment(BaseModel):
    """Hours during which grid import is capped at max_grid_kwh."""
    hours: List[int] = Field(..., description="Unique integers 0-23 in ascending order")
    max_grid_kwh: float = Field(..., ge=0.0, description="Grid import cap in kWh")


StructuredAdjustmentUnion = Union[
    SolarReductionAdjustment,
    MinimumBatteryReserveAdjustment,
    NoChargeAdjustment,
    NoDischargeAdjustment,
    MaxGridAdjustment,
]


# ------------------------------------------------------------------------------
# Directive Interpretation Schema (Section 10.2)
# ------------------------------------------------------------------------------

class DirectiveInterpretation(BaseModel):
    """Machine-checkable interpretation entry for each operator note."""
    note_index: int = Field(..., ge=0, description="Zero-based index of corresponding operator note")
    applies: bool = Field(..., description="true for applicable directive; false only for no_op")
    directive_type: DirectiveType
    structured_adjustment: Optional[Union[dict, StructuredAdjustmentUnion]] = Field(
        None,
        description="Structured adjustment object, or null only for no_op",
    )
    explanation: str = Field(..., description="Short explanation of the interpretation")

    @model_validator(mode="after")
    def validate_applies_and_adjustment(self) -> "DirectiveInterpretation":
        if self.directive_type == DirectiveType.NO_OP:
            if self.applies:
                raise ValueError("applies must be false when directive_type is no_op")
            if self.structured_adjustment is not None:
                raise ValueError("structured_adjustment must be null when directive_type is no_op")
        else:
            if not self.applies:
                raise ValueError(f"applies must be true when directive_type is {self.directive_type.value}")
            if self.structured_adjustment is None:
                raise ValueError(f"structured_adjustment cannot be null when directive_type is {self.directive_type.value}")
        return self


# ------------------------------------------------------------------------------
# Hourly Plan Entry Schema (Section 10.3)
# ------------------------------------------------------------------------------

class HourlyPlanEntry(BaseModel):
    """Single hour result in the 24-hour schedule."""
    hour: int = Field(..., ge=0, le=23, description="Hour 0 through 23")
    grid_kwh: float = Field(..., ge=0.0, description="Grid energy purchased in kWh")
    solar_used_kwh: float = Field(..., ge=0.0, description="Solar energy used in kWh")
    battery_action: BatteryAction = Field(..., description="charge, discharge, or idle")
    battery_kwh: float = Field(..., ge=0.0, description="Magnitude of battery action, 0 when idle")
    battery_energy_after_kwh: float = Field(..., ge=0.0, description="Battery energy after this hour")

    @model_validator(mode="after")
    def validate_action_consistency(self) -> "HourlyPlanEntry":
        if self.battery_action == BatteryAction.IDLE and abs(self.battery_kwh) > 1e-4:
            raise ValueError(f"battery_kwh must be 0 when battery_action is idle, got {self.battery_kwh}")
        return self


# ------------------------------------------------------------------------------
# Response Schemas (Section 06 & 10.1)
# ------------------------------------------------------------------------------

class OptimizeEnergyResponse(BaseModel):
    """Full successful POST /optimize-energy response schema."""
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str

    model_config = {
        "json_schema_extra": {
            "example": {
                "scenario_id": "SAMPLE-01",
                "directive_interpretation": [
                    {
                        "note_index": 0,
                        "applies": True,
                        "directive_type": "solar_reduction",
                        "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
                        "explanation": "Solar output reduced to 25% usable during stated window."
                    },
                    {
                        "note_index": 1,
                        "applies": False,
                        "directive_type": "no_op",
                        "structured_adjustment": None,
                        "explanation": "This note does not affect today's 24-hour energy schedule."
                    }
                ],
                "hourly_plan": [
                    {
                        "hour": 0,
                        "grid_kwh": 90.0,
                        "solar_used_kwh": 0.0,
                        "battery_action": "idle",
                        "battery_kwh": 0.0,
                        "battery_energy_after_kwh": 110.0
                    },
                    {
                        "hour": 1,
                        "grid_kwh": 45.0,
                        "solar_used_kwh": 0.0,
                        "battery_action": "discharge",
                        "battery_kwh": 40.0,
                        "battery_energy_after_kwh": 70.0
                    },
                    {
                        "hour": 2,
                        "grid_kwh": 130.0,
                        "solar_used_kwh": 0.0,
                        "battery_action": "charge",
                        "battery_kwh": 50.0,
                        "battery_energy_after_kwh": 120.0
                    }
                ],
                "total_grid_kwh": 2692.5,
                "total_cost_bdt": 38365.0,
                "peak_grid_kwh": 175.0,
                "plan_summary": "Optimized 24-hour schedule applying 1 active directive(s). Neutrality preserved."
            }
        }
    }


class HealthResponse(BaseModel):
    """GET /health response."""
    status: Literal["ok"] = "ok"
