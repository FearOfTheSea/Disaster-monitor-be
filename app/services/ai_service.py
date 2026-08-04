import asyncio
import json
import logging
import re
from datetime import date, timedelta
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

_FLOOD_TERMS = re.compile(r"\b(?:ngập|ngap|lụt|lut|flood)\b|洪水|浸水", re.IGNORECASE)
_DATE_PATTERN = re.compile(
    r"\b(?:(\d{4})[-/](\d{1,2})[-/](\d{1,2})|(\d{1,2})[-/](\d{1,2})[-/](\d{4}))\b"
)
_LATEST_LOOKBACK_DAYS = 30
_JAPANESE_TEXT_PATTERN = re.compile(r"[\u3040-\u30ff]")
_LANGUAGE_KEYWORD_PATTERN = re.compile(
    r"日本語|tiếng nhật|japanese|tiếng anh|english|tiếng việt|vietnamese",
    re.IGNORECASE,
)
_LANGUAGE_ACTION_PATTERN = re.compile(
    r"答|回答|返|話|trả lời|phản hồi|answer|respond|reply",
    re.IGNORECASE,
)
_REASONING_TAG_PATTERN = re.compile(
    r"<(?P<tag>think|thinking|analysis|reasoning)>.*?</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_FINAL_RESPONSE_MARKER = re.compile(
    r"^\s*(?:final\s+(?:response|answer)|answer|response|final)\s*:\s*",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_agent_reasoning(text: str) -> str:
    """Keep only user-facing prose from a model response."""
    cleaned = _REASONING_TAG_PATTERN.sub("", text).strip()
    markers = list(_FINAL_RESPONSE_MARKER.finditer(cleaned))
    if markers:
        cleaned = cleaned[markers[-1].end() :].strip()

    cleaned = re.sub(r"^```(?:json|text)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _normalize_agent_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    if normalized.get("area") in {"area", "n/a", "none", "null"}:
        normalized["area"] = ""
    if isinstance(normalized.get("response"), str):
        normalized["response"] = _strip_agent_reasoning(normalized["response"])
    return normalized


def _parse_agent_output(raw_output: str) -> dict[str, Any]:
    """Parse the agent contract and remove leaked planning/reasoning text."""
    cleaned = _strip_agent_reasoning(raw_output)
    candidates = [cleaned]
    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx != -1 and end_idx > start_idx:
        candidates.append(cleaned[start_idx : end_idx + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return _normalize_agent_payload(payload)

    return {"response": cleaned}


def _detect_response_language(input_text: str) -> str:
    if _JAPANESE_TEXT_PATTERN.search(input_text):
        return "ja"
    if re.search(r"(?:ngập|lụt|tiếng việt|vietnamese)", input_text, re.IGNORECASE):
        return "vi"
    return "en"


def _try_language_preference_response(input_text: str) -> dict[str, Any] | None:
    if not (
        _LANGUAGE_KEYWORD_PATTERN.search(input_text)
        and _LANGUAGE_ACTION_PATTERN.search(input_text)
    ):
        return None

    lowered = input_text.lower()
    if "日本語" in input_text or "tiếng nhật" in lowered or "japanese" in lowered:
        response = "はい、日本語でお答えできます。ご質問をどうぞ。"
    elif "tiếng anh" in lowered or "english" in lowered:
        response = "Yes, I can answer in English. What would you like to know?"
    else:
        response = "Được, tôi có thể trả lời bằng tiếng Việt. Bạn muốn hỏi điều gì?"

    return {
        "analysis_type": "Conversation",
        "area": "",
        "response": response,
        "visualizations": [],
    }


def _prepare_agent_input(input_text: str) -> str:
    if _JAPANESE_TEXT_PATTERN.search(input_text):
        return (
            "[Response-language instruction: answer only in Japanese. Do not expose reasoning or tool activity.]\n"
            f"{input_text}"
        )
    return input_text


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
        today = date.today()
        return (today - timedelta(days=_LATEST_LOOKBACK_DAYS)).isoformat(), today.isoformat()
    if len(dates) == 1:
        return dates[0], dates[0]
    return dates[0], dates[1]


def _extract_place(input_text: str) -> str | None:
    if "ハノイ" in input_text:
        return "Hanoi Vietnam"
    if "ベトナム" in input_text:
        return "Vietnam"

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
    language: str = "vi",
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
        total_flooded_days = analysis.get("total_flooded_days", len(flooded_days))
        if total_flooded_days == 0:
            if language == "ja":
                response = f"{place}について、{start_date}から{end_date}までのGFM観測は見つかりましたが、洪水を示すピクセルは検出されませんでした。"
            elif language == "en":
                response = f"GFM observations were available for {place} from {start_date} to {end_date}, but no flood pixels were detected."
            else:
                response = f"Đã tìm thấy quan sát GFM tại {place} từ {start_date} đến {end_date}, nhưng không phát hiện pixel ngập lụt trong các ảnh đã có."
        else:
            area_label = (
                ("推定浸水面積" if language == "ja" else "Estimated flooded area")
                if result.get("area_is_estimate")
                else ("最大浸水面積" if language == "ja" else "Maximum flooded area")
            )
            if language == "ja":
                response = (
                    f"{place}の洪水を{start_date}から{end_date}まで分析しました。"
                    f"洪水の兆候が確認された日数は{total_flooded_days}日、{area_label}は"
                    f"{analysis.get('total_max_flood_area_km2', 'データなし')} km²です。"
                )
                if flooded_days:
                    response += f"確認日は{', '.join(map(str, flooded_days))}です。"
            elif language == "en":
                response = (
                    f"Flood analysis for {place} from {start_date} to {end_date}: "
                    f"{total_flooded_days} day(s) showed flood signals, with an {area_label.lower()} of "
                    f"{analysis.get('total_max_flood_area_km2', 'no data')} km²."
                )
                if flooded_days:
                    response += f" Detected dates: {', '.join(map(str, flooded_days))}."
            else:
                response = (
                    f"Phân tích ngập lụt tại {place} từ {start_date} đến {end_date}. "
                    f"Số ngày có dấu hiệu ngập: {total_flooded_days}. "
                    f"{area_label}: {analysis.get('total_max_flood_area_km2', 'không có dữ liệu')} km²."
                )
                if flooded_days:
                    response += f" Các ngày ghi nhận: {', '.join(map(str, flooded_days))}."
    elif status == "no_data":
        if language == "ja":
            response = f"{place}について、{start_date}から{end_date}までのGFM洪水データは見つかりませんでした。"
        elif language == "en":
            response = f"No GFM flood observations were found for {place} from {start_date} to {end_date}."
        else:
            response = f"Không tìm thấy dữ liệu GFM về ngập lụt tại {place} trong khoảng {start_date} đến {end_date}."
    else:
        response = str(result.get("message") or "Không thể hoàn tất phân tích ngập lụt.")

    return {
        "analysis_type": result.get("analysis_type", "GFM Flood Analysis"),
        "area": bbox,
        "response": response,
        "tile_url": result.get("tile_url"),
        "legend": result.get("legend"),
        "visualizations": result.get("visualizations", []),
        "source": result.get("source"),
        "observed_dates": result.get("observed_dates", []),
        "requested_window": {"start_date": start_date, "end_date": end_date},
    }


async def _try_direct_flood_analysis(input_text: str) -> dict[str, Any] | None:
    if not _FLOOD_TERMS.search(input_text):
        return None

    place = _extract_place(input_text)
    if not place:
        return None

    start_date, end_date = _extract_analysis_dates(input_text)
    language = _detect_response_language(input_text)
    display_place = "ベトナム" if language == "ja" and place == "Vietnam" else place
    if language == "ja" and place == "Hanoi Vietnam":
        display_place = "ベトナム・ハノイ"
    try:
        geocoded = await geocode_place(place)
        raw_result = await asyncio.wait_for(
            _invoke_flood_tool(geocoded["bbox"], start_date, end_date),
            timeout=120.0,
        )
        return _format_flood_result(
            raw_result, display_place, geocoded["bbox"], start_date, end_date, language
        )
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
    language_result = _try_language_preference_response(input)
    if language_result is not None:
        return language_result

    direct_result = await _try_direct_flood_analysis(input)
    if direct_result is not None:
        return direct_result

    try:
        session = AsyncSQLiteSession(session_id, DB_PATH)
        logger.info("Processing input: %s for session_id: %s", input, session_id)

        try:
            result = await Runner.run(
                management_agent,
                input=_prepare_agent_input(input),
                session=session,
                max_turns=5,
                run_config=RunConfig(session_settings=SessionSettings(limit=50)),
            )
        except (httpx.RequestError, httpx.HTTPStatusError, TimeoutError) as net_err:
            logger.error("Network error while calling agent", exc_info=True)
            raise RuntimeError("Unable to connect to the AI service. Please try again.") from net_err

        raw_output = result.final_output
        if not raw_output:
            return {"response": "The assistant did not return a response."}

        return _parse_agent_output(str(raw_output))
    except Exception:
        logger.exception("Error while getting response from agent")
        raise
