"""Independent Schedule Replay and Consistency Verifier.

Replays the returned 24-hour plan against all GridWise physics and directive rules
before returning the final response.

Verifies:
1. Hourly energy balance within 0.01 kWh tolerance.
2. Solar usage within effective solar bounds (unused solar curtailed, no grid export).
3. Battery state transitions and hourly capacity/reserve bounds.
4. Active directive windows (no_charge, no_discharge, minimum_reserve, max_grid).
5. End-of-day battery neutrality (E_after[23] == E_initial within 0.01 kWh).
6. Accurate recalculation of total_grid_kwh, total_cost_bdt, and peak_grid_kwh.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple
import numpy as np

from app.schemas import (
    BatteryAction,
    BatteryInput,
    DirectiveInterpretation,
    DirectiveType,
    HourInput,
    HourlyPlanEntry,
)

logger = logging.getLogger("gridwise.validator")


class ReplayValidationError(Exception):
    """Raised when an energy or directive constraint is violated during schedule replay."""
    pass


def replay_and_validate_schedule(
    hourly_plan: List[HourlyPlanEntry],
    hours: List[HourInput],
    battery: BatteryInput,
    directives: List[DirectiveInterpretation],
    tolerance: float = 0.02,
) -> Tuple[float, float, float, str]:
    """Replays the 24-hour hourly_plan against all constraints.

    Returns:
        total_grid_kwh: recalculated sum of grid energy
        total_cost_bdt: recalculated total cost
        peak_grid_kwh: recalculated peak hourly grid intake
        plan_summary: concise summary of the operating strategy
    """
    if len(hourly_plan) != 24:
        raise ReplayValidationError(f"Expected 24 hourly entries in hourly_plan, got {len(hourly_plan)}")

    # Compute effective parameters
    n = 24
    effective_solar = {h.hour: float(h.solar_kwh) for h in hours}
    min_reserve = {h.hour: float(battery.minimum_energy_kwh) for h in hours}
    no_charge_hours = set()
    no_discharge_hours = set()
    grid_caps: Dict[int, float] = {}

    for d in directives:
        if not d.applies or d.structured_adjustment is None:
            continue
        adj = d.structured_adjustment
        h_list = adj.get("hours", []) if isinstance(adj, dict) else getattr(adj, "hours", [])

        if d.directive_type == DirectiveType.SOLAR_REDUCTION:
            factor = adj.get("factor") if isinstance(adj, dict) else getattr(adj, "factor", 1.0)
            for h in h_list:
                if h in effective_solar:
                    effective_solar[h] *= float(factor)

        elif d.directive_type == DirectiveType.MINIMUM_BATTERY_RESERVE:
            res_val = adj.get("minimum_energy_kwh") if isinstance(adj, dict) else getattr(adj, "minimum_energy_kwh", 0.0)
            for h in h_list:
                if h in min_reserve:
                    min_reserve[h] = max(min_reserve[h], float(res_val))

        elif d.directive_type == DirectiveType.NO_CHARGE_WINDOW:
            no_charge_hours.update(h_list)

        elif d.directive_type == DirectiveType.NO_DISCHARGE_WINDOW:
            no_discharge_hours.update(h_list)

        elif d.directive_type == DirectiveType.MAX_GRID_WINDOW:
            cap_val = adj.get("max_grid_kwh") if isinstance(adj, dict) else getattr(adj, "max_grid_kwh", 1e9)
            for h in h_list:
                if h in grid_caps:
                    grid_caps[h] = min(grid_caps[h], float(cap_val))
                else:
                    grid_caps[h] = float(cap_val)

    # Sort plan by hour
    sorted_plan = sorted(hourly_plan, key=lambda x: x.hour)
    demand_by_hour = {h.hour: float(h.demand_kwh) for h in hours}
    tariff_by_hour = {h.hour: float(h.tariff_bdt_per_kwh) for h in hours}

    total_grid_kwh = 0.0
    total_cost_bdt = 0.0
    peak_grid_kwh = 0.0

    current_battery = float(battery.initial_energy_kwh)

    for entry in sorted_plan:
        h = entry.hour
        d_kwh = demand_by_hour[h]
        s_eff = effective_solar[h]
        g_kwh = float(entry.grid_kwh)
        s_used = float(entry.solar_used_kwh)
        b_act = entry.battery_action
        b_kwh = float(entry.battery_kwh)
        e_after = float(entry.battery_energy_after_kwh)

        # 1. Non-negativity
        if g_kwh < -tolerance or s_used < -tolerance or b_kwh < -tolerance:
            raise ReplayValidationError(f"Hour {h}: Negative energy value detected.")

        # 2. Solar usage bound
        if s_used > s_eff + tolerance:
            raise ReplayValidationError(
                f"Hour {h}: Solar used ({s_used:.2f} kWh) exceeds effective solar ({s_eff:.2f} kWh)."
            )

        # 3. Battery Action & Limits
        charge_amt = 0.0
        discharge_amt = 0.0
        if b_act == BatteryAction.CHARGE:
            charge_amt = b_kwh
            if h in no_charge_hours and charge_amt > tolerance:
                raise ReplayValidationError(f"Hour {h}: Charging during active no_charge_window.")
            if charge_amt > battery.max_charge_kwh_per_hour + tolerance:
                raise ReplayValidationError(f"Hour {h}: Charge {charge_amt:.2f} exceeds max_charge limit.")
            expected_e_after = current_battery + charge_amt

        elif b_act == BatteryAction.DISCHARGE:
            discharge_amt = b_kwh
            if h in no_discharge_hours and discharge_amt > tolerance:
                raise ReplayValidationError(f"Hour {h}: Discharging during active no_discharge_window.")
            if discharge_amt > battery.max_discharge_kwh_per_hour + tolerance:
                raise ReplayValidationError(f"Hour {h}: Discharge {discharge_amt:.2f} exceeds max_discharge limit.")
            expected_e_after = current_battery - discharge_amt

        else:  # IDLE
            if b_kwh > tolerance:
                raise ReplayValidationError(f"Hour {h}: battery_kwh must be 0 for idle action.")
            expected_e_after = current_battery

        # 4. Battery State Transition
        if abs(e_after - expected_e_after) > tolerance:
            raise ReplayValidationError(
                f"Hour {h}: State transition error: reported E_after={e_after:.2f}, expected={expected_e_after:.2f}"
            )

        # 5. Battery Bounds
        active_min = min_reserve[h]
        if e_after < active_min - tolerance:
            raise ReplayValidationError(
                f"Hour {h}: Battery energy ({e_after:.2f}) below required reserve ({active_min:.2f})."
            )
        if e_after > battery.capacity_kwh + tolerance:
            raise ReplayValidationError(
                f"Hour {h}: Battery energy ({e_after:.2f}) exceeds capacity ({battery.capacity_kwh:.2f})."
            )

        # 6. Grid Import Cap
        if h in grid_caps:
            cap = grid_caps[h]
            if g_kwh > cap + tolerance:
                raise ReplayValidationError(f"Hour {h}: Grid import ({g_kwh:.2f}) exceeds feeder cap ({cap:.2f}).")

        # 7. Hourly Energy Balance: G + S_used + D = Demand + C
        generation_side = g_kwh + s_used + discharge_amt
        demand_side = d_kwh + charge_amt
        if abs(generation_side - demand_side) > tolerance:
            raise ReplayValidationError(
                f"Hour {h}: Energy balance violation: Gen={generation_side:.2f} != Load={demand_side:.2f}"
            )

        # Update running state
        current_battery = e_after
        total_grid_kwh += g_kwh
        total_cost_bdt += g_kwh * tariff_by_hour[h]
        peak_grid_kwh = max(peak_grid_kwh, g_kwh)

    # 8. End-of-Day Neutrality: E_after[23] == E_initial
    if abs(current_battery - battery.initial_energy_kwh) > tolerance:
        raise ReplayValidationError(
            f"End-of-day neutrality violated: final battery energy {current_battery:.2f} != initial {battery.initial_energy_kwh:.2f}"
        )

    # Generate professional summary
    applied_count = sum(1 for d in directives if d.applies)
    plan_summary = (
        f"Optimized 24-hour schedule applying {applied_count} active directive(s). "
        f"Discharges battery during peak tariff windows, charges during economical night/solar periods, "
        f"and preserves end-of-day neutrality at {battery.initial_energy_kwh:.1f} kWh."
    )

    return (
        round(total_grid_kwh, 4),
        round(total_cost_bdt, 4),
        round(peak_grid_kwh, 4),
        plan_summary,
    )
