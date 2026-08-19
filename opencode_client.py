import httpx


class OpenCodeClient:
    def __init__(self, base_url: str = "http://127.0.0.1:4096"):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=120.0,
        )
        self._directory = "/home/participant/lab"

    def health(self) -> dict:
        r = self._client.get("/api/health")
        r.raise_for_status()
        return r.json()

    def create_session(self) -> dict:
        r = self._client.post(
            "/session",
            params={"directory": self._directory},
            json={},
        )
        r.raise_for_status()
        return r.json()

    def send_message(self, session_id: str, text: str) -> dict:
        r = self._client.post(
            f"/session/{session_id}/message",
            params={"directory": self._directory},
            json={"parts": [{"type": "text", "text": text}]},
        )
        r.raise_for_status()
        return r.json()

    def get_messages(self, session_id: str) -> list:
        r = self._client.get(
            f"/session/{session_id}/message",
            params={"directory": self._directory},
        )
        r.raise_for_status()
        result = r.json()
        return result["data"] if isinstance(result, dict) else result
