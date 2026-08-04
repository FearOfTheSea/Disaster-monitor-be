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
