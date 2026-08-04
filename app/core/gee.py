import logging

import ee

from app.core.config import settings


logger = logging.getLogger(__name__)


def init_gee() -> None:
    if not settings.GEE_SERVICE_ACCOUNT or not (settings.GEE_KEY_JSON or settings.GEE_KEY_PATH):
        raise RuntimeError(
            "Google Earth Engine is not configured. Set GEE_SERVICE_ACCOUNT and "
            "GEE_KEY_JSON or GEE_KEY_PATH to enable index analysis."
        )

    if settings.GEE_KEY_JSON:
        credentials = ee.ServiceAccountCredentials(
            settings.GEE_SERVICE_ACCOUNT,
            key_data=settings.GEE_KEY_JSON,
        )
    else:
        credentials = ee.ServiceAccountCredentials(
            settings.GEE_SERVICE_ACCOUNT,
            settings.GEE_KEY_PATH,
        )
    ee.Initialize(credentials)
    logger.info("GEE initialized")
