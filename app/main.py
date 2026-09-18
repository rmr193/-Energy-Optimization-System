"""FastAPI Application for GridWise Smart Campus Energy Scheduling and Optimization.

Endpoints:
- GET  /health           : Readiness endpoint (HTTP 200 {"status": "ok"})
- POST /optimize-energy  : Main optimization endpoint (Section 06, 07, 10)
- GET  /                 : Interactive Master-Level Web Dashboard UI
- GET  /api/sample-cases : Endpoint to load official public sample cases into UI
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.guardrails import GuardrailValidationError
from app.llm_interpreter import interpret_operator_notes
from app.optimizer import OptimizationError, solve_schedule_lp
from app.replay_validator import ReplayValidationError, replay_and_validate_schedule
from app.schemas import (
    HealthResponse,
    OptimizeEnergyRequest,
    OptimizeEnergyResponse,
)

# Load environment configuration
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gridwise.main")

# Initialize FastAPI App
app = FastAPI(
    title="GridWise — Smart Campus Energy Optimization",
    version="2.0.0",
    description="LLM-Assisted Operator Directive Interpretation & 24-Hour Energy Scheduling",
)

# CORS middleware for open accessibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR.parent / "data"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _load_sample_openapi_examples() -> Dict[str, Any]:
    sample_file = DATA_DIR / "sample_cases.json"
    examples: Dict[str, Any] = {}
    if sample_file.exists():
        try:
            with open(sample_file, "r", encoding="utf-8") as f:
                cases = json.load(f).get("cases", [])
                for c in cases:
                    cid = c.get("id", "SAMPLE")
                    label = c.get("label", "")
                    inp = c.get("input")
                    if inp:
                        examples[cid] = {
                            "summary": f"{cid}: {label}",
                            "description": f"Benchmark scenario {cid} ({label})",
                            "value": inp,
                        }
        except Exception as e:
            logger.warning(f"Could not load openapi examples from {sample_file}: {e}")
    return examples


SAMPLE_OPENAPI_EXAMPLES = _load_sample_openapi_examples()


# ------------------------------------------------------------------------------
# Exception Handlers (Section 06.1)
# ------------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle malformed or structurally invalid requests with HTTP 400."""
    logger.warning(f"Request validation error: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": "Malformed or structurally invalid request",
            "details": [
                {"loc": err.get("loc"), "msg": err.get("msg"), "type": err.get("type")}
                for err in exc.errors()
            ],
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Safe internal error handler without leaking secret keys or raw stack traces."""
    logger.error(f"Controlled internal error on {request.url.path}: {exc}", exc_info=False)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "An internal error occurred while processing the energy schedule",
            "message": str(exc),
        },
    )


# ------------------------------------------------------------------------------
# Core Judging Endpoints (Section 06)
# ------------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def health_check() -> HealthResponse:
    """Readiness endpoint for the judging harness."""
    return HealthResponse(status="ok")


@app.post(
    "/optimize-energy",
    response_model=OptimizeEnergyResponse,
    status_code=status.HTTP_200_OK,
    summary="Optimize 24-Hour Energy Dispatch",
    description="Main LLM interpretation + 24-hour LP optimization endpoint. Select an official scenario from the Examples dropdown or submit custom operator notes.",
)
async def optimize_energy(
    request: OptimizeEnergyRequest = Body(
        ...,
        openapi_examples=SAMPLE_OPENAPI_EXAMPLES,
    )
) -> OptimizeEnergyResponse:
    """Main LLM interpretation + 24-hour optimization endpoint.

    Flow:
    1. Parse and validate scenario request schema.
    2. Pass operator_notes to LLM (with fallback) and sanitize directives.
    3. Solve cost-optimal 24-hour schedule via HiGHS Linear Programming.
    4. Independently replay and verify energy balance, battery state, and bounds.
    5. Return complete structured response.
    """
    logger.info(f"Processing scenario: {request.scenario_id} with {len(request.operator_notes)} notes")

    # Step 1: LLM Interpretation & Guardrail Validation
    try:
        directives = interpret_operator_notes(
            operator_notes=request.operator_notes,
            battery=request.battery,
        )
    except Exception as exc:
        logger.error(f"Directive interpretation failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to interpret operator notes safely.",
        )

    # Step 2: Mathematical LP Optimization
    try:
        hourly_plan = solve_schedule_lp(
            hours=request.hours,
            battery=request.battery,
            directives=directives,
        )
    except OptimizationError as exc:
        logger.error(f"Optimization error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Optimization failed: {exc}",
        )

    # Step 3: Schedule Replay & Verification
    try:
        total_grid_kwh, total_cost_bdt, peak_grid_kwh, plan_summary = replay_and_validate_schedule(
            hourly_plan=hourly_plan,
            hours=request.hours,
            battery=request.battery,
            directives=directives,
        )
    except ReplayValidationError as exc:
        logger.error(f"Replay validation error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Replay validation failed: {exc}",
        )

    response = OptimizeEnergyResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=directives,
        hourly_plan=hourly_plan,
        total_grid_kwh=total_grid_kwh,
        total_cost_bdt=total_cost_bdt,
        peak_grid_kwh=peak_grid_kwh,
        plan_summary=plan_summary,
    )

    logger.info(
        f"Scenario {request.scenario_id} solved: "
        f"Grid={total_grid_kwh:.1f} kWh, Cost={total_cost_bdt:.1f} BDT, Peak={peak_grid_kwh:.1f} kWh"
    )
    return response


# ------------------------------------------------------------------------------
# Dashboard & Developer Support Endpoints
# ------------------------------------------------------------------------------

@app.get("/", response_class=FileResponse)
async def dashboard():
    """Interactive Master-Level Visual Dashboard."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse(content={"message": "GridWise API is running. Visit /health or POST to /optimize-energy."})


@app.get("/api/sample-cases")
async def get_sample_cases():
    """Serve the 10 official public sample cases to the web dashboard."""
    sample_file = DATA_DIR / "sample_cases.json"
    if sample_file.exists():
        with open(sample_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return JSONResponse(status_code=404, content={"error": "sample_cases.json not found"})
