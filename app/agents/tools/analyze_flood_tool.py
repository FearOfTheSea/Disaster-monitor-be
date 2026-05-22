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
import ee
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

def _gfm_raster_worker(items, bbox, wkt_string, resolution, bands) -> Dict[str, Any]:
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
    binary_result = xr.where(filtered_data.sum(dim="time") > 0, 1, 0).astype("uint8")
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

@function_tool(timeout=180.0)
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

    # Truyền chuỗi WKT vào Process để tránh lỗi
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


# def _get_clean_sentinel(aoi: ee.Geometry, start: str, end: str, cloud_pct: float=50) -> Optional[ee.Image]:
#     """
#     Tải và lọc mây ảnh Sentinel-2 bằng s2cloudless kết hợp bóng mây.
#     """
#     CLD_PRB_THRESH = 50     # Điểm xác suất > 50% thì coi là mây
#     NIR_DRK_THRESH = 0.15   # Ngưỡng vùng tối để tìm bóng mây: nir < 0.15 -> bóng mây
#     CLD_PRJ_DIST = 1        # Khoảng cách tối đa chiếu bóng mây
#     BUFFER = 50             # Mở rộng viền mây thêm 50m

#     # Tải và lọc bộ dữ liệu gốc
#     s2_sr_col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
#                   .filterBounds(aoi)
#                   .filterDate(start, end)
#                   .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_pct)))

#     # Tải bộ dữ liệu xác suất mây
#     s2_cloudless_col = (ee.ImageCollection('COPERNICUS/S2_CLOUD_PROBABILITY')
#                         .filterBounds(aoi)
#                         .filterDate(start, end))

#     # Ghép cả hai bộ dữ liệu
#     joined_col = ee.ImageCollection(ee.Join.saveFirst('s2cloudless').apply(**{
#         'primary': s2_sr_col,
#         'secondary': s2_cloudless_col,
#         'condition': ee.Filter.equals(**{
#             'leftField': 'system:index',
#             'rightField': 'system:index'
#         })
#     }))

#     # Kiểm tra xem có ảnh không
#     if joined_col.limit(1).size().getInfo() == 0:
#         return None

#     # Hàm lọc mây và bóng mây
#     def process_cloud_shadow(img):
#         # Nhận diện mây
#         cld_prb = ee.Image(img.get('s2cloudless')).select('probability')
#         is_cloud = cld_prb.gt(CLD_PRB_THRESH).rename('clouds')
#         img_cloud = img.addBands(ee.Image([cld_prb, is_cloud]))

#         # Nhận diện Bóng mây
#         not_water = img_cloud.select('SCL').neq(6)
#         SR_BAND_SCALE = 1e4
#         dark_pixels = img_cloud.select('B8').lt(NIR_DRK_THRESH * SR_BAND_SCALE).multiply(not_water).rename('dark_pixels')

#         shadow_azimuth = ee.Number(90).subtract(ee.Number(img_cloud.get('MEAN_SOLAR_AZIMUTH_ANGLE')))
#         cld_proj = (img_cloud.select('clouds').directionalDistanceTransform(shadow_azimuth, CLD_PRJ_DIST * 10)
#             .reproject(**{'crs': img_cloud.select(0).projection(), 'scale': 100})
#             .select('distance')
#             .mask()
#             .rename('cloud_transform'))

#         shadows = cld_proj.multiply(dark_pixels).rename('shadows')
#         img_cloud_shadow = img_cloud.addBands(ee.Image([dark_pixels, cld_proj, shadows]))

#         # Gộp Mây và Bóng mây
#         is_cld_shdw = img_cloud_shadow.select('clouds').add(img_cloud_shadow.select('shadows')).gt(0)
#         is_cld_shdw = (is_cld_shdw.focalMin(2).focalMax(BUFFER * 2 / 20)
#             .reproject(**{'crs': img.select([0]).projection(), 'scale': 20})
#             .rename('cloudmask'))

#         not_cld_shdw = is_cld_shdw.Not()
#         return img.updateMask(not_cld_shdw).divide(10000)

#     img_final = (joined_col.map(process_cloud_shadow)
#                  .median()
#                  .select(['B4', 'B8', 'B11', 'B12'], ['red', 'nir', 'swir1', 'swir2'])
#                  .clip(aoi))

#     return img_final
