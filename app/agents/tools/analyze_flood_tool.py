import asyncio
import json
from typing import Any, Dict, List
import logging
import pystac
import pyproj
import xarray as xr
from datetime import datetime
from shapely.geometry import box
from pystac_client import Client
from odc import stac as odc_stac
from agents import function_tool
from concurrent.futures import TimeoutError as PebbleTimeoutError
from pebble import ProcessPool

logger = logging.getLogger(__name__)

def _fetch_gfm_items(bbox: List[float], start_date: str, end_date: str) -> pystac.ItemCollection | None:
    """
    Truy vấn EODC STAC API cho bộ dữ liệu GFM.

    Args:
        bbox (list): Bounding box [min_x, min_y, max_x, max_y]
        start_date (str): Ngày bắt đầu "YYYY-MM-DD"
        end_date (str): Ngày kết thúc "YYYY-MM-DD"

    Returns:
        pystac.ItemCollection: Tập hợp các STAC Item (pystac.Item)
    """
    api_url = "https://stac.eodc.eu/api/v1"
    collection_id = "GFM"
    max_items = 1000

    try:
        aoi = box(*bbox)
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        time_range = (start_dt, end_dt)

        eodc_catalog = Client.open(api_url)
        search = eodc_catalog.search(
            max_items=max_items,
            collections=collection_id,
            intersects=aoi,
            datetime=time_range
        )

        items = search.item_collection()
        if not items:
            logger.warning("Không thấy dữ liệu gfm nào phù hợp với khoảng thời gian đầu vào.")
            return None

        return items

    except Exception as e:
        logger.error(f"Lỗi khi truy vấn GFM data: {str(e)}")
        raise RuntimeError("Không thể kết nối dịch vụ dữ liệu ngập lụt.") from e

def _gfm_raster_worker(items: pystac.ItemCollection, bbox: List[float], wkt_string: str, resolution: float, bands: List[str]) -> Dict[str, Any]:
    """
    Xử lí dữ liệu raster GFM
    """
    crs = pyproj.CRS.from_wkt(wkt_string)

    xx_object = odc_stac.load(
        items,
        bbox=bbox,
        crs=crs,
        bands=bands,
        resolution=resolution,
        dtype='uint8',
        groupby="solar_day",
    )

    if xx_object is None or len(xx_object.data_vars) == 0:
        return {"status": "no_data", "message": "Không load được dữ liệu từ items gfm."}

    data = xx_object["ensemble_flood_extent"]
    flooded_days = []

    # Tìm các ngày có ngập
    for t in data.time.values:
        day_slice = data.sel(time=t)
        if int(((day_slice != 255) & (day_slice > 0)).sum().values) > 0:
            flooded_days.append(str(t)[:10])

    if not flooded_days:
        return {
            "status": "success",
            "message": "Phân tích hoàn tất. Khu vực này không có dấu hiệu ngập lụt trong khoảng thời gian đã chọn."
        }

    # Tính diện tích ngập tối đa
    filtered_data = data.where((data != 255) & (data != 0))
    result = filtered_data.sum(dim="time")
    binary_result = xr.where(result > 0, 1, 0).astype("uint8")
    pixel_area_m2 = resolution * resolution
    total_flood_pixel = (binary_result == 1).sum().values
    total_flood_area_km2 = round(total_flood_pixel * pixel_area_m2 / 1_000_000, 2)

    return {
        "status": "success",
        "analysis_type": "GFM Flood Analysis",
        "source": "GloFAS Global Flood Monitoring",
        "area": bbox,
        "analysis": {
            "total_flooded_days": len(flooded_days),
            "flooded_days": flooded_days,
            "total_max_flood_area_km2": total_flood_area_km2
        }
    }

@function_tool(timeout=200.0)
async def get_gfm_flood_analysis(bbox: str, start_date: str, end_date: str) -> str:
    """
    Công cụ này dùng để phân tích dữ liệu ngập lụt từ GFM trong một khu vực và khoảng thời gian nhất định.
        Args:
        - bbox: Danh sách tọa độ [min_lon, min_lat, max_lon, max_lat] xác định khu vực quan tâm.
        - start_date: Ngày bắt đầu phân tích theo định dạng "YYYY-MM-DD".
        - end_date: Ngày kết thúc phân tích theo định dạng "YYYY-MM-DD".
       Returns: Chuỗi JSON chứa kết quả phân tích ngập lụt, bao gồm các ngày bị ngập, diện tích ngập tối đa và URL của file raster kết quả.
       """
    try:
        if isinstance(bbox, str):
            clean_input = bbox.replace("[", "").replace("]", "")
            bbox = [float(coord.strip()) for coord in clean_input.split(",")]
    except Exception as e:
        logger.error(f"Lỗi khi xử lý bbox đầu vào: {str(e)}")
        return json.dumps({
            "status": "error",
            "message": "Định dạng bbox không hợp lệ. Vui lòng cung cấp bbox dưới dạng danh sách hoặc chuỗi 'min_lon,min_lat,max_lon,max_lat'.",
            "detail": str(e)
        })

    try:
        items = await asyncio.to_thread(_fetch_gfm_items, bbox, start_date, end_date)
    except RuntimeError as e:
        return json.dumps({
            "status": "error",
            "message": "Không thể kết nối dịch vụ dữ liệu ngập lụt. Vui lòng thử lại sau.",
            "detail": str(e)
        })
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": "Lỗi khi tìm kiếm dữ liệu",
            "detail": str(e)
        })

    if not items:
        logger.warning("Không thấy dữ liệu gfm nào phù hợp với khoảng thời gian đầu vào.")
        return json.dumps({
            "status": "no_data",
            "message": f"Không tìm thấy dữ liệu gfm trong khoảng thời gian {start_date} đến {end_date}.",
        })

    wkt_string = items[0].properties["proj:wkt2"]
    resolution = items[0].properties['gsd']
    bands = ["ensemble_flood_extent"]

    try:
        with ProcessPool(max_workers=1) as pool:
            future = pool.schedule(
                _gfm_raster_worker,
                args=(items, bbox, wkt_string, resolution, bands),
                timeout=180.0
            )

            result_dict = await asyncio.wrap_future(future)
            return json.dumps(result_dict)

    except PebbleTimeoutError:
        logger.error("Hệ thống xử lý Raster quá 180s, tiến trình đã bị hệ thống tiêu diệt.")
        return json.dumps({
            "status": "error",
            "message": "Thời gian phân tích quá lâu (vượt quá 180 giây). Tác vụ đã tự động bị hủy để bảo vệ hệ thống. Vui lòng thử lại với một khu vực (bbox) nhỏ hơn."
        })
    except Exception as e:
        logger.error(f"Lỗi khi tính toán dữ liệu flood trong worker: {str(e)}")
        return json.dumps({
            "status": "error",
            "message": "Không thể tính toán dữ liệu do lỗi nội bộ.",
            "detail": str(e)
        })
