import httpx
import planetary_computer
from typing import List, Dict, Any
from app.services.satellite.planetary_client import get_planetary_client
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

SATELLITE_CONFIG = {
    "sentinel2_rgb": {
        "collection": "sentinel-2-l2a",
        "assets": ["B04", "B03", "B02"],
        "color_formula": (
            "Gamma RGB 3.2 "
            "Saturation 0.8 "
            "Sigmoidal RGB 25 0.35"
        ),
    },

    "landsat_c2_l2_rgb": {
        "collection": "landsat-c2-l2",
        "assets": ["red", "green", "blue"],
        "color_formula": (
            "gamma RGB 2.7, "
            "saturation 1.5, "
            "sigmoidal RGB 15 0.55"
        )
    }
}

async def generate_satellite_mosaic(satellite_type: str, date: str, bbox: List[float]) -> Dict[str, Any]:
    """
    Tìm kiếm STAC và đăng ký Mosaic bằng search.get_parameters()
    """
    client = get_planetary_client()
    config = SATELLITE_CONFIG.get(satellite_type)
    if not config:
        raise ValueError(f"Satellite type '{satellite_type}' không được hỗ trợ.")

    collection = config["collection"]

    try:
        search = client.search(
            collections=[collection],
            bbox=bbox,
            datetime=f"{date}T00:00:00Z/{date}T23:59:59Z",
            limit=20
        )
        items = list(search.items())
        items_count = len(items)
    except Exception as e:
        logger.error(f"Lỗi xảy ra khi tìm kiếm ảnh vệ tinh : {e}")
        raise RuntimeError(f"Lỗi khi tìm kiếm hình ảnh vệ tinh: {str(e)}")

    if items_count == 0:
        return {"items_count": 0}

    try:
        async with httpx.AsyncClient(timeout=20.0) as async_client:
            response = await async_client.post(
                settings.PLANETARY_MOSAIC_URL,
                json=search.get_parameters()
            )
            response.raise_for_status()
            data = response.json()

    except httpx.HTTPStatusError as e:
        logger.error(f"Lỗi server Mosaic ({e.response.status_code}): {e.response.text}")
        raise RuntimeError(f"Hệ thống xử lý ảnh của Microsoft đang gặp sự cố. Mã lỗi: {e.response.status_code}")
    except httpx.RequestError as e:
        logger.error(f"Lỗi mạng khi đăng ký Mosaic: {str(e)}")
        raise RuntimeError("Kết nối mạng đến máy chủ xử lý ảnh bị gián đoạn. Vui lòng thử lại sau.")
    except Exception as e:
        logger.error(f"Lỗi không xác định khi đăng ký Mosaic: {str(e)}")
        raise RuntimeError("Đã xảy ra lỗi không xác định khi tạo bản đồ vệ tinh.")

    search_id = data["searchid"]

    try:
        assets_query = "".join([f"&assets={asset}" for asset in config["assets"]])
        render_params = (
            f"?collection={collection}"
            f"{assets_query}"
            "&nodata=0"
            f"&color_formula={config['color_formula']}"
            "&resampling=lanczos"
            "&tile_scale=2"
        )

        base_mosaic_url = f"https://planetarycomputer.microsoft.com/api/data/v1/mosaic/{search_id}/tiles/WebMercatorQuad"

        raw_tilejson_url = f"{base_mosaic_url}/tilejson.json{render_params}"
        signed_tilejson_url = planetary_computer.sign(raw_tilejson_url)

        raw_xyz_url = f"{base_mosaic_url}/{{z}}/{{x}}/{{y}}@1x{render_params}"
        signed_xyz_url = planetary_computer.sign(raw_xyz_url)
    except Exception as e:
        logger.error(f"Lỗi khi tạo URL cho Mosaic: {str(e)}")
        raise RuntimeError("Đã xảy ra lỗi khi tạo URL cho bản đồ vệ tinh.")

    return {
        "items_count": items_count,
        "search_id": search_id,
        "tilejson_url": signed_tilejson_url,
        "xyz_url": signed_xyz_url
    }
