# ⚡ GridWise — Smart Campus Energy Optimization

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.14-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![Solver](https://img.shields.io/badge/Optimizer-HiGHS%20LP%20(SciPy)-orange.svg)](https://scipy.org/)
[![Status](https://img.shields.io/badge/Tests-20%2F20%20Passing-brightgreen.svg)]()
[![BUP CSE Fest 2026](https://img.shields.io/badge/Hackathon-BUP%20CSE%20Fest%202026-indigo.svg)](https://fest.bupcopc.tech)

An enterprise-grade, high-performance solution for the **BUP CSE Fest 2026 Hackathon (Online Preliminary Round)**: **LLM-Assisted Smart Campus Energy Scheduling and Optimization**.

GridWise bridges the gap between unstructured human natural-language operator notes and mathematically rigorous 24-hour cost-optimal energy scheduling across grid electricity, rooftop solar generation, and battery energy storage systems (BESS).

---

## 📑 Table of Contents
1. [Key Highlights & Architecture](#-key-highlights--architecture)
2. [Supported Directives & Semantic Rules](#-supported-directives--semantic-rules)
3. [Quickstart (Local Reproduction)](#-quickstart-local-reproduction)
4. [Docker Deployment](#-docker-deployment)
5. [API Endpoints & Curl Examples](#-api-endpoints--curl-examples)
6. [Testing & Verification Suite](#-testing--verification-suite)
7. [Mathematical Formulation](#-mathematical-formulation)
8. [Configuration & Environment Variables](#-configuration--environment-variables)
9. [Architecture & Tie-Break Video Script](#-architecture--tie-break-video-script)
10. [Security & Secret Handling](#-security--secret-handling)

---

## 🏛 Key Highlights & Architecture

The system operates as a strict four-stage pipeline: **Human notes are never directly trusted as math.**

```
                                  [HTTP POST /optimize-energy]
                                                │
                                                ▼
                                    [Pydantic Request Schema]
                                                │
                                                ▼
 ┌─────────────────────────────────────────────────────────────────────────────────────────────┐
 │ STAGE 1: LLM DIRECTIVE INTERPRETER                                                          │
 │ • Google Gemini 2.5 Flash / OpenAI GPT-4o-mini with structured JSON output                  │
 │ • Safe-Failure Local Semantic Fallback Engine (zero external downtime vulnerability)        │
 └──────────────────────────────────────┬──────────────────────────────────────────────────────┘
                                        │
                                        ▼
 ┌─────────────────────────────────────────────────────────────────────────────────────────────┐
 │ STAGE 2: DETERMINISTIC GUARDRAILS & SANITIZER                                               │
 │ • Validates 6 supported directive enums and 0..N-1 note coverage                            │
 │ • Enforces start-inclusive, end-exclusive ascending hour intervals in [0..23]               │
 │ • Sanitizes factor bounds [0.0..1.0] and battery reserve bounds [0..capacity_kwh]           │
 │ • Converts distractors / invalid models to safe no_op (applies=false, adjustment=null)      │
 └──────────────────────────────────────┬──────────────────────────────────────────────────────┘
                                        │
                                        ▼
 ┌─────────────────────────────────────────────────────────────────────────────────────────────┐
 │ STAGE 3: MATHEMATICAL LP OPTIMIZER (HiGHS Solver)                                           │
 │ • Formulates exact 24-hour linear program with 120 decision variables and 49 constraints     │
 │ • Solves in 2–5 ms with guaranteed mathematical global cost optimality                      │
 │ • Enforces hourly balance, solar curtailment bounds, battery limits & neutrality            │
 └──────────────────────────────────────┬──────────────────────────────────────────────────────┘
                                        │
                                        ▼
 ┌─────────────────────────────────────────────────────────────────────────────────────────────┐
 │ STAGE 4: SCHEDULE REPLAY & VERIFICATION                                                     │
 │ • Hour-by-hour physical simulation replaying all active constraints                        │
 │ • Exact recalculation of total_grid_kwh, total_cost_bdt, and peak_grid_kwh                  │
 │ • Zero-tolerance neutrality check: |E_after[23] - E_initial| <= 0.01 kWh                    │
 └──────────────────────────────────────┬──────────────────────────────────────────────────────┘
                                        │
                                        ▼
                           [HTTP 200 JSON Response + UI]
```

---

## 📋 Supported Directives & Semantic Rules

| Directive Type | Meaning | Structured Adjustment Shape |
| :--- | :--- | :--- |
| `solar_reduction` | Reduces usable solar during specific hours | `{"hours": [int...], "factor": number}` *(factor in [0.0, 1.0]; e.g., 80% reduction -> factor 0.2)* |
| `minimum_battery_reserve` | Raises battery reserve for specific hours | `{"hours": [int...], "minimum_energy_kwh": number}` |
| `no_charge_window` | Prohibits battery charging during listed hours | `{"hours": [int...]}` |
| `no_discharge_window` | Prohibits battery discharging during listed hours | `{"hours": [int...]}` |
| `max_grid_window` | Caps grid import during listed hours | `{"hours": [int...], "max_grid_kwh": number}` |
| `no_op` | Unrelated or distractor note | `null` *(applies MUST be false)* |

---

## 🚀 Quickstart (Local Reproduction)

Reproduce the entire application from a fresh environment in under 2 minutes:

### 1. Clone Repository & Create Virtual Environment
```bash
git clone https://github.com/tasin/gridwise-energy-optimization.git
cd gridwise-energy-optimization

python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure Environment
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
*(Note: If no API key is set, the service automatically runs in high-precision local fallback mode, guaranteeing 100% test pass rate without external credentials).*

### 4. Start the Service
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```
- Interactive Web Dashboard: **[http://localhost:8000](http://localhost:8000)**
- Swagger API Docs: **[http://localhost:8000/docs](http://localhost:8000/docs)**
- Health Endpoint: **[http://localhost:8000/health](http://localhost:8000/health)**

---

## 🐳 Docker Deployment

### Build and Run with Docker
```bash
# Build container image
docker build -t gridwise-api:latest .

# Run container binding to port 8000
docker run -d --name gridwise-service -p 8000:8000 gridwise-api:latest

# Verify health status
curl -i http://localhost:8000/health
```

### Run with Docker Compose
```bash
docker compose up --build -d
```

---

## 🌐 API Endpoints & Curl Examples

### 1. Health Readiness Endpoint
```bash
curl -X GET http://localhost:8000/health
```
**Response (HTTP 200):**
```json
{
  "status": "ok"
}
```

### 2. Energy Optimization Endpoint
```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "SAMPLE-01",
    "operator_notes": [
      "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
      "The sports office moved next months registration deadline."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 1, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 2, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 3, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 4, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 5, "demand_kwh": 95, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 6, "demand_kwh": 110, "solar_kwh": 5, "tariff_bdt_per_kwh": 8},
      {"hour": 7, "demand_kwh": 130, "solar_kwh": 20, "tariff_bdt_per_kwh": 10},
      {"hour": 8, "demand_kwh": 150, "solar_kwh": 50, "tariff_bdt_per_kwh": 12},
      {"hour": 9, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 10, "demand_kwh": 175, "solar_kwh": 130, "tariff_bdt_per_kwh": 16},
      {"hour": 11, "demand_kwh": 180, "solar_kwh": 160, "tariff_bdt_per_kwh": 16},
      {"hour": 12, "demand_kwh": 185, "solar_kwh": 180, "tariff_bdt_per_kwh": 15},
      {"hour": 13, "demand_kwh": 180, "solar_kwh": 170, "tariff_bdt_per_kwh": 14},
      {"hour": 14, "demand_kwh": 170, "solar_kwh": 140, "tariff_bdt_per_kwh": 13},
      {"hour": 15, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 16, "demand_kwh": 170, "solar_kwh": 45, "tariff_bdt_per_kwh": 18},
      {"hour": 17, "demand_kwh": 185, "solar_kwh": 10, "tariff_bdt_per_kwh": 22},
      {"hour": 18, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 28},
      {"hour": 19, "demand_kwh": 215, "solar_kwh": 0, "tariff_bdt_per_kwh": 30},
      {"hour": 20, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 26},
      {"hour": 21, "demand_kwh": 175, "solar_kwh": 0, "tariff_bdt_per_kwh": 18},
      {"hour": 22, "demand_kwh": 135, "solar_kwh": 0, "tariff_bdt_per_kwh": 10},
      {"hour": 23, "demand_kwh": 105, "solar_kwh": 0, "tariff_bdt_per_kwh": 7}
    ],
    "battery": {
      "capacity_kwh": 220,
      "initial_energy_kwh": 110,
      "minimum_energy_kwh": 40,
      "max_charge_kwh_per_hour": 50,
      "max_discharge_kwh_per_hour": 50
    }
  }'
```

**Response (HTTP 200):**
```json
{
  "scenario_id": "SAMPLE-01",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {
        "hours": [12, 13],
        "factor": 0.25
      },
      "explanation": "Solar output reduced to 25% usable during stated window."
    },
    {
      "note_index": 1,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": null,
      "explanation": "This note does not affect today's 24-hour energy schedule."
    }
  ],
  "hourly_plan": [ ... 24 hourly entries ... ],
  "total_grid_kwh": 2692.5,
  "total_cost_bdt": 38365.0,
  "peak_grid_kwh": 175.0,
  "plan_summary": "Optimized 24-hour schedule applying 1 active directive(s)..."
}
```

---

## 🧪 Testing & Verification Suite

Execute the complete test suite:
```bash
python -m pytest -v
```

### Test Coverage Summary
- **`tests/test_samples.py`**: Runs all 10 official sample cases from the competition pack:
  - Validates directive extraction against expected ground truth.
  - Verifies exact hourly balance, battery limits, rate bounds, and end-of-day neutrality.
  - Confirms total cost equals or outperforms organizer reference cost within 0.01 tolerance.
- **`tests/test_api.py`**: Tests `/health`, request validation, 400 error handlers, and `/api/sample-cases`.
- **`tests/test_guardrails.py`**: Tests hour window sorting, factor range clamping, reserve bounds, and malformed inputs.

---

## 📐 Mathematical Formulation

The energy scheduling problem is formulated as a Linear Program (LP):

$$\min \sum_{h=0}^{23} G_h \cdot \text{Tariff}_h + 10^{-7} (C_h + D_h) - 10^{-6} S^{\text{used}}_h$$

**Subject to:**
1. **Energy Balance**:
   $$G_h + S^{\text{used}}_h + D_h - C_h = \text{Demand}_h \quad \forall h \in [0..23]$$
2. **Solar Curtailment**:
   $$0 \le S^{\text{used}}_h \le S^{\text{eff}}_h \quad \forall h \in [0..23]$$
3. **Battery Dynamics**:
   $$E_0 = E^{\text{init}} + C_0 - D_0$$
   $$E_h = E_{h-1} + C_h - D_h \quad \forall h \in [1..23]$$
4. **State of Charge Bounds**:
   $$E^{\min}_h \le E_h \le E^{\text{cap}} \quad \forall h \in [0..23]$$
5. **Rate Limits**:
   $$0 \le C_h \le C^{\max}_h, \quad 0 \le D_h \le D^{\max}_h \quad \forall h \in [0..23]$$
6. **Feeder Capacity**:
   $$0 \le G_h \le G^{\max}_h \quad \forall h \in [0..23]$$
7. **End-of-Day Neutrality**:
   $$E_{23} = E^{\text{init}}$$

---

## ⚙️ Configuration & Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `HOST` | `0.0.0.0` | Host IP for uvicorn server |
| `PORT` | `8000` | Port for HTTP API |
| `LLM_PROVIDER` | `gemini` | Model provider (`gemini`, `openai`, `local_fallback`) |
| `GEMINI_API_KEY` | `""` | Google Gemini API key (optional) |
| `OPENAI_API_KEY` | `""` | OpenAI API key (optional) |
| `LLM_MODEL` | `gemini-2.5-flash`| Target model identifier |
| `ENABLE_LOCAL_FALLBACK` | `true` | Enables deterministic NLP engine if remote LLM fails |

---

## 🎬 Architecture & Tie-Break Video Script
The detailed 3-minute video script covering system architecture, live dashboard demo, and mathematical proof is provided in [`docs/VIDEO_SCRIPT.md`](docs/VIDEO_SCRIPT.md).

---

## 🔒 Security & Secret Handling
- **No Hardcoded Secrets**: All credentials are strictly read from environment variables.
- **Safe Error Masking**: API error responses never expose API keys, credentials, or raw Python stack traces.
- **Synthesized Data Only**: Uses only synthetic test data per challenge specifications.
