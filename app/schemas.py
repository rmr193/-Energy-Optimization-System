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


class HealthResponse(BaseModel):
    """GET /health response."""
    status: Literal["ok"] = "ok"
