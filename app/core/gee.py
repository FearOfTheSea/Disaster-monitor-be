import logging

import ee

from app.core.config import settings


logger = logging.getLogger(__name__)


def init_gee() -> None:
    credentials = ee.ServiceAccountCredentials(
        settings.GEE_SERVICE_ACCOUNT,
        settings.GEE_KEY_PATH
    )
    ee.Initialize(credentials)
    logger.info("GEE initialized")
