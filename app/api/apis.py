from fastapi import APIRouter
from app.api.v1 import map, satellite_image, weather_current

api_router = APIRouter()

api_router.include_router(map.router, prefix="/maps", tags=["Maps"])
api_router.include_router(satellite_image.router, prefix="/satellite", tags=["Satellite Images"])
api_router.include_router(weather_current.router, prefix="/weather", tags=["Weather"])
