import json
from types import SimpleNamespace

import httpx
import pytest

from app.main import app
from app.services import ai_service


@pytest.mark.asyncio
async def test_agent_response_handles_normal_chat_without_session_nameerror(
    monkeypatch, tmp_path
) -> None:
    user_input = "ベトナムの洪水に関する最新情報を教えてください。"
    expected = {
        "analysis_type": "General conversation",
        "area": "",
        "response": "The chat service is available.",
        "visualizations": [],
    }

    monkeypatch.setattr(ai_service, "DB_PATH", str(tmp_path / "conversations.sqlite3"))

    async def fake_runner(*args, **kwargs):
        assert kwargs["input"] == user_input
        assert kwargs["session"] is not None
        return SimpleNamespace(final_output=json.dumps(expected))

    monkeypatch.setattr(ai_service.Runner, "run", fake_runner)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/maps/agent-response",
            json={"input": user_input, "session_id": "test-session"},
        )

    assert response.status_code == 200
    assert response.json() == expected
