import httpx
import logging

from app.core.config import settings

from app.schemas.weather_schema import (
    ResponseCurrentWeatherSchema,
    LocationSchema,
    WeatherConditionSchema,
    TemperatureSchema,
    WindSchema,
    RainSchema,
    SnowSchema
)

logger = logging.getLogger(__name__)

OPENWEATHER_CURRENT_URL = (
    "https://api.openweathermap.org/data/2.5/weather"
)


async def get_current_weather(lat: float, lon: float) -> ResponseCurrentWeatherSchema:

    if not settings.OPENWEATHER_API_KEY:
        raise RuntimeError(
            "OpenWeather is not configured. Set OPENWEATHER_API_KEY to enable weather data."
        )

    params = {
        "lat": lat,
        "lon": lon,
        "appid": settings.OPENWEATHER_API_KEY,
        "units": "metric",
        "lang": "vi"
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:

            response = await client.get(OPENWEATHER_CURRENT_URL, params=params)
            response.raise_for_status()
            data = response.json()

    except httpx.HTTPStatusError as e:
        logger.error(f"OpenWeather HTTP Error: {e.response.text}")
        raise RuntimeError("Không thể lấy dữ liệu thời tiết.")
    except httpx.RequestError as e:
        logger.error(f"OpenWeather Request Error: {str(e)}")
        raise RuntimeError("Lỗi kết nối đến hệ thống thời tiết.")

    weather_data = data["weather"][0]

    rain_data = data.get("rain")
    snow_data = data.get("snow")

    return ResponseCurrentWeatherSchema(

        location=LocationSchema(
            lat=data["coord"]["lat"],
            lon=data["coord"]["lon"],
            name=data.get("name", ""),
        ),

        weather=WeatherConditionSchema(
            main=weather_data.get("main", ""),
            description=weather_data.get(
                "description",
                ""
            ),
            icon=weather_data.get("icon", "")
        ),

        temperature=TemperatureSchema(
            current=data["main"]["temp"],
            feels_like=data["main"]["feels_like"],
        ),

        humidity=data["main"]["humidity"],

        pressure=data["main"]["pressure"],

        wind=WindSchema(
            speed=data["wind"]["speed"],
            deg=data["wind"].get("deg", 0),
            gust=data["wind"].get("gust")
        ),

        clouds=data.get("clouds", {}).get("all"),

        rain=(
            RainSchema.model_validate(rain_data) if rain_data else None
        ),

        snow=(
            SnowSchema.model_validate(snow_data) if snow_data else None
        ),

        dt=data.get("dt")
    )
