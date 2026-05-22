from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Settings(BaseSettings):
    PROJECT_NAME: str = "FastApi Backend"
    ALLOWED_ORIGINS: List[str] = []
    GEMINI_API_KEY: str = ""
    API_V1_STR: str = "/api/v1"
    BASE_URL: str = "http://localhost:8001"
    STATIC_DIR: str = "./static"
    STATIC_URL: str = "/static"
    GEE_SERVICE_ACCOUNT: str
    GEE_KEY_PATH: str
    PLANETARY_COMPUTER_STAC_URL: str
    PLANETARY_MOSAIC_URL: str
    OPENWEATHER_API_KEY: str
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
