import logging

from agents import function_tool

from app.services.geocoding_service import GeocodingError, geocode_place

logger = logging.getLogger(__name__)


@function_tool(timeout=15.0)
async def get_bbox_from_input(string_input: str) -> str | dict[str, str]:
    """Convert a place name or address into an Earth Engine bbox string."""

    try:
        result = await geocode_place(string_input)
        return result["bbox"]
    except GeocodingError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("Unexpected error while geocoding bbox for %r", string_input)
        return {"error": "An unexpected geocoding error occurred."}
