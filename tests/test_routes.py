"""
Route integration tests for FinAgent-Lithium.

Verifies all Flask routes respond correctly after Blueprint modularization.
Tests focus on route existence and basic response, not full pipeline logic.
"""
import json


def test_health_endpoint(client):
    """GET /health returns 200 with JSON containing 'status'."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "status" in data


def test_index_page(client):
    """GET / returns 200 with HTML containing 'FinAgent'."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"FinAgent" in resp.data


def test_history_page(client):
    """GET /history returns 200."""
    resp = client.get("/history")
    assert resp.status_code == 200


def test_ask_page(client):
    """GET /ask returns 200."""
    resp = client.get("/ask")
    assert resp.status_code == 200


def test_api_history(client):
    """GET /api/history returns 200 with JSON containing 'items'."""
    resp = client.get("/api/history")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "items" in data


def test_api_ask_requires_question(client):
    """POST /api/ask with empty body returns 400."""
    resp = client.post(
        "/api/ask",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_api_ask_requires_report_id(client):
    """POST /api/ask with question but no valid report_id returns 404."""
    resp = client.post(
        "/api/ask",
        data=json.dumps({"question": "test question", "report_id": "nonexistent"}),
        content_type="application/json",
    )
    assert resp.status_code == 404
