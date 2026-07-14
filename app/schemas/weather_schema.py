from typing import Optional
from pydantic import BaseModel, Field

class RequestCurrentWeatherSchema(BaseModel):
    lat: float
    lon: float

class LocationSchema(BaseModel):
    lat: float
    lon: float

    name: str

class WeatherConditionSchema(BaseModel):
    main: str
    description: str
    icon: str


class TemperatureSchema(BaseModel):
    current: float
    feels_like: float


class WindSchema(BaseModel):
    speed: float
    deg: int
    gust: Optional[float] = None


class RainSchema(BaseModel):
    one_hour: Optional[float] = Field(
        default=None,
        alias="1h"
    )



class SnowSchema(BaseModel):
    one_hour: Optional[float] = Field(
        default=None,
        alias="1h"
    )



class ResponseCurrentWeatherSchema(BaseModel):
    location: LocationSchema

    weather: WeatherConditionSchema

    temperature: TemperatureSchema

    humidity: int

    pressure: int

    wind: WindSchema

    clouds: Optional[int] = None

    rain: Optional[RainSchema] = None

    snow: Optional[SnowSchema] = None

    dt: int

    model_config = {
        "populate_by_name": True
    }
