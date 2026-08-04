import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import ai_service


@pytest.mark.asyncio
async def test_agent_response_handles_normal_chat_without_session_nameerror(
    monkeypatch, tmp_path, api_client
) -> None:
    user_input = "ベトナムの洪水に関する最新情報を教えてください。"
    expected = {
        "analysis_type": "General conversation",
        "area": "",
        "response": "The chat service is available.",
        "visualizations": [],
    }

    async def no_direct_flood_analysis(_input: str):
        return None

    monkeypatch.setattr(ai_service, "_try_direct_flood_analysis", no_direct_flood_analysis)
    monkeypatch.setattr(ai_service, "DB_PATH", str(tmp_path / "conversations.sqlite3"))

    async def fake_runner(*args, **kwargs):
        assert user_input in kwargs["input"]
        assert kwargs["session"] is not None
        return SimpleNamespace(final_output=json.dumps(expected))

    monkeypatch.setattr(ai_service.Runner, "run", fake_runner)

    response = await api_client.post(
        "/api/v1/maps/agent-response",
        json={"input": user_input, "session_id": "test-session"},
    )

    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.asyncio
async def test_agent_response_routes_japanese_hanoi_flood_request_to_analysis(
    monkeypatch, api_client
) -> None:
    async def fake_geocode(place: str) -> dict[str, str]:
        assert place == "Hanoi Vietnam"
        return {"bbox": "105.28,20.56,106.02,21.39"}

    async def fake_invoke(bbox: str, start_date: str, end_date: str) -> dict[str, object]:
        assert bbox == "105.28,20.56,106.02,21.39"
        assert start_date < end_date
        return {
            "status": "success",
            "source": "Copernicus EMS GFM via WMS",
            "analysis": {
                "total_flooded_days": 1,
                "flooded_days": ["2026-07-26"],
                "total_max_flood_area_km2": 14.31,
            },
        }

    monkeypatch.setattr(ai_service, "geocode_place", fake_geocode)
    monkeypatch.setattr(ai_service, "_invoke_flood_tool", fake_invoke)

    response = await api_client.post(
        "/api/v1/maps/agent-response",
        json={
            "input": "ベトナム・ハノイの洪水に関する最新情報を教えてください。",
            "session_id": "japanese-flood-session",
        },
    )

    assert response.status_code == 200
    assert response.json()["area"] == "105.28,20.56,106.02,21.39"
    assert "2026-07-26" in response.json()["response"]


@pytest.mark.asyncio
async def test_agent_response_hides_model_reasoning_from_user(monkeypatch, api_client) -> None:
    raw_output = """
Okay, let's handle this.
The user wants Japanese.
No tool calls are needed here.

Final response:
"はい、日本語で回答できます。ご質問をどうぞ。"
"""

    async def no_direct_flood_analysis(_input: str):
        return None

    async def fake_runner(*args, **kwargs):
        return SimpleNamespace(final_output=raw_output)

    monkeypatch.setattr(ai_service, "_try_direct_flood_analysis", no_direct_flood_analysis)
    monkeypatch.setattr(ai_service.Runner, "run", fake_runner)

    response = await api_client.post(
        "/api/v1/maps/agent-response",
        json={"input": "今日は元気ですか", "session_id": "language-session"},
    )

    assert response.status_code == 200
    assert response.json() == {"response": "はい、日本語で回答できます。ご質問をどうぞ。"}


@pytest.mark.asyncio
async def test_agent_response_preserves_analysis_visualizations(monkeypatch, api_client) -> None:
    expected = {
        "analysis_type": "RGB analysis",
        "area": "105,20,106,21",
        "response": "The comparison is complete.",
        "tile_url": "https://example.test/tiles.json",
        "visualizations": [{"label": "Before", "image_url": "https://example.test/before.png"}],
    }

    async def no_direct_flood_analysis(_input: str):
        return None

    async def fake_runner(*args, **kwargs):
        return SimpleNamespace(final_output=json.dumps(expected))

    monkeypatch.setattr(ai_service, "_try_direct_flood_analysis", no_direct_flood_analysis)
    monkeypatch.setattr(ai_service.Runner, "run", fake_runner)

    response = await api_client.post(
        "/api/v1/maps/agent-response",
        json={"input": "Compare these satellite images.", "session_id": "vision-session"},
    )

    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.asyncio
async def test_agent_response_validates_request_contract(api_client) -> None:
    response = await api_client.post(
        "/api/v1/maps/agent-response",
        json={"input": "Hello"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_agent_response_returns_safe_error_when_model_is_unreachable(
    monkeypatch, api_client
) -> None:
    async def no_direct_flood_analysis(_input: str):
        return None

    async def failed_runner(*args, **kwargs):
        raise httpx.RequestError(
            "Ollama is offline",
            request=httpx.Request("POST", "http://ollama.test/v1/chat/completions"),
        )

    monkeypatch.setattr(ai_service, "_try_direct_flood_analysis", no_direct_flood_analysis)
    monkeypatch.setattr(ai_service.Runner, "run", failed_runner)

    response = await api_client.post(
        "/api/v1/maps/agent-response",
        json={"input": "Hello", "session_id": "offline-session"},
    )

    assert response.status_code == 500
    assert "Unable to connect to the AI service" in response.json()["detail"]
