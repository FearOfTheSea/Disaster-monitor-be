import asyncio
import json
import logging
from concurrent.futures import TimeoutError as PebbleTimeoutError

import ee
from pebble import ProcessPool
from agents import function_tool
from app.agents.utils.statistics import compute_area_statistics, build_visualization
from app.agents.utils.get_baseline import get_historical_baseline
from app.core.gee import init_gee
logger = logging.getLogger(__name__)


def _init_gee_in_worker() -> None:
    # Process workers do not inherit initialized EE context.
    try:
        init_gee()
    except Exception as e:
        raise RuntimeError("Không thể kết nối GEE.") from e

def _mask_modis_ndvi(img):
    """
    Hàm gộp: Vừa lọc mây vừa lấy NDVI cho MODIS.
    """
    qa = img.select('SummaryQA')
    qa_extracted = qa.bitwiseAnd(3)
    mask = qa_extracted.lte(1)

    ndvi = img.updateMask(mask).select('NDVI').multiply(0.0001)
    return ndvi.copyProperties(img, ['system:time_start', 'system:time_end'])

def _bitwise_extract(input_img, from_bit, to_bit):
    """
    Trích xuất các bit chỉ định.
    """
    mask_size = ee.Number(1).add(to_bit).subtract(from_bit) # 2
    mask = ee.Number(1).leftShift(mask_size).subtract(1) # 00000011
    return input_img.rightShift(from_bit).bitwiseAnd(mask)

def _process_lst(image):
    """
    Hàm gộp: Lọc mây LST, scale và đổi sang độ C.
    """
    lst_day = image.select('LST_Day_1km')
    qc_day = image.select('QC_Day')

    # bit 0-1, 2-3:  0: chất lượng tốt
    qa_mask = _bitwise_extract(qc_day, 0, 1).eq(0)
    data_quality_mask = _bitwise_extract(qc_day, 2, 3).eq(0)

    # bit 6-7:  0: sai số  <= 1000K, 1: sai số <= 2000k
    lst_error_mask = _bitwise_extract(qc_day, 6, 7).lte(1)

    mask = qa_mask.And(data_quality_mask).And(lst_error_mask)

    lst_celsius = (
        lst_day.updateMask(mask)
        .multiply(0.02)
        .subtract(273.15)
        .rename('LST_Day_1km')
    )
    
    return lst_celsius.copyProperties(image, ['system:time_start', 'system:time_end'])


def _compute_tci_worker(aoi_geo: list[float], start_date: str, end_date: str, bbox_raw: str) -> dict:
    try:
        _init_gee_in_worker()

        aoi = ee.Geometry.Rectangle(aoi_geo)

        target_start = ee.Date(start_date)
        target_end = ee.Date(end_date)

        base_col = (
            ee.ImageCollection('MODIS/061/MOD11A2')
            .select(['LST_Day_1km', 'QC_Day'])
            .filterBounds(aoi)
        )

        target_lst = base_col.filterDate(target_start, target_end).map(_process_lst)

        if target_lst.size().getInfo() == 0:
            return {
                "status": "no_data",
                "message": f"Không có dữ liệu nhiệt độ sạch cho khu vực này từ {start_date} đến {end_date}."
            }

        baseline_info = get_historical_baseline(
            start_date=start_date,
            min_year=2000,
            requested_years_back=10,
            source_name="MODIS"
        )
        if not baseline_info["is_valid"]:
            return {
                "status": "error",
                "message": baseline_info["error_msg"]
            }

        hist_start = ee.Date.fromYMD(baseline_info["hist_year_start"], 1, 1)
        hist_end = ee.Date.fromYMD(baseline_info["hist_year_end"], 12, 31)

        start_doy = target_start.getRelative('day', 'year')
        end_doy = target_end.getRelative('day', 'year')

        doy_filter = ee.Filter(ee.Algorithms.If(
            ee.Number(start_doy).lte(end_doy),
            ee.Filter.dayOfYear(start_doy, end_doy),
            ee.Filter.Or(
                ee.Filter.dayOfYear(start_doy, 366),
                ee.Filter.dayOfYear(1, end_doy)
            )
        ))

        hist_lst = base_col.filterDate(hist_start, hist_end).filter(doy_filter).map(_process_lst)

        min_lst = hist_lst.min()
        max_lst = hist_lst.max()

        epsilon = ee.Image.constant(0.0001)

        def compute_tci(img):
            tci_val = (
                max_lst.subtract(img)
                .divide(
                    max_lst.subtract(min_lst).add(epsilon)
                )
                .multiply(100)
                .clamp(0, 100)
            )
            return tci_val.rename('TCI').copyProperties(img, ['system:time_start'])

        final_tci = target_lst.map(compute_tci).mean().clip(aoi)

        class_config = {
            1: {"label": "Hạn hán nhiệt độ cực đoan", "color": "#d7191c", "range": "TCI < 10"},
            2: {"label": "Hạn hán nhiệt độ nghiêm trọng", "color": "#fdae61", "range": "10 <= TCI < 20"},
            3: {"label": "Hạn hán nhiệt độ vừa", "color": "#ffffbf", "range": "20 <= TCI < 30"},
            4: {"label": "Hạn hán nhiệt độ nhẹ", "color": "#a6d96a", "range": "30 <= TCI < 40"},
            5: {"label": "Bình thường / Mát mẻ", "color": "#1a9641", "range": "TCI >= 40"}
        }

        class_img = final_tci.expression(
            "idx < 10 ? 1 : idx < 20 ? 2 : idx < 30 ? 3 : idx < 40 ? 4 : 5",
            {"idx": final_tci.select('TCI')}
        ).rename("class")

        world_cover = ee.ImageCollection("ESA/WorldCover/v200").first().clip(aoi)
        non_urban_water_mask = world_cover.neq(50).And(world_cover.neq(80))
        classified_tci = class_img.updateMask(final_tci.mask()).updateMask(non_urban_water_mask)

        palette = [conf["color"] for conf in class_config.values()]
        vis_result = build_visualization(class_img=classified_tci, palette=palette, geometry=aoi, dimensions=800)
        statistics = compute_area_statistics(
            class_img=classified_tci,
            labels=class_config,
            geometry=aoi,
            scale=1000,
            tileScale=4
        )

        return {
            "status": "success",
            "analysis_type": "TCI",
            "source": "MODIS_MOD11A2",
            "area": bbox_raw,
            "time_range": {
                "start_date": start_date,
                "end_date": end_date
            },
            "analysis": statistics,
            "image_url": vis_result['image_url'],
            "tile_url": vis_result['tile_url'],
            "legend": {
                conf_id: {
                    "label": conf_value["label"],
                    "color": conf_value["color"],
                    "range": conf_value["range"]
                } for conf_id, conf_value in class_config.items()
            }
        }
    except Exception as e:
        logging.getLogger("app").error(f"Lỗi hệ thống tính TCI: {str(e)}", exc_info=True)
        if "Không thể kết nối GEE" in str(e):
            return {
                "status": "error",
                "message": "Không thể kết nối GEE.",
                "detail": str(e)
            }
        return {
            "status": "error",
            "message": "Không thể xử lý TCI cho khu vực này.",
            "detail": str(e)
        }


def _compute_vhi_worker(aoi_geo: list[float], start_date: str, end_date: str, bbox_raw: str) -> dict:
    try:
        _init_gee_in_worker()

        aoi = ee.Geometry.Rectangle(aoi_geo)

        target_start = ee.Date(start_date)
        target_end = ee.Date(end_date)

        def process_ndvi(img):
            ndvi = img.select('NDVI').multiply(0.0001)
            return ndvi.copyProperties(img, ['system:time_start', 'system:time_end'])

        target_ndvi = (
            ee.ImageCollection('MODIS/061/MOD13Q1')
            .filterDate(target_start, target_end)
            .filterBounds(aoi)
            .map(process_ndvi)
        )

        if target_ndvi.size().getInfo() == 0:
            return {
                "status": "error",
                "message": (
                    "Không có dữ liệu NDVI cho khu vực này trong khoảng thời gian từ "
                    f"{start_date} đến {end_date}. Vui lòng mở rộng khoảng thời gian "
                    "(khuyến nghị tối thiểu 16 - 30 ngày)."
                )
            }

        def process_lst(img):
            lst = img.select('LST_Day_1km').multiply(0.02).subtract(273.15)
            return lst.copyProperties(img, ['system:time_start'])

        lst_8day = (
            ee.ImageCollection('MODIS/061/MOD11A2')
            .filterDate(target_start, target_end)
            .filterBounds(aoi)
            .map(process_lst)
        )

        if lst_8day.size().getInfo() == 0:
            return {
                "status": "error",
                "message": (
                    "Không có dữ liệu LST cho khu vực này trong khoảng thời gian từ "
                    f"{start_date} đến {end_date}. Vui lòng mở rộng khoảng thời gian "
                    "(khuyến nghị tối thiểu 16 - 30 ngày)."
                )
            }

        baseline_info = get_historical_baseline(
            start_date=start_date,
            min_year=2000,
            requested_years_back=10,
            source_name="MODIS"
        )
        if not baseline_info["is_valid"]:
            return {
                "status": "error",
                "message": baseline_info["error_msg"]
            }

        hist_start = ee.Date.fromYMD(baseline_info["hist_year_start"], 1, 1)
        hist_end = ee.Date.fromYMD(baseline_info["hist_year_end"], 12, 31)

        start_doy = target_start.getRelative('day', 'year')
        end_doy = target_end.getRelative('day', 'year')

        doy_filter = ee.Algorithms.If(
            ee.Number(start_doy).lte(end_doy),
            ee.Filter.dayOfYear(start_doy, end_doy),
            ee.Filter.Or(
                ee.Filter.dayOfYear(start_doy, 366),
                ee.Filter.dayOfYear(1, end_doy)
            )
        )

        epsilon = ee.Image.constant(0.0001)

        hist_ndvi = (
            ee.ImageCollection('MODIS/061/MOD13Q1')
            .filterDate(hist_start, hist_end)
            .filter(doy_filter)
            .filterBounds(aoi)
            .map(process_ndvi)
        )

        hist_lst = (
            ee.ImageCollection('MODIS/061/MOD11A2')
            .filterDate(hist_start, hist_end)
            .filter(doy_filter)
            .filterBounds(aoi)
            .map(process_lst)
        )

        min_ndvi = hist_ndvi.min()
        max_ndvi = hist_ndvi.max()

        min_lst = hist_lst.min()
        max_lst = hist_lst.max()

        class_config = {
            1: {"label": "Hạn hán cực đoan", "color": "#d7191c", "range": "VHI < 0.1"},
            2: {"label": "Hạn hán nghiêm trọng", "color": "#fdae61", "range": "0.1 - 0.2"},
            3: {"label": "Hạn hán vừa", "color": "#ffffc0", "range": "0.2 - 0.3"},
            4: {"label": "Hạn hán nhẹ", "color": "#a6d96a", "range": "0.3 - 0.4"},
            5: {"label": "Không hạn hán", "color": "#1a9641", "range": "VHI >= 0.4"}
        }

        def sync_lst(ndvi_img):
            start = ndvi_img.get('system:time_start')
            end = ndvi_img.get('system:time_end')

            lst_composite = (lst_8day.filterDate(start, end).mean())

            return (lst_composite.set('match_time', start))

        target_lst = target_ndvi.map(sync_lst)

        def set_match_time(img):
            return img.set('match_time', img.get('system:time_start'))

        target_ndvi_join = target_ndvi.map(set_match_time)

        join_filter = ee.Filter.equals(
            leftField='match_time',
            rightField='match_time'
        )

        join = ee.Join.saveFirst('matched_lst')

        joined = ee.ImageCollection(
            join.apply(
                target_ndvi_join,
                target_lst,
                join_filter
            )
        )

        def compute_vhi(img):
            ndvi_img = ee.Image(img)
            lst_img = ee.Image(img.get('matched_lst'))

            vci = (
                ndvi_img
                .subtract(min_ndvi)
                .divide(
                    max_ndvi
                    .subtract(min_ndvi)
                    .add(epsilon)
                )
                .clamp(0, 1)
            )

            tci = (
                max_lst
                .subtract(lst_img)
                .divide(
                    max_lst
                    .subtract(min_lst)
                    .add(epsilon)
                )
                .clamp(0, 1)
            )

            vhi = (
                vci.multiply(0.5)
                .add(tci.multiply(0.5))
                .rename('VHI')
            )

            return vhi.copyProperties(img, ['match_time'])

        vhi_collection = joined.map(compute_vhi)

        final_vhi = (vhi_collection.mean().clip(aoi))

        class_img = final_vhi.expression(
            """
            idx < 0.1 ? 1 :
            idx < 0.2 ? 2 :
            idx < 0.3 ? 3 :
            idx < 0.4 ? 4 : 5
            """,
            {"idx": final_vhi.select('VHI')}
        ).rename("class")
        world_cover = ee.ImageCollection("ESA/WorldCover/v200").first().clip(aoi)
        non_urban_water_mask = world_cover.neq(50).And(world_cover.neq(80))
        classified_vhi = class_img.updateMask(final_vhi.mask()).clip(aoi).updateMask(non_urban_water_mask)

        palette = [conf["color"] for conf in class_config.values()]
        vis_result = build_visualization(class_img=classified_vhi, palette=palette, geometry=aoi, dimensions=800)
        statistics = compute_area_statistics(
            class_img=classified_vhi,
            labels=class_config,
            geometry=aoi,
            scale=250,
            tileScale=4
        )

        return {
            "status": "success",
            "analysis_type": "VHI",
            "source": "MODIS",
            "area": bbox_raw,
            "time_range": {
                "start_date": start_date,
                "end_date": end_date
            },
            "analysis": statistics,
            "image_url": vis_result['image_url'],
            "tile_url": vis_result['tile_url'],
            "legend": {
                conf_id: {
                    "label": conf_value["label"],
                    "color": conf_value["color"],
                    "range": conf_value["range"]
                } for conf_id, conf_value in class_config.items()
            }
        }
    except Exception as e:
        logging.getLogger("app").error(f"Lỗi hệ thống tính VHI: {str(e)}", exc_info=True)
        if "Không thể kết nối GEE" in str(e):
            return {
                "status": "error",
                "message": "Không thể kết nối GEE.",
                "detail": str(e)
            }
        return {
            "status": "error",
            "message": "Không thể xử lý ảnh vệ tinh cho khu vực này.",
            "detail": str(e)
        }


def _compute_vci_worker(aoi_geo: list[float], start_date: str, end_date: str, bbox_raw: str) -> dict:
    try:
        _init_gee_in_worker()

        aoi = ee.Geometry.Rectangle(aoi_geo)

        target_start = ee.Date(start_date)
        target_end = ee.Date(end_date)

        base_col = (
            ee.ImageCollection('MODIS/061/MOD13Q1')
            .select(['NDVI', 'SummaryQA'])
            .filterBounds(aoi)
        )

        target_ndvi = base_col.filterDate(target_start, target_end).map(_mask_modis_ndvi)
        if target_ndvi.size().getInfo() == 0:
            return {
                "status": "error",
                "message": (
                    "Không có dữ liệu NDVI cho khu vực này trong khoảng thời gian từ "
                    f"{start_date} đến {end_date}. Vui lòng mở rộng khoảng thời gian "
                    "(khuyến nghị tối thiểu 16 - 30 ngày)."
                )
            }

        baseline_info = get_historical_baseline(
            start_date=start_date,
            min_year=2000,
            requested_years_back=10,
            source_name="MODIS"
        )
        if not baseline_info["is_valid"]:
            return {
                "status": "error",
                "message": baseline_info["error_msg"]
            }

        hist_start = ee.Date.fromYMD(baseline_info["hist_year_start"], 1, 1)
        hist_end = ee.Date.fromYMD(baseline_info["hist_year_end"], 12, 31)

        start_doy = target_start.getRelative('day', 'year')
        end_doy = target_end.getRelative('day', 'year')

        doy_filter = ee.Algorithms.If(
            ee.Number(start_doy).lte(end_doy),
            ee.Filter.dayOfYear(start_doy, end_doy),
            ee.Filter.Or(
                ee.Filter.dayOfYear(start_doy, 366),
                ee.Filter.dayOfYear(1, end_doy)
            )
        )
        hist_ndvi = base_col.filterDate(hist_start, hist_end).filter(doy_filter).map(_mask_modis_ndvi)
        min_ndvi = hist_ndvi.min()
        max_ndvi = hist_ndvi.max()
        epsilon = ee.Image.constant(0.0001)

        def compute_vci(img):
            vci_val = (
                img.subtract(min_ndvi)
                .divide(
                    max_ndvi.subtract(min_ndvi).add(epsilon)
                )
                .multiply(100)
                .clamp(0, 100)
            )
            return vci_val.rename('VCI').copyProperties(img, ['system:time_start'])

        final_vci = target_ndvi.map(compute_vci).mean().clip(aoi)

        class_config = {
            1: {"label": "Hạn hán cực đoan", "color": "#d7191c", "range": "VCI < 10"},
            2: {"label": "Hạn hán nghiêm trọng", "color": "#fdae61", "range": "10 <= VCI < 20"},
            3: {"label": "Hạn hán vừa", "color": "#ffffbf", "range": "20 <= VCI < 30"},
            4: {"label": "Hạn hán nhẹ", "color": "#a6d96a", "range": "30 <= VCI < 40"},
            5: {"label": "Bình thường / Tốt", "color": "#1a9641", "range": "VCI >= 40"}
        }

        class_img = final_vci.expression(
            "idx < 10 ? 1 : idx < 20 ? 2 : idx < 30 ? 3 : idx < 40 ? 4 : 5",
            {"idx": final_vci.select('VCI')}
        ).rename("class")

        world_cover = ee.ImageCollection("ESA/WorldCover/v200").first().clip(aoi)
        non_urban_water_mask = world_cover.neq(50).And(world_cover.neq(80))
        classified_vci = class_img.updateMask(final_vci.mask()).updateMask(non_urban_water_mask)

        palette = [conf["color"] for conf in class_config.values()]
        vis_result = build_visualization(class_img=classified_vci, palette=palette, geometry=aoi, dimensions=800)
        statistics = compute_area_statistics(
            class_img=classified_vci,
            labels=class_config,
            geometry=aoi,
            scale=250,
            tileScale=4
        )

        return {
            "status": "success",
            "analysis_type": "VCI",
            "source": "MODIS",
            "area": bbox_raw,
            "time_range": {
                "start_date": start_date,
                "end_date": end_date
            },
            "analysis": statistics,
            "image_url": vis_result['image_url'],
            "tile_url": vis_result['tile_url'],
            "legend": {
                conf_id: {
                    "label": conf_value["label"],
                    "color": conf_value["color"],
                    "range": conf_value["range"]
                } for conf_id, conf_value in class_config.items()
            }
        }
    except Exception as e:
        logging.getLogger("app").error(f"Lỗi hệ thống tính VCI: {str(e)}", exc_info=True)
        if "Không thể kết nối GEE" in str(e):
            return {
                "status": "error",
                "message": "Không thể kết nối GEE.",
                "detail": str(e)
            }
        return {
            "status": "error",
            "message": "Không thể tính VCI cho khu vực này.",
            "detail": str(e)
        }

@function_tool(timeout=200.0)
async def compute_TCI_MODIS_tool(bbox: str, start_date: str, end_date: str) -> str:
    """
    Tính toán TCI dựa trên dữ liệu LST từ MODIS cho một khu vực và khoảng thời gian nhất định.
    Args:
    - bbox: Danh sách tọa độ [min_lon, min_lat, max_lon, max_lat] dạng string.
    - start_date: Ngày bắt đầu định dạng 'YYYY-MM-DD'
    - end_date: Ngày kết thúc định dạng 'YYYY-MM-DD'
    Returns: Chuỗi JSON chứa kết quả phân tích TCI, bao gồm thống kê về diện tích và phần trăm diện tích cho từng lớp hạn hán, cùng với URL link hiển thị kết quả.
    """
    try:
        if isinstance(bbox, str):
            try:
                clean_bbox = bbox.replace("[", "").replace("]", "")
                aoi_geo = [float(coord.strip()) for coord in clean_bbox.split(",")]
            except ValueError:
                return json.dumps({
                    "status": "error",
                    "message": "Định dạng tọa độ bbox không hợp lệ từ Agent."
                }, ensure_ascii=False)
        else:
            aoi_geo = bbox

        with ProcessPool(max_workers=1) as pool:
            future = pool.schedule(
                _compute_tci_worker,
                args=(aoi_geo, start_date, end_date, bbox),
                timeout=200.0
            )
            result_dict = await asyncio.wrap_future(future)
            return json.dumps(result_dict, ensure_ascii=False)

    except PebbleTimeoutError:
        logger.error("Hệ thống xử lý TCI quá 200s, tiến trình đã bị hệ thống tiêu diệt.")
        return json.dumps({
            "status": "error",
            "message": (
                "Thời gian phân tích quá lâu (vượt quá 200 giây). Tác vụ đã tự động bị hủy "
                "để bảo vệ hệ thống. Vui lòng thử lại với một khu vực (bbox) nhỏ hơn."
            )
        }, ensure_ascii=False)
    except Exception as e:
        logging.getLogger("app").error(f"Lỗi hệ thống tính TCI: {str(e)}", exc_info=True)
        return json.dumps({
            "status": "error",
            "message": "Không thể xử lý TCI cho khu vực này.",
            "detail": str(e)
        }, ensure_ascii=False)

@function_tool(timeout=200.0)
async def compute_VHI_MODIS_tool(bbox: str, start_date: str, end_date: str) -> dict:
    """
    Tính toán Chỉ số Hạn hán Thực vật (VHI) dựa trên dữ liệu NDVI và LST từ MODIS cho một khu vực và khoảng thời gian nhất định.
    Args:
    - bbox: Danh sách tọa độ [min_lon, min_lat, max_lon, max_lat] dạng string.
    - start_date: Ngày bắt đầu định dạng 'YYYY-MM-DD'
    - end_date: Ngày kết thúc định dạng 'YYYY-MM-DD'
    Returns: Chuỗi JSON chứa kết quả phân tích VHI, bao gồm thống kê về diện tích và phần trăm diện tích cho từng lớp hạn hán, cùng với URL link hiển thị kết quả.
    """
    try:
        if isinstance(bbox, str):
            try:
                clean_bbox = bbox.replace("[", "").replace("]", "")
                aoi_geo = [float(coord.strip()) for coord in clean_bbox.split(",")]
            except ValueError:
                return json.dumps({
                    "status": "error",
                    "message": "Định dạng tọa độ bbox không hợp lệ từ Agent."
                }, ensure_ascii=False)
        else:
            aoi_geo = bbox

        with ProcessPool(max_workers=1) as pool:
            future = pool.schedule(
                _compute_vhi_worker,
                args=(aoi_geo, start_date, end_date, bbox),
                timeout=200.0
            )
            result_dict = await asyncio.wrap_future(future)
            return json.dumps(result_dict, ensure_ascii=False)

    except PebbleTimeoutError:
        logger.error("Hệ thống xử lý VHI quá 200s, tiến trình đã bị hệ thống tiêu diệt.")
        return json.dumps({
            "status": "error",
            "message": (
                "Thời gian phân tích quá lâu (vượt quá 200 giây). Tác vụ đã tự động bị hủy "
                "để bảo vệ hệ thống. Vui lòng thử lại với một khu vực (bbox) nhỏ hơn."
            )
        }, ensure_ascii=False)
    except Exception as e:
        logging.getLogger("app").error(f"Lỗi hệ thống tính VHI: {str(e)}", exc_info=True)
        return json.dumps({
            "status": "error",
            "message": "Không thể xử lý ảnh vệ tinh cho khu vực này.",
            "detail": str(e)
        }, ensure_ascii=False)

@function_tool(timeout=200.0)
async def compute_VCI_MODIS_tool (bbox: str, start_date: str, end_date: str) -> dict:
    """
    Tính toán Chỉ số Hạn hán Thực vật (VCI) dựa trên dữ liệu NDVI từ MODIS cho một khu vực và khoảng thời gian nhất định.
    Args:
    - bbox: Danh sách tọa độ [min_lon, min_lat, max_lon, max_lat] dạng string.
    - start_date: Ngày bắt đầu định dạng 'YYYY-MM-DD'
    - end_date: Ngày kết thúc định dạng 'YYYY-MM-DD'
    Returns: Chuỗi JSON chứa kết quả phân tích VCI, bao gồm thống kê về diện tích và phần trăm diện tích cho từng lớp hạn hán, cùng với URL link hiển thị kết quả.
    """
    try:
        if isinstance(bbox, str):
            try:
                clean_bbox = bbox.replace("[", "").replace("]", "")
                aoi_geo = [float(coord.strip()) for coord in clean_bbox.split(",")]
            except ValueError:
                return json.dumps({
                    "status": "error",
                    "message": "Định dạng tọa độ bbox không hợp lệ từ Agent."
                }, ensure_ascii=False)
        else:
            aoi_geo = bbox

        with ProcessPool(max_workers=1) as pool:
            future = pool.schedule(
                _compute_vci_worker,
                args=(aoi_geo, start_date, end_date, bbox),
                timeout=200.0
            )
            result_dict = await asyncio.wrap_future(future)
            return json.dumps(result_dict, ensure_ascii=False)

    except PebbleTimeoutError:
        logger.error("Hệ thống xử lý VCI quá 200s, tiến trình đã bị hệ thống tiêu diệt.")
        return json.dumps({
            "status": "error",
            "message": (
                "Thời gian phân tích quá lâu (vượt quá 200 giây). Tác vụ đã tự động bị hủy "
                "để bảo vệ hệ thống. Vui lòng thử lại với một khu vực (bbox) nhỏ hơn."
            )
        }, ensure_ascii=False)
    except Exception as e:
        logging.getLogger("app").error(f"Lỗi hệ thống tính VCI: {str(e)}", exc_info=True)
        return json.dumps({
            "status": "error",
            "message": "Không thể tính VCI cho khu vực này.",
            "detail": str(e)
        }, ensure_ascii=False)
