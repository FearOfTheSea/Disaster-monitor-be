from datetime import date, datetime

import numpy as np

from app.agents.tools import analyze_flood_tool
from app.services import ai_service


def test_extract_analysis_dates_supports_single_date_and_range() -> None:
    assert ai_service._extract_analysis_dates("flood in Hanoi on 04/08/2026") == (
        "2026-08-04",
        "2026-08-04",
    )
    assert ai_service._extract_analysis_dates(
        "flood in Hanoi from 2026-08-01 to 2026-08-04"
    ) == ("2026-08-01", "2026-08-04")


def test_extract_place_uses_the_location_phrase() -> None:
    assert ai_service._extract_place("latest flood in Hanoi Vietnam") == "Hanoi Vietnam"
    assert ai_service._extract_place("ベトナムの洪水に関する最新情報") == "Vietnam"
    assert ai_service._extract_place("ベトナム・ハノイの洪水") == "Hanoi Vietnam"


def test_language_preference_request_is_answered_in_requested_language() -> None:
    result = ai_service._try_language_preference_response("日本語で答えてもらえますか")

    assert result is not None
    assert result["response"] == "はい、日本語でお答えできます。ご質問をどうぞ。"


def test_language_preference_request_works_when_language_is_at_end() -> None:
    result = ai_service._try_language_preference_response("Can you answer in Japanese?")

    assert result is not None
    assert result["response"] == "はい、日本語でお答えできます。ご質問をどうぞ。"


def test_latest_without_explicit_dates_uses_a_recent_window() -> None:
    start_date, end_date = ai_service._extract_analysis_dates("latest flood in Hanoi")

    assert end_date == date.today().isoformat()
    assert (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days == 30


def test_format_flood_result_keeps_no_data_response_factual() -> None:
    result = ai_service._format_flood_result(
        {"status": "no_data", "analysis_type": "GFM Flood Analysis"},
        "Hanoi Vietnam",
        "105.1,20.8,105.9,21.4",
        "2026-08-04",
        "2026-08-04",
    )

    assert result["area"] == "105.1,20.8,105.9,21.4"
    assert result["visualizations"] == []
    assert "Hanoi Vietnam" in result["response"]
    assert "2026-08-04" in result["response"]
    assert "no data" not in result["response"].lower()


async def test_direct_flood_analysis_uses_geocoder_and_analysis_tool(monkeypatch) -> None:
    calls: dict[str, tuple[str, ...]] = {}

    async def fake_geocode(place: str) -> dict[str, str]:
        calls["geocode"] = (place,)
        return {"bbox": "105.1,20.8,105.9,21.4"}

    async def fake_invoke(bbox: str, start_date: str, end_date: str) -> dict[str, object]:
        calls["analysis"] = (bbox, start_date, end_date)
        return {
            "status": "success",
            "analysis_type": "GFM Flood Analysis",
            "analysis": {
                "flooded_days": ["2026-08-04"],
                "total_flooded_days": 1,
                "total_max_flood_area_km2": 2.5,
            },
        }

    monkeypatch.setattr(ai_service, "geocode_place", fake_geocode)
    monkeypatch.setattr(ai_service, "_invoke_flood_tool", fake_invoke)

    result = await ai_service._try_direct_flood_analysis(
        "latest flood in Hanoi Vietnam on 2026-08-04"
    )

    assert result is not None
    assert calls["geocode"] == ("Hanoi Vietnam",)
    assert calls["analysis"] == ("105.1,20.8,105.9,21.4", "2026-08-04", "2026-08-04")
    assert "Hanoi Vietnam" in result["response"]


async def test_non_flood_chat_falls_back_to_agent_path() -> None:
    assert await ai_service._try_direct_flood_analysis("Introduce yourself") is None


def test_wms_fallback_aggregates_flood_dates(monkeypatch) -> None:
    class FakeItem:
        def __init__(self, observation_date: str) -> None:
            self.datetime = datetime.fromisoformat(observation_date)

    responses = {
        "2026-07-25": np.array([[False, False], [False, False]]),
        "2026-07-26": np.array([[False, True], [False, False]]),
    }

    def fake_fetch(observation_date, bbox, width, height):
        return observation_date, responses[observation_date], 1.5

    monkeypatch.setattr(analyze_flood_tool, "_fetch_wms_observation", fake_fetch)

    result = analyze_flood_tool._gfm_wms_worker(
        [FakeItem("2026-07-25"), FakeItem("2026-07-26")],
        [105.0, 20.0, 106.0, 21.0],
    )

    assert result["source"] == "Copernicus EMS GFM via WMS"
    assert result["area_is_estimate"] is True
    assert result["analysis"]["flooded_days"] == ["2026-07-26"]
    assert result["analysis"]["total_max_flood_area_km2"] == 1.5
