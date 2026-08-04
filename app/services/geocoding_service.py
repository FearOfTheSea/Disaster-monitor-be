"""Provider-abstracted place-name geocoding for the management agent."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_nominatim_rate_lock = asyncio.Lock()
_last_nominatim_request_at = 0.0


class GeocodingError(RuntimeError):
    """Raised when a configured geocoder cannot resolve a place."""


async def _respect_nominatim_rate_limit() -> None:
    """Keep requests to public Nominatim at or below one request per second."""

    global _last_nominatim_request_at

    async with _nominatim_rate_lock:
        elapsed = time.monotonic() - _last_nominatim_request_at
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        _last_nominatim_request_at = time.monotonic()


def _normalise_query(query: str) -> str:
    normalised = " ".join(query.split())
    if not normalised:
        raise GeocodingError("A place name is required for geocoding.")
    return normalised


def _parse_result(result: dict[str, Any], query: str, provider: str) -> dict[str, Any]:
    try:
        lat = float(result["lat"])
        lon = float(result["lon"])
        south, north, west, east = (float(value) for value in result["boundingbox"])
    except (KeyError, TypeError, ValueError):
        raise GeocodingError(f"The geocoder returned an incomplete result for '{query}'.")

    return {
        "lat": lat,
        "lon": lon,
        "bbox": f"{west},{south},{east},{north}",
        "display_name": result.get("display_name", query),
        "provider": provider,
    }


async def geocode_place(query: str) -> dict[str, Any]:
    """Resolve a place name to coordinates and an Earth Engine bbox.

    Nominatim is the default keyless provider for the small local MVP. Set
    ``GEOCODER_PROVIDER=locationiq`` to use the existing LocationIQ path.
    """

    normalised_query = _normalise_query(query)
    provider = settings.GEOCODER_PROVIDER.strip().lower()
    cache_key = (provider, normalised_query.casefold())
    cached = _cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < settings.GEOCODER_CACHE_TTL_SECONDS:
        return cached[1]

    if provider == "nominatim":
        base_url = settings.GEOCODER_BASE_URL.rstrip("/") + "/search"
        params: dict[str, Any] = {
            "q": normalised_query,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
            "accept-language": "vi,en",
        }
        if settings.GEOCODER_COUNTRYCODES.strip():
            params["countrycodes"] = settings.GEOCODER_COUNTRYCODES.strip()
        headers = {"User-Agent": settings.GEOCODER_USER_AGENT}
        await _respect_nominatim_rate_limit()
    elif provider == "locationiq":
        if not settings.IQ_LOCATION_API_KEY:
            raise GeocodingError("LocationIQ is not configured for place-name lookup.")
        base_url = "https://us1.locationiq.com/v1/search"
        params = {
            "key": settings.IQ_LOCATION_API_KEY,
            "q": normalised_query,
            "format": "json",
            "limit": 1,
            "addressdetails": 1,
        }
        headers = {"User-Agent": settings.GEOCODER_USER_AGENT}
    else:
        raise GeocodingError(f"Unsupported geocoder provider: {provider}.")

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            headers=headers,
            follow_redirects=True,
        ) as client:
            response = await client.get(base_url, params=params)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException as exc:
        raise GeocodingError("The geocoding service timed out. Please try again.") from exc
    except httpx.HTTPStatusError as exc:
        logger.warning("Geocoder returned HTTP %s for %s", exc.response.status_code, normalised_query)
        raise GeocodingError("The geocoding service returned an error.") from exc
    except httpx.RequestError as exc:
        raise GeocodingError("The geocoding service could not be reached.") from exc
    except ValueError as exc:
        raise GeocodingError("The geocoding service returned invalid data.") from exc

    if not isinstance(data, list) or not data:
        raise GeocodingError(f"No coordinates were found for '{normalised_query}'.")

    result = _parse_result(data[0], normalised_query, provider)
    _cache[cache_key] = (time.monotonic(), result)
    return result
