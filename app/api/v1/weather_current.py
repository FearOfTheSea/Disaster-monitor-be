from fastapi import APIRouter, HTTPException
from app.services.weather_data_service import get_current_weather
from app.schemas.weather_schema import ResponseCurrentWeatherSchema, RequestCurrentWeatherSchema

router = APIRouter()

@router.post("/current", response_model=ResponseCurrentWeatherSchema)
async def current_weather(request: RequestCurrentWeatherSchema):
    try:
        return await get_current_weather(lat=request.lat, lon=request.lon)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi Server khi lấy dữ liệu thời tiết: {str(e)}")
