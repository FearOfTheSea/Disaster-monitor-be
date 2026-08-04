import logging

import ee

from app.core.config import settings


logger = logging.getLogger(__name__)


def init_gee() -> None:
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
