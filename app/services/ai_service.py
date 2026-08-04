import asyncio
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from agents import RunConfig, Runner, SessionSettings
from agents.extensions.memory import AsyncSQLiteSession
from agents.tool_context import ToolContext
from agents.usage import Usage
from app.agents.management_agent import management_agent
from app.agents.tools.analyze_flood_tool import get_gfm_flood_analysis
from app.core.config import settings
from app.services.geocoding_service import GeocodingError, geocode_place

logger = logging.getLogger(__name__)
SESSION_DIR = Path(settings.SESSION_DIR)
SESSION_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = str(SESSION_DIR / "conversations.db")

_FLOOD_TERMS = re.compile(r"\b(?:ngập|ngap|lụt|lut|flood)\b", re.IGNORECASE)
_DATE_PATTERN = re.compile(
    r"\b(?:(\d{4})[-/](\d{1,2})[-/](\d{1,2})|(\d{1,2})[-/](\d{1,2})[-/](\d{4}))\b"
)


def _extract_analysis_dates(input_text: str) -> tuple[str, str]:
    dates: list[str] = []
    for match in _DATE_PATTERN.finditer(input_text):
        year, month, day, day_first, month_first, year_last = match.groups()
        if year is None:
            year, month, day = year_last, month_first, day_first
        try:
            dates.append(date(int(year), int(month), int(day)).isoformat())
        except ValueError:
            continue

    if not dates:
        today = date.today().isoformat()
        return today, today
    if len(dates) == 1:
        return dates[0], dates[0]
    return dates[0], dates[1]


def _extract_place(input_text: str) -> str | None:
    without_dates = _DATE_PATTERN.sub(" ", input_text)
    location_match = re.search(
        r"\b(?:ở|o|tai|tại|in|at)\s+(.+?)(?=\s+(?:vào|trong|ngày|ngay|on|from)|[,.!?;]|$)",
        without_dates,
        flags=re.IGNORECASE,
    )
    candidate = location_match.group(1) if location_match else without_dates
    candidate = re.sub(
        r"\b(?:cho tôi|cho toi|thông tin|thong tin|mới nhất|moi nhat|phân tích|phan tich|"
        r"tình hình|tinh hinh|ngập|ngap|lụt|lut|flood|latest|current|recent|ngày|ngay)\b",
        " ",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(r"\s+", " ", candidate).strip(" ,.;:!?\"")
    return candidate if len(candidate) >= 2 else None


async def _invoke_flood_tool(bbox: str, start_date: str, end_date: str) -> Any:
    arguments = {
        "bbox": bbox,
        "start_date": start_date,
        "end_date": end_date,
    }
    context = ToolContext(
        None,
        usage=Usage(),
        tool_name=get_gfm_flood_analysis.name,
        tool_call_id="local-flood-analysis",
        tool_arguments=json.dumps(arguments),
    )
    return await get_gfm_flood_analysis.on_invoke_tool(context, json.dumps(arguments))


def _format_flood_result(
    result: Any,
    place: str,
    bbox: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {"status": "error", "message": result}
    if not isinstance(result, dict):
        result = {"status": "error", "message": "The flood-analysis service returned an invalid result."}

    status = result.get("status")
    analysis = result.get("analysis") or {}
    if status == "success" and analysis:
        flooded_days = analysis.get("flooded_days") or []
        response = (
            f"Phân tích ngập lụt tại {place} từ {start_date} đến {end_date}. "
            f"Số ngày có dấu hiệu ngập: {analysis.get('total_flooded_days', len(flooded_days))}. "
            f"Diện tích ngập lớn nhất: {analysis.get('total_max_flood_area_km2', 'không có dữ liệu')} km²."
        )
        if flooded_days:
            response += f" Các ngày ghi nhận: {', '.join(map(str, flooded_days))}."
    elif status == "no_data":
        response = (
            f"Không tìm thấy dữ liệu GFM về ngập lụt tại {place} "
            f"trong khoảng {start_date} đến {end_date}."
        )
    else:
        response = str(result.get("message") or "Không thể hoàn tất phân tích ngập lụt.")

    return {
        "analysis_type": result.get("analysis_type", "GFM Flood Analysis"),
        "area": bbox,
        "response": response,
        "tile_url": result.get("tile_url"),
        "legend": result.get("legend"),
        "visualizations": result.get("visualizations", []),
    }


async def _try_direct_flood_analysis(input_text: str) -> dict[str, Any] | None:
    if not _FLOOD_TERMS.search(input_text):
        return None

    place = _extract_place(input_text)
    if not place:
        return None

    start_date, end_date = _extract_analysis_dates(input_text)
    try:
        geocoded = await geocode_place(place)
        raw_result = await asyncio.wait_for(
            _invoke_flood_tool(geocoded["bbox"], start_date, end_date),
            timeout=120.0,
        )
        return _format_flood_result(raw_result, place, geocoded["bbox"], start_date, end_date)
    except GeocodingError as exc:
        return {
            "analysis_type": "Flood analysis",
            "area": "",
            "response": f"Không thể xác định khu vực '{place}': {exc}",
            "visualizations": [],
        }
    except asyncio.TimeoutError:
        logger.warning("Flood analysis timed out for %s", place)
        return {
            "analysis_type": "Flood analysis",
            "area": "",
            "response": "Phân tích ngập lụt mất quá nhiều thời gian. Vui lòng thử lại với khoảng thời gian ngắn hơn.",
            "visualizations": [],
        }
    except Exception:
        logger.exception("Direct flood analysis failed for %s", place)
        return {
            "analysis_type": "Flood analysis",
            "area": "",
            "response": "Không thể hoàn tất phân tích ngập lụt lúc này. Vui lòng thử lại sau.",
            "visualizations": [],
        }


async def process_response(input: str, session_id: str):
    direct_result = await _try_direct_flood_analysis(input)
    if direct_result is not None:
        return direct_result

    try:
        session = AsyncSQLiteSession(session_id, DB_PATH)
        logger.info("Processing input: %s for session_id: %s", input, session_id)

        try:
            result = await Runner.run(
                management_agent,
                input=input,
                session=session,
                max_turns=5,
                run_config=RunConfig(session_settings=SessionSettings(limit=50)),
            )
        except (httpx.RequestError, httpx.HTTPStatusError, TimeoutError) as net_err:
            logger.error("Network error while calling agent", exc_info=True)
            raise RuntimeError("Unable to connect to the AI service. Please try again.") from net_err

        raw_output = result.final_output
        if not raw_output:
            return "Error: the agent returned an empty response."

        try:
            return json.loads(raw_output)
        except json.JSONDecodeError:
            cleaned_text = re.sub(r"```(?:json)?\n?(.*?)\n?```", r"\1", raw_output, flags=re.DOTALL)
            start_idx = cleaned_text.find("{")
            end_idx = cleaned_text.rfind("}")
            if start_idx != -1 and end_idx != -1:
                try:
                    return json.loads(cleaned_text[start_idx : end_idx + 1])
                except json.JSONDecodeError:
                    pass
            return {"response": raw_output.strip()}
    except Exception:
        logger.exception("Error while getting response from agent")
        raise
