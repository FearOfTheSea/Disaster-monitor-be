from pystac_client import Client
import planetary_computer

from app.core.config import settings


def get_planetary_client():
    client = Client.open(
        settings.PLANETARY_COMPUTER_STAC_URL,
        modifier=planetary_computer.sign_inplace
    )

    return client
