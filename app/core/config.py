from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Settings(BaseSettings):
    PROJECT_NAME: str = "FastApi Backend"
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000"]
    DATABASE_URL: str = ""
    SESSION_DIR: str = "session"

    LLM_PROVIDER: str = "ollama"
    LLM_MODEL: str = ""
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434/v1"
    OLLAMA_API_KEY: str = "ollama"
    OLLAMA_MODEL: str = "qwen3:1.7b"

    GEMINI_API_KEY: str = ""
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    GEMINI_MODEL: str = "gemini-2.5-flash-lite"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    API_V1_STR: str = "/api/v1"
    BASE_URL: str = "http://localhost:8001"
    STATIC_DIR: str = "./static"
    STATIC_URL: str = "/static"

    GEE_SERVICE_ACCOUNT: str = ""
    GEE_KEY_PATH: str = ""
    GEE_KEY_JSON: str = ""
    PLANETARY_COMPUTER_STAC_URL: str = "https://planetarycomputer.microsoft.com/api/stac/v1"
    PLANETARY_MOSAIC_URL: str = "https://planetarycomputer.microsoft.com/api/data/v1/mosaic/register"
    OPENWEATHER_API_KEY: str = ""
    IQ_LOCATION_API_KEY: str = ""
    GEOCODER_PROVIDER: str = "nominatim"
    GEOCODER_BASE_URL: str = "https://nominatim.openstreetmap.org"
    GEOCODER_USER_AGENT: str = "DisasterMonitor/0.1"
    GEOCODER_COUNTRYCODES: str = "vn"
    GEOCODER_CACHE_TTL_SECONDS: int = 86400
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
