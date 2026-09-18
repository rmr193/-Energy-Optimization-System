"""API Integration and Contract tests."""

import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)
DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_cases.json"


def test_health_endpoint():
    """Section 06: GET /health returns HTTP 200 with status='ok'."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_optimize_energy_sample_01():
    """Test POST /optimize-energy with SAMPLE-01."""
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    sample_01 = cases[0]["input"]

    response = client.post("/optimize-energy", json=sample_01)
    assert response.status_code == 200
    data = response.json()

    assert data["scenario_id"] == "SAMPLE-01"
    assert len(data["directive_interpretation"]) == 2
    assert len(data["hourly_plan"]) == 24
    assert data["total_cost_bdt"] > 0
    assert data["total_grid_kwh"] > 0
    assert data["peak_grid_kwh"] > 0
    assert "plan_summary" in data


def test_optimize_energy_invalid_hours_length():
    """Verify malformed/incomplete hours array triggers HTTP 400."""
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        sample_input = json.load(f)["cases"][0]["input"].copy()

    # Shorten hours array to 20 entries
    sample_input["hours"] = sample_input["hours"][:20]

    response = client.post("/optimize-energy", json=sample_input)
    assert response.status_code == 400
    err_body = response.json()
    assert "error" in err_body


def test_optimize_energy_empty_notes():
    """Verify empty notes list triggers HTTP 400."""
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        sample_input = json.load(f)["cases"][0]["input"].copy()

    sample_input["operator_notes"] = []
    response = client.post("/optimize-energy", json=sample_input)
    assert response.status_code == 400


def test_api_sample_cases_endpoint():
    """Verify /api/sample-cases returns the public test cases."""
    response = client.get("/api/sample-cases")
    assert response.status_code == 200
    data = response.json()
    assert "cases" in data
    assert len(data["cases"]) == 10
