from fastapi import APIRouter, HTTPException
from app.services.satellite.satellite_service import generate_satellite_mosaic
from app.schemas.satellite_schema import SatelliteSearchRequest, SatelliteSearchResponse

router = APIRouter()

@router.post("/mosaic", response_model=SatelliteSearchResponse)
async def create_mosaic_endpoint(request: SatelliteSearchRequest):
    try:
        result = await generate_satellite_mosaic(
            satellite_type=request.satellite_type,
            date=request.date,
            bbox=request.bbox
        )

        if result["items_count"] == 0:
            return SatelliteSearchResponse(
                search_id="",
                tilejson_url="",
                xyz_url="",
                bounds=request.bbox,
                items_count=0
            )

        return SatelliteSearchResponse(
            search_id=result["search_id"],
            tilejson_url=result["tilejson_url"],
            xyz_url=result["xyz_url"],
            bounds=request.bbox,
            items_count=result["items_count"]
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError:
        raise HTTPException(
            status_code=502,
            detail=(
                "Không thể lấy ảnh vệ tinh lúc này do dịch vụ bên ngoài gặp sự cố hoặc mất kết nối. "
                "Vui lòng thử lại sau."
            )
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=(
                "Không thể xử lý yêu cầu ảnh vệ tinh lúc này. "
                "Vui lòng thử lại sau."
            )
        )
