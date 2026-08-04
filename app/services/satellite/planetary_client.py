from pystac_client import Client
import planetary_computer

from app.core.config import settings


def get_planetary_client():
    if not settings.PLANETARY_COMPUTER_STAC_URL:
        raise RuntimeError(
            "Planetary Computer is not configured. Set PLANETARY_COMPUTER_STAC_URL "
            "to enable satellite imagery."
        )

    client = Client.open(
        settings.PLANETARY_COMPUTER_STAC_URL,
        modifier=planetary_computer.sign_inplace
    )

    return client
