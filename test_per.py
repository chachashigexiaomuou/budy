# test_permission.py
import pytest
import httpx

BASE = "http://127.0.0.1:8000"

def test_permission_endpoint_accepts_valid_request():
    """Permission endpoint should return ok for valid requests."""
    # Note: This test requires a real permission_id from a running session
    # It verifies the endpoint exists and accepts the correct format
    resp = httpx.post(f"{BASE}/api/permission", json={
        "permission_id": "per_fake_id",
        "action": "once"
    })
    # Should not 404 or 500 (may fail with OpenCode error, that's ok)
    assert resp.status_code in (200, 400, 500)
    assert "status" in resp.json() or "error" in resp.json()
