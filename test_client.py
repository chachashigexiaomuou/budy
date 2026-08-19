"""OpenCode Client 测试 — 先写这个，再实现 client。"""
import pytest
from opencode_client import OpenCodeClient

@pytest.fixture
def client():
    return OpenCodeClient("http://127.0.0.1:4096")

def test_health(client):
    result = client.health()
    assert result["healthy"] is True

def test_create_session(client):
    session = client.create_session()
    assert "id" in session
    assert session["id"].startswith("ses_")

def test_send_message(client):
    session = client.create_session()
    response = client.send_message(session["id"], "What is 2+2?")
    assert "parts" in response
    text_parts = [p["text"] for p in response["parts"] if p.get("type") == "text"]
    assert len(text_parts) > 0
    assert "4" in text_parts[0]

def test_multi_turn_context(client):
    session = client.create_session()
    client.send_message(session["id"], "My name is Alice")
    response = client.send_message(session["id"], "What is my name?")
    text_parts = [p["text"] for p in response["parts"] if p.get("type") == "text"]
    assert any("Alice" in t for t in text_parts)

def test_get_messages(client):
    session = client.create_session()
    client.send_message(session["id"], "Hello")
    messages = client.get_messages(session["id"])
    assert len(messages) >= 2  # user + assistant
