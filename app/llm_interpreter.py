"""LLM-Assisted Operator Directive Interpreter.

Interprets natural-language campus operator notes into structured machine-checkable
directives complying with BUP CSE Fest 2026 Problem Statement Section 04.

Architecture:
1. Primary Layer: Calls generative language model (Google Gemini or OpenAI-compatible)
   with strict JSON schema output and few-shot calibration.
2. Fallback Layer: Deterministic local semantic / regex NLP engine that parses
   times, percentages, battery reserve fractions, and feeder limits when no API key
   is supplied or external API calls fail / time out.
3. Guardrail Hook: Raw interpretations are piped directly through app.guardrails
   to ensure 100% schema correctness and zero silent failures.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from app.guardrails import validate_and_guardrail_directives
from app.schemas import (
    BatteryInput,
    DirectiveInterpretation,
    DirectiveType,
)

logger = logging.getLogger("gridwise.llm")

# System prompt defining exact challenge semantics
SYSTEM_PROMPT = """You are the GridWise Operator Directive Interpreter for the BUP CSE Fest 2026 Hackathon.
Your task is to analyze 1 to 3 campus operator notes and translate each note into a structured directive.

For each note, return a JSON object with:
- note_index: integer (0, 1, ..., N-1)
- directive_type: ONE OF ["solar_reduction", "minimum_battery_reserve", "no_charge_window", "no_discharge_window", "max_grid_window", "no_op"]
- applies: boolean (TRUE for all directives, FALSE ONLY for no_op)
- structured_adjustment: object or null:
    - for "solar_reduction": {"hours": [int...], "factor": float}
        NOTE: factor is the USABLE FRACTION REMAINING (e.g., 80% reduction means factor=0.2; 25% usable means factor=0.25).
    - for "minimum_battery_reserve": {"hours": [int...], "minimum_energy_kwh": float}
        NOTE: if percentage is specified (e.g., 50% of capacity 200 kWh), calculate minimum_energy_kwh = 100.0.
    - for "no_charge_window": {"hours": [int...]}
    - for "no_discharge_window": {"hours": [int...]}
    - for "max_grid_window": {"hours": [int...], "max_grid_kwh": float}
    - for "no_op": null (MUST be null when applies is false)
- explanation: string (short, concise explanation of the rationale)

CRITICAL RULES:
1. Time windows use whole-hour intervals with START-INCLUSIVE and END-EXCLUSIVE:
   - "1 PM to 3 PM" -> hours [13, 14]
   - "noon until 2 PM" -> hours [12, 13]
   - "2 AM until 5 AM" -> hours [2, 3, 4]
   - "6 PM until 9 PM" -> hours [18, 19, 20]
   - "10 AM until noon" -> hours [10, 11]
   - "11 AM until 1 PM" -> hours [11, 12]
   - "6 PM until 10 PM" -> hours [18, 19, 20, 21]
   - "7 PM until 9 PM" -> hours [19, 20]
   - "7 PM until 10 PM" -> hours [19, 20, 21]
2. Hours array inside structured_adjustment must contain UNIQUE integers in strictly ASCENDING order.
3. Unrelated or distractor notes (e.g. sports, cafeteria, registration, library book return, seminar room booking, club notices) MUST be mapped to directive_type "no_op", with applies=false and structured_adjustment=null.
4. Output must be valid JSON conforming to the schema.
"""


def _parse_hour_token(token: str, default_period: Optional[str] = None) -> Optional[int]:
    """Convert a time token like 'noon', '1 PM', '13:00', '2', 'midnight' into integer hour 0-23."""
    t = token.strip().lower()
    if t == "noon" or t == "12 noon":
        return 12
    if t == "midnight" or t == "12 midnight":
        return 0

    word_to_num = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12,
    }
    for word, num in word_to_num.items():
        if t.startswith(word):
            t = t.replace(word, str(num))

    # Match "13:00" or "13"
    match_24 = re.match(r"^(\d{1,2})(?::00)?$", t)
    if match_24:
        val = int(match_24.group(1))
        if default_period == "pm" and val < 12:
            val += 12
        elif default_period == "am" and val == 12:
            val = 0
        return val if 0 <= val <= 24 else None

    # Match "2 pm", "2:00 am"
    match_12 = re.match(r"^(\d{1,2})(?::00)?\s*(am|pm)$", t)
    if match_12:
        val = int(match_12.group(1))
        meridiem = match_12.group(2)
        if meridiem == "pm" and val < 12:
            val += 12
        elif meridiem == "am" and val == 12:
            val = 0
        return val if 0 <= val <= 24 else None

    return None


def extract_time_window(text: str) -> List[int]:
    """Extract whole-hour start-inclusive, end-exclusive time interval [start, end)."""
    text_clean = text.replace("–", "-").replace("—", "-")

    # Patterns:
    # 1. "from X until Y", "between X and Y", "from X to Y"
    pattern1 = r"(?:from|between)\s+([a-zA-Z0-9:\s]+?)\s+(?:until|to|and)\s+([a-zA-Z0-9:\s]+?)(?:[\.,;]|\s+for|\s+during|\s+because|\s+while|\s+next|\s+with|$)"
    m1 = re.search(pattern1, text_clean, re.IGNORECASE)
    if m1:
        start_str = m1.group(1).strip()
        end_str = m1.group(2).strip()

        # Infer AM/PM from end if missing from start
        end_period = "pm" if "pm" in end_str.lower() else ("am" if "am" in end_str.lower() else None)
        start_period = "pm" if "pm" in start_str.lower() else ("am" if "am" in start_str.lower() else None)

        if not start_period and end_period:
            # e.g., "1 PM to 3 PM" or "noon until 2 PM" or "1 until 3 PM"
            if "noon" in start_str.lower():
                start_h = 12
            else:
                start_h = _parse_hour_token(start_str, default_period=end_period)
        else:
            start_h = _parse_hour_token(start_str)

        end_h = _parse_hour_token(end_str)

        if start_h is not None and end_h is not None and start_h < end_h:
            return list(range(start_h, end_h))

    # 2. "1-3 PM maintenance window" or "10:00-12:00"
    pattern2 = r"(\d{1,2})\s*-\s*(\d{1,2})\s*(am|pm)?"
    m2 = re.search(pattern2, text_clean, re.IGNORECASE)
    if m2:
        s_val = int(m2.group(1))
        e_val = int(m2.group(2))
        period = m2.group(3).lower() if m2.group(3) else None
        if period == "pm":
            if s_val < 12:
                s_val += 12
            if e_val < 12:
                e_val += 12
        if s_val < e_val and 0 <= s_val <= 24 and 0 <= e_val <= 24:
            return list(range(s_val, e_val))

    return []


def local_semantic_parse_note(
    note_text: str,
    note_index: int,
    battery: Optional[BatteryInput] = None,
) -> Dict[str, Any]:
    """Deterministic local semantic engine to interpret notes when LLM API is offline."""
    lower = note_text.lower()

    # Detect obvious non-operational / distractor topics
    distractor_keywords = [
        "registration deadline", "cafeteria menu", "book-return", "library",
        "seminar room", "club notices", "sports office", "student affairs",
        "next month", "next week", "tomorrow", "published",
    ]
    is_distractor = any(kw in lower for kw in distractor_keywords)
    energy_keywords = ["solar", "battery", "charger", "charging", "discharge", "grid", "feeder", "transformer"]
    has_energy_kw = any(kw in lower for kw in energy_keywords)

    if is_distractor and not has_energy_kw:
        return {
            "note_index": note_index,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "This note does not affect today's 24-hour energy schedule.",
        }

    hours = extract_time_window(note_text)

    # 1. Solar reduction directive
    if any(k in lower for k in ["solar", "panel", "rooftop", "pv"]):
        # Determine factor (remaining usable solar)
        factor = 0.5  # default
        m_pct_red = re.search(r"(\d{1,2})%\s*reduction", lower)
        if m_pct_red:
            pct = float(m_pct_red.group(1))
            factor = round((100.0 - pct) / 100.0, 4)
        else:
            m_pct_usable = re.search(r"(?:about|roughly|to)\s*(\d{1,2})%", lower)
            if m_pct_usable:
                factor = round(float(m_pct_usable.group(1)) / 100.0, 4)
            elif "one-fifth" in lower:
                factor = 0.2
            elif "half" in lower:
                factor = 0.5
            elif "quarter" in lower or "one-fourth" in lower:
                factor = 0.25

        if hours:
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {
                    "hours": hours,
                    "factor": factor,
                },
                "explanation": f"Solar output reduced to {factor * 100:.0f}% usable during stated window.",
            }

    # 2. No Charge Window
    if any(k in lower for k in ["charger", "charging"]) and any(k in lower for k in ["isolate", "maintenance", "unavailable", "disabled", "not charge", "do not charge"]):
        if hours:
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "no_charge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": "Battery charging is disabled during this window.",
            }

    # 3. No Discharge Window
    if any(k in lower for k in ["discharge", "discharging"]) and any(k in lower for k in ["not discharge", "do not discharge", "disabled", "testing"]):
        if hours:
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "no_discharge_window",
                "structured_adjustment": {"hours": hours},
                "explanation": "Battery discharging is disabled during this window.",
            }

    # 4. Minimum Battery Reserve
    if any(k in lower for k in ["reserve", "remain in the battery", "stored in the battery", "keep at least"]):
        min_kwh = 0.0
        # Check percentage of capacity
        m_cap_pct = re.search(r"(\d{1,2})%\s*of\s*(?:the\s*)?battery\s*capacity", lower)
        if m_cap_pct and battery:
            pct = float(m_cap_pct.group(1)) / 100.0
            min_kwh = pct * battery.capacity_kwh
        else:
            m_kwh = re.search(r"(\d+(?:\.\d+)?)\s*kwh", lower)
            if m_kwh:
                min_kwh = float(m_kwh.group(1))

        if hours and min_kwh > 0:
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "minimum_battery_reserve",
                "structured_adjustment": {
                    "hours": hours,
                    "minimum_energy_kwh": min_kwh,
                },
                "explanation": f"Required battery reserve of {min_kwh:.1f} kWh maintained.",
            }

    # 5. Max Grid Window
    if any(k in lower for k in ["grid import", "grid intake", "feeder", "transformer"]):
        m_grid = re.search(r"(?:exceed|below|at|limit is)\s*(\d+(?:\.\d+)?)\s*kwh", lower)
        if not m_grid:
            m_grid = re.search(r"(\d+(?:\.\d+)?)\s*kwh\s*of\s*grid", lower)

        if m_grid and hours:
            cap_kwh = float(m_grid.group(1))
            return {
                "note_index": note_index,
                "applies": True,
                "directive_type": "max_grid_window",
                "structured_adjustment": {
                    "hours": hours,
                    "max_grid_kwh": cap_kwh,
                },
                "explanation": f"Grid import is capped at {cap_kwh:.1f} kWh.",
            }

    # Default fallback: no_op
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": "This note does not affect today's 24-hour energy schedule.",
    }


def call_gemini_api(
    operator_notes: List[str],
    battery: Optional[BatteryInput],
    api_key: str,
    model_name: str = "gemini-2.5-flash",
) -> List[Dict[str, Any]]:
    """Invoke Google Gemini using the official google-genai SDK."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    user_content = {
        "battery": battery.model_dump() if battery else None,
        "operator_notes": [{"note_index": i, "text": note} for i, note in enumerate(operator_notes)],
    }

    prompt_text = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Input Data:\n{json.dumps(user_content, indent=2)}\n\n"
        f"Respond with a JSON array of {len(operator_notes)} objects, in note_index order."
    )

    response = client.models.generate_content(
        model=model_name,
        contents=prompt_text,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )

    raw_text = response.text or ""
    data = json.loads(raw_text)
    if isinstance(data, dict) and "directives" in data:
        data = data["directives"]
    if isinstance(data, list):
        return data
    raise ValueError("Gemini response was not a JSON list")


def call_openai_api(
    operator_notes: List[str],
    battery: Optional[BatteryInput],
    api_key: str,
    base_url: Optional[str] = None,
    model_name: str = "gpt-4o-mini",
) -> List[Dict[str, Any]]:
    """Invoke OpenAI or compatible endpoint."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)

    user_content = {
        "battery": battery.model_dump() if battery else None,
        "operator_notes": [{"note_index": i, "text": note} for i, note in enumerate(operator_notes)],
    }

    completion = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Input Data:\n{json.dumps(user_content, indent=2)}\nReturn a JSON array of {len(operator_notes)} objects in note_index order.",
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )

    content = completion.choices[0].message.content or "{}"
    parsed = json.loads(content)
    if isinstance(parsed, list):
        return parsed
    for key in ["directives", "directive_interpretation", "results", "notes"]:
        if key in parsed and isinstance(parsed[key], list):
            return parsed[key]
    raise ValueError("OpenAI response did not contain a list of directives")


def interpret_operator_notes(
    operator_notes: List[str],
    battery: Optional[BatteryInput] = None,
) -> List[DirectiveInterpretation]:
    """End-to-end interpretation pipeline with LLM call and deterministic fallback."""
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    raw_interpretations: List[Dict[str, Any]] = []

    # Attempt LLM call if credentials are available
    if provider == "gemini" and gemini_key:
        model_name = os.getenv("LLM_MODEL", "gemini-2.5-flash")
        try:
            logger.info(f"Calling Gemini ({model_name}) for note interpretation...")
            raw_interpretations = call_gemini_api(operator_notes, battery, gemini_key, model_name)
        except Exception as exc:
            logger.warning(f"Gemini API call failed: {exc}. Engaging deterministic local fallback.")

    elif provider == "openai" and openai_key:
        model_name = os.getenv("LLM_MODEL", "gpt-4o-mini")
        base_url = os.getenv("OPENAI_BASE_URL")
        try:
            logger.info(f"Calling OpenAI ({model_name}) for note interpretation...")
            raw_interpretations = call_openai_api(operator_notes, battery, openai_key, base_url, model_name)
        except Exception as exc:
            logger.warning(f"OpenAI API call failed: {exc}. Engaging deterministic local fallback.")

    # Fallback to local semantic engine if no output received
    if not raw_interpretations:
        logger.info("Using deterministic local semantic engine for note interpretation.")
        raw_interpretations = [
            local_semantic_parse_note(note, idx, battery)
            for idx, note in enumerate(operator_notes)
        ]

    # Validate and guardrail through deterministic layer
    sanitized_interpretations = validate_and_guardrail_directives(
        raw_interpretations=raw_interpretations,
        operator_notes=operator_notes,
        battery=battery,
    )

    return sanitized_interpretations
