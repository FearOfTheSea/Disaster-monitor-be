import logging
from typing import List

from agents import function_tool

from app.services.geocoding_service import GeocodingError, geocode_place

logger = logging.getLogger(__name__)


@function_tool(timeout=15.0)
async def get_coordinates_from_input(string_input: str) -> List[float] | None:
    """Convert a place name or address into [latitude, longitude]."""

    try:
        result = await geocode_place(string_input)
        return [round(result["lat"], 5), round(result["lon"], 5)]
    except GeocodingError as exc:
        logger.warning("Geocoding failed for %r: %s", string_input, exc)
        return None
    except Exception:
        logger.exception("Unexpected error while geocoding coordinates for %r", string_input)
        return None
