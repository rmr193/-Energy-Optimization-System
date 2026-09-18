"""Deterministic Guardrails and Sanitizer for LLM Operator Directive Interpretations.

Strictly enforces Section 08 of the BUP CSE Fest 2026 Problem Statement:
- Allowed directive types
- Note mapping coverage and order (0..N-1)
- Ascending unique hours in [0..23]
- Factor bounds [0.0, 1.0] for solar_reduction
- Battery reserve bounds [0, capacity_kwh]
- Non-negative finite values for grid cap
- Applies semantics (applies=False iff directive_type=no_op)
- Safe failure handling without crashing
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.schemas import (
    BatteryInput,
    DirectiveInterpretation,
    DirectiveType,
    MaxGridAdjustment,
    MinimumBatteryReserveAdjustment,
    NoChargeAdjustment,
    NoDischargeAdjustment,
    SolarReductionAdjustment,
)

logger = logging.getLogger("gridwise.guardrails")


class GuardrailValidationError(Exception):
    """Raised when an interpretation cannot be safely sanitized."""
    pass


def normalize_hours(raw_hours: Any) -> List[int]:
    """Ensure hours are unique integers in [0..23], returned in strictly ascending order."""
    if not isinstance(raw_hours, list):
        return []
    valid_hours = set()
    for h in raw_hours:
        try:
            h_int = int(h)
            if 0 <= h_int <= 23:
                valid_hours.add(h_int)
        except (ValueError, TypeError):
            continue
    return sorted(list(valid_hours))


def sanitize_directive_interpretation(
    raw_item: Dict[str, Any],
    note_index: int,
    operator_note: str,
    battery: Optional[BatteryInput] = None,
) -> DirectiveInterpretation:
    """Validate and sanitize a single raw directive interpretation dictionary.

    Guarantees that the resulting DirectiveInterpretation obeys all Problem Statement rules.
    If the raw output is invalid or inconsistent, safely corrects or falls back to no_op.
    """
    raw_type = str(raw_item.get("directive_type", "")).strip().lower()

    # Match allowed directive enum
    allowed_types = {e.value: e for e in DirectiveType}
    if raw_type not in allowed_types:
        logger.warning(
            f"Note {note_index}: Unknown directive_type '{raw_type}'. Falling back to no_op."
        )
        return DirectiveInterpretation(
            note_index=note_index,
            applies=False,
            directive_type=DirectiveType.NO_OP,
            structured_adjustment=None,
            explanation=f"Safely handled: '{operator_note[:60]}' did not produce a recognized directive.",
        )

    matched_type = allowed_types[raw_type]
    raw_adj = raw_item.get("structured_adjustment")
    raw_explanation = str(raw_item.get("explanation", "")).strip()
    if not raw_explanation:
        raw_explanation = f"Interpreted directive for note {note_index}."

    # Handle NO_OP
    if matched_type == DirectiveType.NO_OP or raw_adj is None:
        return DirectiveInterpretation(
            note_index=note_index,
            applies=False,
            directive_type=DirectiveType.NO_OP,
            structured_adjustment=None,
            explanation=raw_explanation or "This note does not affect today's energy schedule.",
        )

    if not isinstance(raw_adj, dict):
        logger.warning(f"Note {note_index}: Non-dict structured_adjustment for {matched_type}. Fallback to no_op.")
        return DirectiveInterpretation(
            note_index=note_index,
            applies=False,
            directive_type=DirectiveType.NO_OP,
            structured_adjustment=None,
            explanation="Invalid adjustment format; treated as non-operational.",
        )

    # Normalize hours
    hours = normalize_hours(raw_adj.get("hours", []))
    if not hours:
        logger.warning(f"Note {note_index}: Empty or invalid hours for {matched_type}. Fallback to no_op.")
        return DirectiveInterpretation(
            note_index=note_index,
            applies=False,
            directive_type=DirectiveType.NO_OP,
            structured_adjustment=None,
            explanation="Could not identify valid hours for this directive.",
        )

    # Validate specific directive schemas
    try:
        if matched_type == DirectiveType.SOLAR_REDUCTION:
            raw_factor = raw_adj.get("factor")
            if raw_factor is None:
                raise ValueError("Missing factor for solar_reduction")
            factor = float(raw_factor)
            # Factor must be in [0.0, 1.0]
            factor = max(0.0, min(1.0, factor))
            adjustment = SolarReductionAdjustment(hours=hours, factor=factor)

        elif matched_type == DirectiveType.MINIMUM_BATTERY_RESERVE:
            raw_reserve = raw_adj.get("minimum_energy_kwh")
            if raw_reserve is None:
                raise ValueError("Missing minimum_energy_kwh for minimum_battery_reserve")
            reserve = float(raw_reserve)
            if reserve < 0:
                reserve = 0.0
            if battery and reserve > battery.capacity_kwh:
                reserve = battery.capacity_kwh
            adjustment = MinimumBatteryReserveAdjustment(hours=hours, minimum_energy_kwh=reserve)

        elif matched_type == DirectiveType.NO_CHARGE_WINDOW:
            adjustment = NoChargeAdjustment(hours=hours)

        elif matched_type == DirectiveType.NO_DISCHARGE_WINDOW:
            adjustment = NoDischargeAdjustment(hours=hours)

        elif matched_type == DirectiveType.MAX_GRID_WINDOW:
            raw_cap = raw_adj.get("max_grid_kwh")
            if raw_cap is None:
                raise ValueError("Missing max_grid_kwh for max_grid_window")
            cap = max(0.0, float(raw_cap))
            adjustment = MaxGridAdjustment(hours=hours, max_grid_kwh=cap)

        else:
            return DirectiveInterpretation(
                note_index=note_index,
                applies=False,
                directive_type=DirectiveType.NO_OP,
                structured_adjustment=None,
                explanation="Non-operational note.",
            )

        return DirectiveInterpretation(
            note_index=note_index,
            applies=True,
            directive_type=matched_type,
            structured_adjustment=adjustment.model_dump(),
            explanation=raw_explanation,
        )

    except Exception as exc:
        logger.error(f"Note {note_index}: Guardrail sanitization failed with error: {exc}. Falling back to no_op.")
        return DirectiveInterpretation(
            note_index=note_index,
            applies=False,
            directive_type=DirectiveType.NO_OP,
            structured_adjustment=None,
            explanation=f"Sanitized to no_op due to validation error: {exc}",
        )


def validate_and_guardrail_directives(
    raw_interpretations: List[Dict[str, Any]],
    operator_notes: List[str],
    battery: Optional[BatteryInput] = None,
) -> List[DirectiveInterpretation]:
    """Ensure exactly one interpretation entry exists per operator note in note_index order (0..N-1)."""
    raw_by_index: Dict[int, Dict[str, Any]] = {}
    for idx, item in enumerate(raw_interpretations):
        if isinstance(item, dict):
            n_idx = item.get("note_index")
            if n_idx is not None and isinstance(n_idx, int) and 0 <= n_idx < len(operator_notes):
                raw_by_index[n_idx] = item
            elif idx < len(operator_notes) and idx not in raw_by_index:
                # Fallback to positional mapping
                raw_by_index[idx] = item

    results: List[DirectiveInterpretation] = []
    for note_idx, note_text in enumerate(operator_notes):
        raw_item = raw_by_index.get(note_idx, {
            "note_index": note_idx,
            "directive_type": "no_op",
            "applies": False,
            "structured_adjustment": None,
            "explanation": "No valid LLM interpretation returned.",
        })
        sanitized = sanitize_directive_interpretation(raw_item, note_idx, note_text, battery)
        results.append(sanitized)

    return results
