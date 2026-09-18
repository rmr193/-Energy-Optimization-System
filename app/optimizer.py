"""Mathematical Energy Optimizer for GridWise Smart Campus.

Formulates and solves the 24-hour cost-minimization Linear Program (LP)
using the high-performance HiGHS solver in scipy.optimize.linprog.

Constraints:
1. Hourly Energy Balance:
   grid_kwh[h] + solar_used_kwh[h] + battery_discharge_kwh[h] = demand_kwh[h] + battery_charge_kwh[h]
2. Solar Limits:
   0 <= solar_used_kwh[h] <= effective_solar_kwh[h]
3. Battery Dynamics:
   E_after[0] = E_initial + charge[0] - discharge[0]
   E_after[h] = E_after[h-1] + charge[h] - discharge[h]  (for h = 1..23)
4. Battery State Bounds & Directive Reserves:
   effective_minimum_energy_kwh[h] <= E_after[h] <= capacity_kwh
5. Hourly Charge / Discharge Limits:
   charge[h] <= max_charge_kwh_per_hour (0 in no_charge_window)
   discharge[h] <= max_discharge_kwh_per_hour (0 in no_discharge_window)
6. Grid Import Bounds:
   0 <= grid_kwh[h] <= max_grid_kwh (if max_grid_window active)
7. End-of-Day Neutrality:
   E_after[23] == E_initial
8. Objective:
   Minimize total_cost_bdt = sum(grid_kwh[h] * tariff_bdt_per_kwh[h])
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple
import numpy as np
from scipy.optimize import linprog

from app.schemas import (
    BatteryAction,
    BatteryInput,
    DirectiveInterpretation,
    DirectiveType,
    HourInput,
    HourlyPlanEntry,
)

logger = logging.getLogger("gridwise.optimizer")


class OptimizationError(Exception):
    """Raised when the optimization problem is infeasible or solver fails."""
    pass


def compute_effective_inputs(
    hours: List[HourInput],
    battery: BatteryInput,
    directives: List[DirectiveInterpretation],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Calculate effective arrays after applying all validated directives.

    Returns:
        demand: (24,) array
        effective_solar: (24,) array
        tariff: (24,) array
        min_reserve: (24,) array
        charge_limit: (24,) array
        discharge_limit: (24,) array
        grid_cap: (24,) array (np.inf where unrestricted)
    """
    n = len(hours)
    demand = np.array([h.demand_kwh for h in hours], dtype=np.float64)
    effective_solar = np.array([h.solar_kwh for h in hours], dtype=np.float64)
    tariff = np.array([h.tariff_bdt_per_kwh for h in hours], dtype=np.float64)

    min_reserve = np.full(n, battery.minimum_energy_kwh, dtype=np.float64)
    charge_limit = np.full(n, battery.max_charge_kwh_per_hour, dtype=np.float64)
    discharge_limit = np.full(n, battery.max_discharge_kwh_per_hour, dtype=np.float64)
    grid_cap = np.full(n, np.inf, dtype=np.float64)

    for directive in directives:
        if not directive.applies or directive.structured_adjustment is None:
            continue

        adj = directive.structured_adjustment
        if isinstance(adj, dict):
            h_indices = adj.get("hours", [])
        else:
            h_indices = getattr(adj, "hours", [])

        valid_h = [h for h in h_indices if 0 <= h < n]

        if directive.directive_type == DirectiveType.SOLAR_REDUCTION:
            factor = adj.get("factor") if isinstance(adj, dict) else getattr(adj, "factor", 1.0)
            for h in valid_h:
                effective_solar[h] = effective_solar[h] * float(factor)

        elif directive.directive_type == DirectiveType.MINIMUM_BATTERY_RESERVE:
            min_energy = adj.get("minimum_energy_kwh") if isinstance(adj, dict) else getattr(adj, "minimum_energy_kwh", 0.0)
            for h in valid_h:
                min_reserve[h] = max(min_reserve[h], float(min_energy))

        elif directive.directive_type == DirectiveType.NO_CHARGE_WINDOW:
            for h in valid_h:
                charge_limit[h] = 0.0

        elif directive.directive_type == DirectiveType.NO_DISCHARGE_WINDOW:
            for h in valid_h:
                discharge_limit[h] = 0.0

        elif directive.directive_type == DirectiveType.MAX_GRID_WINDOW:
            max_grid = adj.get("max_grid_kwh") if isinstance(adj, dict) else getattr(adj, "max_grid_kwh", np.inf)
            for h in valid_h:
                grid_cap[h] = min(grid_cap[h], float(max_grid))

    return demand, effective_solar, tariff, min_reserve, charge_limit, discharge_limit, grid_cap


def solve_schedule_lp(
    hours: List[HourInput],
    battery: BatteryInput,
    directives: List[DirectiveInterpretation],
) -> List[HourlyPlanEntry]:
    """Formulate and solve the 24-hour Linear Program with HiGHS."""
    n = 24
    demand, effective_solar, tariff, min_reserve, charge_limit, discharge_limit, grid_cap = (
        compute_effective_inputs(hours, battery, directives)
    )

    # Variable indices:
    # 0..23:   grid_kwh (G_h)
    # 24..47:  solar_used_kwh (S_h)
    # 48..71:  battery_charge (C_h)
    # 72..95:  battery_discharge (D_h)
    # 96..119: battery_energy_after (E_h)
    num_vars = 5 * n

    idx_g = lambda h: h
    idx_s = lambda h: n + h
    idx_c = lambda h: 2 * n + h
    idx_d = lambda h: 3 * n + h
    idx_e = lambda h: 4 * n + h

    # Objective:
    # Minimize sum(tariff[h] * G_h) + tiny tie-breaker on (C_h + D_h) to prevent simultaneous
    # charge and discharge or useless battery cycling, - tiny bonus for using clean solar.
    c = np.zeros(num_vars, dtype=np.float64)
    for h in range(n):
        c[idx_g(h)] = tariff[h]
        c[idx_s(h)] = -1e-6  # Prefer using solar over curtailing
        c[idx_c(h)] = 1e-7   # Prevent unnecessary battery cycling
        c[idx_d(h)] = 1e-7

    # Bounds on variables
    bounds = []
    for h in range(n):
        # G_h
        upper_g = grid_cap[h] if np.isfinite(grid_cap[h]) else None
        bounds.append((0.0, upper_g))
    for h in range(n):
        # S_h
        bounds.append((0.0, float(effective_solar[h])))
    for h in range(n):
        # C_h
        bounds.append((0.0, float(charge_limit[h])))
    for h in range(n):
        # D_h
        bounds.append((0.0, float(discharge_limit[h])))
    for h in range(n):
        # E_h
        bounds.append((float(min_reserve[h]), float(battery.capacity_kwh)))

    # Equality constraints: A_eq @ x = b_eq
    eq_rows = []
    b_eq = []

    # 1. Hourly Energy Balance: G_h + S_h + D_h - C_h = Demand_h
    for h in range(n):
        row = np.zeros(num_vars, dtype=np.float64)
        row[idx_g(h)] = 1.0
        row[idx_s(h)] = 1.0
        row[idx_d(h)] = 1.0
        row[idx_c(h)] = -1.0
        eq_rows.append(row)
        b_eq.append(demand[h])

    # 2. Battery State Dynamics:
    # E_0 - C_0 + D_0 = initial_energy_kwh
    row0 = np.zeros(num_vars, dtype=np.float64)
    row0[idx_e(0)] = 1.0
    row0[idx_c(0)] = -1.0
    row0[idx_d(0)] = 1.0
    eq_rows.append(row0)
    b_eq.append(battery.initial_energy_kwh)

    # E_h - E_{h-1} - C_h + D_h = 0  (for h = 1..23)
    for h in range(1, n):
        row = np.zeros(num_vars, dtype=np.float64)
        row[idx_e(h)] = 1.0
        row[idx_e(h - 1)] = -1.0
        row[idx_c(h)] = -1.0
        row[idx_d(h)] = 1.0
        eq_rows.append(row)
        b_eq.append(0.0)

    # 3. End-of-Day Neutrality: E_23 = initial_energy_kwh
    row_neut = np.zeros(num_vars, dtype=np.float64)
    row_neut[idx_e(23)] = 1.0
    eq_rows.append(row_neut)
    b_eq.append(battery.initial_energy_kwh)

    A_eq = np.array(eq_rows, dtype=np.float64)
    b_eq = np.array(b_eq, dtype=np.float64)

    # Solve using HiGHS
    res = linprog(
        c=c,
        A_eq=A_eq,
        b_eq=b_eq,
        bounds=bounds,
        method="highs",
        options={"presolve": True},
    )

    if not res.success:
        raise OptimizationError(f"LP solver failed: {res.message} (status {res.status})")

    x = res.x

    # Extract hourly plan
    hourly_plan: List[HourlyPlanEntry] = []
    tolerance = 1e-4

    for h in range(n):
        g_val = float(x[idx_g(h)])
        s_val = float(x[idx_s(h)])
        c_val = float(x[idx_c(h)])
        d_val = float(x[idx_d(h)])
        e_val = float(x[idx_e(h)])

        # Clean tiny numerical noise
        g_val = 0.0 if g_val < tolerance else g_val
        s_val = 0.0 if s_val < tolerance else s_val
        c_val = 0.0 if c_val < tolerance else c_val
        d_val = 0.0 if d_val < tolerance else d_val

        # Net battery action
        net_battery = c_val - d_val
        if net_battery > tolerance:
            action = BatteryAction.CHARGE
            batt_kwh = net_battery
        elif net_battery < -tolerance:
            action = BatteryAction.DISCHARGE
            batt_kwh = abs(net_battery)
        else:
            action = BatteryAction.IDLE
            batt_kwh = 0.0

        # Maintain exact balance: G_h + S_h + D_h = Demand_h + C_h
        # Adjust G_h slightly to eliminate floating point residuals
        if action == BatteryAction.CHARGE:
            expected_g = demand[h] + batt_kwh - s_val
        elif action == BatteryAction.DISCHARGE:
            expected_g = demand[h] - batt_kwh - s_val
        else:
            expected_g = demand[h] - s_val

        g_val = max(0.0, expected_g)

        hourly_plan.append(
            HourlyPlanEntry(
                hour=h,
                grid_kwh=round(g_val, 4),
                solar_used_kwh=round(s_val, 4),
                battery_action=action,
                battery_kwh=round(batt_kwh, 4),
                battery_energy_after_kwh=round(e_val, 4),
            )
        )

    return hourly_plan
