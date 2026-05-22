import asyncio
import json
from typing import Optional
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
    init_gee()


def _compute_ndvi_worker(aoi_input: list[float], start_date_i: str, end_date_i: str) -> dict:
    try:
        _init_gee_in_worker()

        aoi_geom = ee.Geometry.Rectangle(aoi_input)

        start_date = ee.Date(start_date_i)
        end_date = ee.Date(end_date_i)

        source_used = "Sentinel-2"
        img = _get_clean_sentinel(aoi_geom, start_date, end_date)
        if img is None:
            source_used = "Landsat 8/9"
            img = _get_clean_landsat(aoi_geom, start_date, end_date)

        if img is None:
            return {
                "status": "no_data",
                "message": (
                    "Không tìm thấy ảnh vệ tinh phù hợp (Sentinel 2 hoặc Landsat 8/9) "
                    "cho khu vực và khoảng thời gian đã chọn."
                )
            }

        class_config = {
            1: {"label": "Rất thấp - Đất trống/Nước", "color": "#0000FF", "range": "<0.0"},
            2: {"label": "Thấp - Đất trọc", "color": "#8B4513", "range": "0.0-0.1"},
            3: {"label": "Thực vật thưa thớt", "color": "#FFFF00", "range": "0.1-0.3"},
            4: {"label": "Thực vật trung bình", "color": "#90EE90", "range": "0.3-0.6"},
            5: {"label": "Thực vật dày đặc", "color": "#006400", "range": ">0.6"}
        }

        ndvi = img.normalizedDifference(['nir', 'red']).rename('NDVI').clip(aoi_geom)

        class_img = ndvi.expression(
            "idx < 0.0 ? 1 : idx < 0.1 ? 2 : idx < 0.3 ? 3 : idx < 0.6 ? 4 : 5",
            {"idx": ndvi}
        ).rename("class")

        world_cover = ee.ImageCollection("ESA/WorldCover/v200").first().clip(aoi_geom)
        non_urban_water_mask = world_cover.neq(50).And(world_cover.neq(80))
        classified_ndvi = class_img.updateMask(ndvi.mask()).updateMask(non_urban_water_mask)

        palette = [conf["color"] for conf in class_config.values()]
        vis_result = build_visualization(
            class_img=classified_ndvi,
            palette=palette,
            geometry=aoi_geom,
            dimensions=800
        )
        statistics = compute_area_statistics(
            class_img=classified_ndvi,
            labels=class_config,
            geometry=aoi_geom,
            scale=10 if source_used == "Sentinel-2" else 30,
            tileScale=4
        )

        return {
            "status": "success",
            "analysis_type": "NDVI",
            "source": source_used,
            "area": aoi_input,
            "time_range": {
                "start_date": start_date_i,
                "end_date": end_date_i
            },
            "analysis": statistics,
            "tile_url": vis_result["tile_url"],
            "image_url": vis_result["image_url"],
            "legend": {
                class_id: {
                    "label": conf["label"],
                    "range": conf["range"],
                    "color": conf["color"]
                }
                for class_id, conf in class_config.items()
            }
        }
    except Exception as e:
        logging.getLogger("app").error(f"Lỗi hệ thống tính NDVI: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": "Không thể xử lý NDVI cho khu vực này.",
            "detail": str(e)
        }


def _compute_nbr_worker(aoi_input: list[float], start_date_i: str, end_date_i: str) -> dict:
    try:
        _init_gee_in_worker()

        aoi_geom = ee.Geometry.Rectangle(aoi_input)

        start_date = ee.Date(start_date_i)
        end_date = ee.Date(end_date_i)

        source_used = "Sentinel-2"

        img = _get_clean_sentinel(aoi_geom, start_date, end_date)

        if img is None:
            source_used = "Landsat 8/9"
            img = _get_clean_landsat(aoi_geom, start_date, end_date)

        if img is None:
            return {
                "status": "no_data",
                "message": "Không tìm thấy ảnh vệ tinh phù hợp."
            }

        class_config = {
            1: {"label": "Rất thấp", "color": "#7a8737", "range": "<-0.5"},
            2: {"label": "Thấp", "color": "#acbe4d", "range": "-0.5 đến -0.1"},
            3: {"label": "Trung bình", "color": "#0ae042", "range": "-0.1 đến 0.1"},
            4: {"label": "Cao", "color": "#fff70b", "range": "0.1 đến 0.5"},
            5: {"label": "Rất cao", "color": "#8a2fc6", "range": ">0.5"}
        }

        nbr = img.normalizedDifference(['nir', 'swir2']).rename('NBR').clip(aoi_geom)

        class_img = nbr.expression(
            """
            idx < -0.5 ? 1 :
            idx < -0.1 ? 2 :
            idx < 0.1 ? 3 :
            idx < 0.5 ? 4 : 5
            """,
            {"idx": nbr}
        ).rename("class")

        world_cover = ee.ImageCollection("ESA/WorldCover/v200").first().clip(aoi_geom)
        non_urban_water_mask = world_cover.neq(50).And(world_cover.neq(80))
        classified_nbr = class_img.updateMask(nbr.mask()).updateMask(non_urban_water_mask)

        palette = [conf["color"] for conf in class_config.values()]

        vis_result = build_visualization(
            class_img=classified_nbr,
            palette=palette,
            geometry=aoi_geom,
            dimensions=800
        )

        statistics = compute_area_statistics(
            class_img=classified_nbr,
            labels=class_config,
            geometry=aoi_geom,
            scale=20 if source_used == "Sentinel-2" else 30,
            tileScale=4
        )

        return {
            "status": "success",
            "analysis_type": "NBR",
            "source": source_used,
            "area": aoi_input,
            "time_range": {
                "start_date": start_date_i,
                "end_date": end_date_i
            },
            "analysis": statistics,
            "tile_url": vis_result["tile_url"],
            "image_url": vis_result["image_url"],
            "legend": {
                class_id: {
                    "label": conf["label"],
                    "range": conf["range"],
                    "color": conf["color"]
                }
                for class_id, conf in class_config.items()
            }
        }
    except Exception as e:
        logging.getLogger("app").error(
            f"Lỗi hệ thống tính NBR: {str(e)}",
            exc_info=True
        )
        return {
            "status": "error",
            "message": "Không thể xử lý NBR cho khu vực này.",
            "detail": str(e)
        }


def _compute_ndbi_worker(aoi_input: list[float], start_date_i: str, end_date_i: str) -> dict:
    try:
        _init_gee_in_worker()

        aoi_geom = ee.Geometry.Rectangle(aoi_input)

        start_date = ee.Date(start_date_i)
        end_date = ee.Date(end_date_i)

        source_used = "Sentinel-2"

        img = _get_clean_sentinel(aoi_geom, start_date, end_date)

        if img is None:
            source_used = "Landsat 8/9"
            img = _get_clean_landsat(aoi_geom, start_date, end_date)

        if img is None:
            return {
                "status": "no_data",
                "message": "Không tìm thấy ảnh vệ tinh phù hợp."
            }

        class_config = {
            1: {
                "label": "Không xây dựng - Nước/Thực vật",
                "color": "#008000",
                "range": "<0.0"
            },
            2: {
                "label": "Vùng chuyển tiếp - Hỗn hợp",
                "color": "#ffff00",
                "range": "0.0-0.2"
            },
            3: {
                "label": "Xây dựng trung bình",
                "color": "#ff8800",
                "range": "0.2-0.5"
            },
            4: {
                "label": "Xây dựng dày đặc",
                "color": "#ff0000",
                "range": ">0.5"
            }
        }

        ndbi = img.normalizedDifference(['swir1', 'nir']).rename('NDBI').clip(aoi_geom)

        class_img = ndbi.expression(
            """
            idx < 0.0 ? 1 :
            idx < 0.2 ? 2 :
            idx < 0.5 ? 3 : 4
            """,
            {"idx": ndbi}
        ).rename("class")

        classified_ndbi = class_img.updateMask(ndbi.mask())

        palette = [conf["color"] for conf in class_config.values()]

        vis_result = build_visualization(
            class_img=classified_ndbi,
            palette=palette,
            geometry=aoi_geom,
            dimensions=800
        )

        statistics = compute_area_statistics(
            class_img=classified_ndbi,
            labels=class_config,
            geometry=aoi_geom,
            scale=20 if source_used == "Sentinel-2" else 30,
            tileScale=4
        )

        return {
            "status": "success",
            "analysis_type": "NDBI",
            "source": source_used,
            "area": aoi_input,
            "time_range": {
                "start_date": start_date_i,
                "end_date": end_date_i
            },
            "analysis": statistics,
            "tile_url": vis_result["tile_url"],
            "image_url": vis_result["image_url"],
            "legend": {
                class_id: {
                    "label": conf["label"],
                    "range": conf["range"],
                    "color": conf["color"]
                }
                for class_id, conf in class_config.items()
            }
        }
    except Exception as e:
        logging.getLogger("app").error(
            f"Lỗi hệ thống tính NDBI: {str(e)}",
            exc_info=True
        )
        return {
            "status": "error",
            "message": "Không thể xử lý NDBI cho khu vực này.",
            "detail": str(e)
        }


def _compute_ndwi_worker(aoi_input: list[float], start_date_i: str, end_date_i: str) -> dict:
    try:
        _init_gee_in_worker()

        aoi_geom = ee.Geometry.Rectangle(aoi_input)

        start_date = ee.Date(start_date_i)
        end_date = ee.Date(end_date_i)

        source_used = "Sentinel-2"

        img = _get_clean_sentinel(aoi_geom, start_date, end_date)

        if img is None:
            source_used = "Landsat 8/9"
            img = _get_clean_landsat(
                aoi_geom,
                start_date,
                end_date
            )

        if img is None:
            return {
                "status": "no_data",
                "message": (
                    "Không tìm thấy ảnh vệ tinh phù hợp (Sentinel 2 hoặc Landsat 8/9) "
                    "cho khu vực và khoảng thời gian đã chọn."
                )
            }

        ndwi = (img.normalizedDifference(['green', 'nir']).rename('NDWI').clip(aoi_geom))

        class_config = {
            1: {
                "label": "Rất khô / Không phải mặt nước",
                "color": "#8B4513",
                "range": "< -0.3"
            },
            2: {
                "label": "Không phải mặt nước",
                "color": "#D2B48C",
                "range": "-0.3 - 0.0"
            },
            3: {
                "label": "Đất ướt",
                "color": "#FFFF99",
                "range": "0.0 - 0.2"
            },
            4: {
                "label": "Mặt nước",
                "color": "#4FC3F7",
                "range": "0.2 - 0.5"
            },
            5: {
                "label": "Mặt nước",
                "color": "#00008B",
                "range": "> 0.5"
            }
        }

        class_img = ndwi.expression(
            """
            idx < -0.3 ? 1 :
            idx < 0.0 ? 2 :
            idx < 0.2 ? 3 :
            idx < 0.5 ? 4 : 5
            """,
            {"idx": ndwi}
        ).rename("class")

        classified_ndwi = class_img.updateMask(ndwi.mask())

        palette = [conf["color"] for conf in class_config.values()]

        vis_result = build_visualization(
            class_img=classified_ndwi,
            palette=palette,
            geometry=aoi_geom,
            dimensions=800
        )

        statistics = compute_area_statistics(
            class_img=classified_ndwi,
            labels=class_config,
            geometry=aoi_geom,
            scale=10 if source_used == "Sentinel-2" else 30,
            tileScale=4
        )

        return {
            "status": "success",
            "analysis_type": "NDWI",
            "source": source_used,
            "area": aoi_input,
            "time_range": {
                "start_date": start_date_i,
                "end_date": end_date_i
            },
            "analysis": statistics,
            "tile_url": vis_result["tile_url"],
            "image_url": vis_result["image_url"],
            "legend": {
                class_id: {
                    "label": conf["label"],
                    "range": conf["range"],
                    "color": conf["color"]
                }
                for class_id, conf in class_config.items()
            }
        }
    except Exception as e:
        logging.getLogger("app").error(
            f"Lỗi hệ thống tính NDWI: {str(e)}",
            exc_info=True
        )
        return {
            "status": "error",
            "message": "Không thể xử lý NDWI cho khu vực này.",
            "detail": str(e)
        }


def _compute_mndwi_worker(aoi_input: list[float], start_date_i: str, end_date_i: str) -> dict:
    try:
        _init_gee_in_worker()

        aoi_geom = ee.Geometry.Rectangle(aoi_input)

        start_date = ee.Date(start_date_i)
        end_date = ee.Date(end_date_i)

        source_used = "Sentinel-2"

        img = _get_clean_sentinel(aoi_geom, start_date, end_date)

        if img is None:
            source_used = "Landsat 8/9"
            img = _get_clean_landsat(
                aoi_geom,
                start_date,
                end_date
            )

        if img is None:
            return {
                "status": "no_data",
                "message": (
                    "Không tìm thấy ảnh vệ tinh phù hợp (Sentinel 2 hoặc Landsat 8/9) "
                    "cho khu vực và khoảng thời gian đã chọn."
                )
            }

        mndwi = (
            img.normalizedDifference(['green', 'swir1'])
            .rename('MNDWI')
            .clip(aoi_geom)
        )

        class_config = {
            1: {
                "label": "Khu đô thị / Bê tông / Nhựa đường",
                "color": "#4E342E",
                "range": "-1 - -0.2"
            },
            2: {
                "label": "Đất trống / Thực vật",
                "color": "#8BC34A",
                "range": "-0.2 - 0.0"
            },
            3: {
                "label": "Đất ẩm / Ruộng ngập nước",
                "color": "#FFF176",
                "range": "0.0 - 0.3"
            },
            4: {
                "label": "Mặt nước",
                "color": "#1E88E5",
                "range": "0.3 - 1"
            }
        }

        class_img = mndwi.expression(
            """
            idx < -0.2 ? 1 :
            idx < 0.0 ? 2 :
            idx < 0.3 ? 3 : 4
            """,
            {"idx": mndwi}
        ).rename("class")

        palette = [conf["color"]for conf in class_config.values()]

        vis_result = build_visualization(
            class_img=class_img,
            palette=palette,
            geometry=aoi_geom,
            dimensions=800
        )

        statistics = compute_area_statistics(
            class_img=class_img,
            labels=class_config,
            geometry=aoi_geom,
            scale=20 if source_used == "Sentinel-2" else 30,
            tileScale=4
        )

        return {
            "status": "success",
            "analysis_type": "NDWI",
            "source": source_used,
            "area": aoi_input,
            "time_range": {
                "start_date": start_date_i,
                "end_date": end_date_i
            },
            "analysis": statistics,
            "tile_url": vis_result["tile_url"],
            "image_url": vis_result["image_url"],
            "legend": {
                class_id: {
                    "label": conf["label"],
                    "range": conf["range"],
                    "color": conf["color"]
                }
                for class_id, conf in class_config.items()
            }
        }
    except Exception as e:
        logging.getLogger("app").error(
            f"Lỗi hệ thống tính NDWI: {str(e)}",
            exc_info=True
        )
        return {
            "status": "error",
            "message": "Không thể xử lý NDWI cho khu vực này.",
            "detail": str(e)
        }


def _compute_dnbr_worker(
    aoi_input: list[float],
    pre_start_date: str,
    pre_end_date: str,
    post_start_date: str,
    post_end_date: str
) -> dict:
    try:
        _init_gee_in_worker()

        aoi_geom = ee.Geometry.Rectangle(aoi_input)

        source_used = "Sentinel-2"

        pre_img = _get_clean_sentinel(
            aoi_geom,
            ee.Date(pre_start_date),
            ee.Date(pre_end_date)
        )

        post_img = _get_clean_sentinel(
            aoi_geom,
            ee.Date(post_start_date),
            ee.Date(post_end_date)
        )

        if pre_img is None or post_img is None:
            source_used = "Landsat 8/9"

            pre_img = _get_clean_landsat(
                aoi_geom,
                ee.Date(pre_start_date),
                ee.Date(pre_end_date)
            )

            post_img = _get_clean_landsat(
                aoi_geom,
                ee.Date(post_start_date),
                ee.Date(post_end_date)
            )

        if pre_img is None or post_img is None:
            return {
                "status": "no_data",
                "message": (
                    "Không tìm thấy dữ liệu ảnh "
                    "trước hoặc sau cháy."
                )
            }

        nbr_pre = (pre_img.normalizedDifference(['nir', 'swir2']).rename('NBR_PRE'))
        nbr_post = (post_img.normalizedDifference(['nir', 'swir2']).rename('NBR_POST'))

        dnbr = (nbr_pre.subtract(nbr_post).rename('dNBR').clip(aoi_geom))

        class_config = {
            1: {
                "label": "Thực vật phục hồi cao",
                "color": "#1B5E20",
                "range": "-1.0 - -0.1"
            },
            2: {
                "label": "Không bị cháy",
                "color": "#A5D6A7",
                "range": "-0.1 - 0.1"
            },
            3: {
                "label": "Mức độ cháy thấp",
                "color": "#FFF176",
                "range": "0.1 - 0.27"
            },
            4: {
                "label": "Mức độ cháy trung bình thấp",
                "color": "#FFB74D",
                "range": "0.27 - 0.44"
            },
            5: {
                "label": "Mức độ cháy trung bình cao",
                "color": "#F4511E",
                "range": "0.44 - 0.659"
            },
            6: {
                "label": "Mức độ cháy cao",
                "color": "#B71C1C",
                "range": "0.66 - 1.3"
            }
        }

        class_img = dnbr.expression(
            """
            idx < -0.1 ? 1 :
            idx < 0.1 ? 2 :
            idx < 0.27 ? 3 :
            idx < 0.44 ? 4 :
            idx < 0.66 ? 5 : 6
            """,
            {"idx": dnbr}
        ).rename("class")

        world_cover = ee.ImageCollection("ESA/WorldCover/v200").first().clip(aoi_geom)
        non_urban_water_mask = world_cover.neq(50).And(world_cover.neq(80))
        classified_dnbr = class_img.updateMask(dnbr.mask()).updateMask(non_urban_water_mask)

        palette = [conf["color"] for conf in class_config.values()]

        vis_result = build_visualization(
            class_img=classified_dnbr,
            palette=palette,
            geometry=aoi_geom,
            dimensions=800
        )

        statistics = compute_area_statistics(
            class_img=classified_dnbr,
            labels=class_config,
            geometry=aoi_geom,
            scale=10 if source_used == "Sentinel-2" else 30,
            tileScale=4
        )

        return {
            "status": "success",
            "analysis_type": "dNBR",
            "source": source_used,
            "area": aoi_input,
            "time_range": {
                "pre_fire": {
                    "start_date": pre_start_date,
                    "end_date": pre_end_date
                },
                "post_fire": {
                    "start_date": post_start_date,
                    "end_date": post_end_date
                }
            },
            "analysis": statistics,
            "tile_url": vis_result["tile_url"],
            "image_url": vis_result["image_url"],
            "legend": {
                class_id: {
                    "label": conf["label"],
                    "range": conf["range"],
                    "color": conf["color"]
                }
                for class_id, conf in class_config.items()
            }
        }
    except Exception as e:
        logging.getLogger("app").error(
            f"Lỗi hệ thống tính dNBR: {str(e)}",
            exc_info=True
        )
        return {
            "status": "error",
            "message": "Không thể xử lý dNBR.",
            "detail": str(e)
        }


def _compute_dvdi_worker(
    aoi_input: list[float],
    start_date_1: str,
    end_date_1: str,
    start_date_2: str,
    end_date_2: str,
    scale: int
) -> dict:
    try:
        _init_gee_in_worker()

        aoi = ee.Geometry.Rectangle(aoi_input)

        start_1 = ee.Date(start_date_1)
        end_1 = ee.Date(end_date_1)
        start_2 = ee.Date(start_date_2)
        end_2 = ee.Date(end_date_2)

        before_img = _get_clean_sentinel(aoi, start_1, end_1)
        if before_img is None:
            return {
                "status": "no_data",
                "message": (
                    "Không tìm thấy ảnh vệ tinh Sentinel 2 phù hợp cho khoảng thời gian "
                    f"{start_date_1} đến {end_date_1}."
                )
            }

        after_img = _get_clean_sentinel(aoi, start_2, end_2)
        if after_img is None:
            return {
                "status": "no_data",
                "message": (
                    "Không tìm thấy ảnh vệ tinh Sentinel 2 phù hợp cho khoảng thời gian "
                    f"{start_date_2} đến {end_date_2}."
                )
            }

        def get_ndvi(img):
            return img.normalizedDifference(['nir', 'red']).rename('NDVI')

        ndvi_before = get_ndvi(before_img)
        ndvi_after = get_ndvi(after_img)

        def fast_mask(image):
            scl = image.select('SCL')

            mask = (
                scl.neq(3)
                .And(scl.neq(8))
                .And(scl.neq(9))
                .And(scl.neq(10))
            )

            ndvi = (image.normalizedDifference(['B8', 'B4']).rename('NDVI'))

            return (
                image
                .updateMask(mask)
                .addBands(ndvi)
            )

        base_col = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
            .filterBounds(aoi)
            .map(fast_mask)
            .select('NDVI')
        )

        baseline_info_1 = get_historical_baseline(
            start_date_1,
            min_year=2018,
            requested_years_back=4,
            source_name="Sentinel-2"
        )
        if not baseline_info_1["is_valid"]:
            return {"status": "no_data", "message": baseline_info_1["error_msg"]}

        hist_start_1 = ee.Date.fromYMD(baseline_info_1["hist_year_start"], 1, 1)
        hist_end_1 = ee.Date.fromYMD(baseline_info_1["hist_year_end"], 12, 31)

        start_doy_1 = start_1.getRelative('day', 'year')
        end_doy_1 = end_1.getRelative('day', 'year')

        doy_filter_1 = ee.Algorithms.If(
            ee.Number(start_doy_1).lte(end_doy_1),
            ee.Filter.dayOfYear(start_doy_1, end_doy_1),
            ee.Filter.Or(ee.Filter.dayOfYear(start_doy_1, 366), ee.Filter.dayOfYear(1, end_doy_1))
        )

        baseline_before = base_col.filterDate(hist_start_1, hist_end_1).filter(doy_filter_1)

        baseline_info_2 = get_historical_baseline(
            start_date_2,
            min_year=2018,
            requested_years_back=4,
            source_name="Sentinel-2"
        )
        if not baseline_info_2["is_valid"]:
            return {"status": "no_data", "message": baseline_info_2["error_msg"]}

        hist_start_2 = ee.Date.fromYMD(baseline_info_2["hist_year_start"], 1, 1)
        hist_end_2 = ee.Date.fromYMD(baseline_info_2["hist_year_end"], 12, 31)

        start_doy_2 = start_2.getRelative('day', 'year')
        end_doy_2 = end_2.getRelative('day', 'year')

        doy_filter_2 = ee.Algorithms.If(
            ee.Number(start_doy_2).lte(end_doy_2),
            ee.Filter.dayOfYear(start_doy_2, end_doy_2),
            ee.Filter.Or(ee.Filter.dayOfYear(start_doy_2, 366), ee.Filter.dayOfYear(1, end_doy_2))
        )

        baseline_after = base_col.filterDate(hist_start_2, hist_end_2).filter(doy_filter_2)

        before_max = baseline_before.max()
        before_med = baseline_before.median()

        after_max = baseline_after.max()
        after_med = baseline_after.median()

        den_before = before_max.subtract(before_med)
        den_before = den_before.where(den_before.eq(0), 1e-6)

        mvci_before = (ndvi_before.subtract(before_med).divide(den_before))

        den_after = after_max.subtract(after_med)
        den_after = den_after.where(den_after.eq(0), 1e-6)

        mvci_after = (ndvi_after.subtract(after_med).divide(den_after))

        dvdi = mvci_after.subtract(mvci_before).rename('DVDI').clip(aoi)

        class_config = {
            1: {"label": "Thiệt hại đặc biệt nghiêm trọng: < -0.4 ", "color": "#8B0000", "range": "DVDI < -0.4"},
            2: {"label": "Thiệt hại rất nặng: -0.4 đến -0.3", "color": "#FF0000", "range": "-0.4 đến -0.3"},
            3: {"label": "Thiệt hại nặng: -0.3 đến -0.2", "color": "#FF8C00", "range": "-0.3 đến -0.2"},
            4: {"label": "Thiệt hại trung bình: -0.2 đến -0.1", "color": "#FFFF00", "range": "-0.2 đến -0.1"},
            5: {"label": "Thiệt hại nhẹ: -0.1 đến 0.0", "color": "#9ACD32", "range": "-0.1 đến 0.0"},
            6: {"label": "Không thiệt hại: >= 0.0", "color": "#008000", "range": "DVDI >= 0.0"}
        }

        class_img = dvdi.expression(
            "idx < -0.4 ? 1 : idx < -0.3 ? 2 : idx < -0.2 ? 3 : idx < -0.1 ? 4 : idx < 0.0 ? 5 : 6",
            {"idx": dvdi}
        ).rename("class")

        world_cover = ee.ImageCollection("ESA/WorldCover/v200").first().clip(aoi)
        non_urban_water_mask = world_cover.neq(50).And(world_cover.neq(80))
        classified_dvdi = class_img.updateMask(dvdi.mask()).updateMask(non_urban_water_mask)

        palette = [conf["color"] for conf in class_config.values()]
        vis_result = build_visualization(class_img=classified_dvdi, palette=palette, geometry=aoi, dimensions=800)
        statistics = compute_area_statistics(
            class_img=classified_dvdi,
            labels=class_config,
            geometry=aoi,
            scale=scale,
            tileScale=4
        )

        return {
            "status": "success",
            "analysis_type": "Delta NDVI",
            "source": "Sentinel-2",
            "area": aoi_input,
            "analysis": statistics,
            "image_url": vis_result['image_url'],
            "tile_url": vis_result['tile_url'],
            "legend": {conf_id: {"label": conf_value["label"], "color": conf_value["color"]} for conf_id, conf_value in class_config.items()}
        }
    except Exception as e:
        logging.getLogger("app").error(f"Lỗi hệ thống tính phổ: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": "Không thể xử lý ảnh vệ tinh cho khu vực này.",
            "detail": str(e)
        }


def _compute_activefire_worker(aoi_input: list[float], bbox_raw: str, start_date: str, end_date: str) -> dict:
    try:
        _init_gee_in_worker()

        aoi = ee.Geometry.Rectangle(aoi_input)

        base_col = (
            ee.ImageCollection('FIRMS')
            .select(['T21'])
            .filterBounds(aoi)
            .filterDate(start_date, end_date)
        )

        fires_max_temp = base_col.max().clip(aoi)

        class_config = {
            1: {"label": "Nhiệt độ 52°C - 67°C", "color": "#ffff00", "range": "325 <= T21 < 340"},
            2: {"label": "Nhiệt độ 67°C - 87°C", "color": "#ffa500", "range": "340 <= T21 < 360"},
            3: {"label": "Nhiệt độ trên 87°C", "color": "#ff0000", "range": "T21 >= 360"}
        }

        class_img = fires_max_temp.expression(
            "t21 >= 360 ? 3 : t21 >= 340 ? 2 : t21 >= 325 ? 1 : 0",
            {"t21": fires_max_temp.select('T21')}
        ).rename("class")

        classified_fires = class_img.updateMask(class_img.gt(0))

        stats = classified_fires.reduceRegion(
            reducer=ee.Reducer.max(),
            geometry=aoi,
            scale=1000,
            maxPixels=1e9
        ).getInfo()

        max_fire_class = stats.get('class')

        if max_fire_class is None:
            return {
                "status": "safe",
                "analysis_type": "Active_Fire_Detection",
                "source": "FIRMS",
                "area": bbox_raw,
                "time_range": {
                    "start_date": start_date,
                    "end_date": end_date
                },
                "message": (
                    "Khu vực hiện tại an toàn. Không phát hiện bức xạ nhiệt bất thường "
                    f"(>= 52°C) nào từ {start_date} đến {end_date}."
                )
            }

        palette = [conf["color"] for conf in class_config.values()]

        vis_result = build_visualization(class_img=classified_fires, palette=palette, geometry=aoi, dimensions=800)

        statistics = compute_area_statistics(
            class_img=classified_fires,
            labels=class_config,
            geometry=aoi,
            scale=1000,
            tileScale=4
        )

        return {
            "status": "danger",
            "analysis_type": "Active_Fire_Detection",
            "source": "FIRMS",
            "area": bbox_raw,
            "time_range": {
                "start_date": start_date,
                "end_date": end_date
            },
            "message": "CẢNH BÁO: Phát hiện bức xạ nhiệt bất thường tại khu vực yêu cầu. Vui lòng xem bản đồ phân lớp chi tiết.",
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
        logging.getLogger("app").error(f"Lỗi hệ thống tính FIRMS Active Fire: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "message": "Không thể xử lý dữ liệu nhiệt cho khu vực này.",
            "detail": str(e)
        }

def _get_clean_sentinel(aoi: ee.Geometry, start: str, end: str, cloud_pct: float = 50.0) -> Optional[ee.Image]:
    """Tải và lọc mây Sentinel-2 bằng SCL mask."""

    s2_col = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(aoi).filterDate(start, end).filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_pct))

    if s2_col.limit(1).size().getInfo() == 0:
        return None

    def mask_scl(img: ee.Image) -> ee.Image:
        scl = img.select('SCL')
        mask = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        return img.updateMask(mask).divide(10000).copyProperties(img, ['system:time_start'])

    img_final = s2_col.map(mask_scl).median().select(
        ['B3', 'B4', 'B8', 'B11', 'B12',],
        ['green', 'red', 'nir', 'swir1', 'swir2']
    ).clip(aoi)

    return img_final
def _get_clean_landsat(aoi: ee.Geometry, start: str, end: str, cloud_pct: float = 50) -> Optional[ee.Image]:
    """
    Gộp Landsat 8 & 9, lọc mây, chuẩn hóa giá trị phản xạ bề mặt.
    """

    def mask_ls_clouds(image: ee.Image) -> ee.Image:
        # Xử lý mặt nạ mây từ băng QA_PIXEL
        qa = image.select('QA_PIXEL')

        dilated_cloud = qa.bitwiseAnd(1 << 1).neq(0)
        cirrus = qa.bitwiseAnd(1 << 2).neq(0)
        cloud = qa.bitwiseAnd(1 << 3).neq(0)
        shadow = qa.bitwiseAnd(1 << 4).neq(0)
        snow = qa.bitwiseAnd(1 << 5).neq(0)
        mask = dilated_cloud.Or(cirrus).Or(cloud).Or(shadow).Or(snow).Not()

        optical_bands = image.select('SR_B.').multiply(0.0000275).add(-0.2)

        return image.addBands(optical_bands, None, True).updateMask(mask).copyProperties(image, ["system:time_start"])

    l8 = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
    l9 = ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")

    collection = (l8.merge(l9)
                  .filterBounds(aoi)
                  .filterDate(start, end)
                  .filter(ee.Filter.lt('CLOUD_COVER', cloud_pct)))

    if collection.limit(1).size().getInfo() == 0:
        return None

    img = (collection.map(mask_ls_clouds)
           .median()
           .select(
                ['SR_B3', 'SR_B4', 'SR_B5', 'SR_B6', 'SR_B7'],
                ['green', 'red', 'nir', 'swir1', 'swir2']
            )
           .clip(aoi))

    return img

def _mask_modis_ndvi(img):
    """
    Hàm gộp: Vừa lọc mây vừa lấy NDVI cho MODIS.
    """
    qa = img.select('SummaryQA')
    qa_extracted = qa.bitwiseAnd(3)
    mask = qa_extracted.lte(1)

    ndvi = img.updateMask(mask).select('NDVI').multiply(0.0001)
    return ndvi.copyProperties(img, ['system:time_start', 'system:time_end'])

@function_tool(timeout=200.0)
async def compute_NDVI_tool (aoi_input: str, start_date_i: str, end_date_i: str) -> str:
    """
    Tính toán chỉ số NDVI cho một khu vực và khoảng thời gian nhất định dựa trên ảnh vệ tinh Sentinel-2 hoặc Landsat 8/9.
    Args:
    - aoi_input: tọa độ "min_lon, min_lat, max_lon, max_lat" dạng string xác định khu vực quan tâm.
    - start_date_i: Ngày bắt đầu phân tích theo định dạng "YYYY-MM-DD".
    - end_date_i: Ngày kết thúc phân tích theo định dạng "YYYY-MM-DD".
    Returns: Chuỗi JSON chứa kết quả thống kê về NDVI, bao gồm diện tích và phần trăm diện tích cho từng lớp, cùng với URL tải file raster kết quả.
    """
    try:
        if isinstance(aoi_input, str):
            try:
                clean_input = aoi_input.replace("[", "").replace("]", "")
                aoi_input = [float(coord.strip()) for coord in clean_input.split(",")]
            except ValueError:
                return json.dumps({
                    "status": "error",
                    "message": "Định dạng tọa độ bbox không hợp lệ từ Agent."
                }, ensure_ascii=False)

        with ProcessPool(max_workers=1) as pool:
            future = pool.schedule(
                _compute_ndvi_worker,
                args=(aoi_input, start_date_i, end_date_i),
                timeout=200.0
            )
            result_dict = await asyncio.wrap_future(future)
            return json.dumps(result_dict, ensure_ascii=False)

    except PebbleTimeoutError:
        logger.error("Hệ thống xử lý NDVI quá 200s, tiến trình đã bị hệ thống tiêu diệt.")
        return json.dumps({
            "status": "error",
            "message": (
                "Thời gian phân tích quá lâu (vượt quá 200 giây). Tác vụ đã tự động bị hủy "
                "để bảo vệ hệ thống. Vui lòng thử lại với một khu vực (bbox) nhỏ hơn."
            )
        }, ensure_ascii=False)
    except Exception as e:
        logging.getLogger("app").error(f"Lỗi hệ thống tính NDVI: {str(e)}", exc_info=True)
        return json.dumps({
            "status": "error",
            "message": "Không thể xử lý NDVI cho khu vực này.",
            "detail": str(e)
        }, ensure_ascii=False)

@function_tool(timeout=200.0)
async def compute_NBR_tool(aoi_input: str, start_date_i: str, end_date_i: str) -> str:
    """
    Tính chỉ số NBR cho một khu vực và khoảng thời gian nhất định.
    Args:
    - aoi_input: tọa độ [min_lon, min_lat, max_lon, max_lat] dạng string xác định khu vực quan tâm.
    - start_date_i: Ngày bắt đầu phân tích theo định dạng "YYYY-MM-DD".
    - end_date_i: Ngày kết thúc phân tích theo định dạng "YYYY-MM-DD".
    Returns: Chuỗi JSON chứa kết quả thống kê về NBR, bao gồm diện tích và phần trăm diện tích cho từng lớp, cùng với URL tải file raster kết quả.
    """

    try:
        if isinstance(aoi_input, str):
            try:
                clean_input = aoi_input.replace("[", "").replace("]", "")
                aoi_input = [float(coord.strip()) for coord in clean_input.split(",")]
            except ValueError:
                return json.dumps({
                    "status": "error",
                    "message": "Định dạng tọa độ bbox không hợp lệ từ Agent."
                }, ensure_ascii=False)

        with ProcessPool(max_workers=1) as pool:
            future = pool.schedule(
                _compute_nbr_worker,
                args=(aoi_input, start_date_i, end_date_i),
                timeout=200.0
            )
            result_dict = await asyncio.wrap_future(future)
            return json.dumps(result_dict, ensure_ascii=False)

    except PebbleTimeoutError:
        logger.error("Hệ thống xử lý NBR quá 200s, tiến trình đã bị hệ thống tiêu diệt.")
        return json.dumps({
            "status": "error",
            "message": (
                "Thời gian phân tích quá lâu (vượt quá 200 giây). Tác vụ đã tự động bị hủy "
                "để bảo vệ hệ thống. Vui lòng thử lại với một khu vực (bbox) nhỏ hơn."
            )
        }, ensure_ascii=False)
    except Exception as e:
        logging.getLogger("app").error(
            f"Lỗi hệ thống tính NBR: {str(e)}",
            exc_info=True
        )
        return json.dumps({
            "status": "error",
            "message": "Không thể xử lý NBR cho khu vực này.",
            "detail": str(e)
        }, ensure_ascii=False)

@function_tool(timeout=200.0)
async def compute_NDBI_tool(aoi_input: str, start_date_i: str, end_date_i: str) -> str:
    """
    Tính toán chỉ số NDBI cho một khu vực và khoảng thời gian nhất định.
    Args:
    - aoi_input: tọa độ [min_lon, min_lat, max_lon, max_lat] dạng string xác định khu vực quan tâm.
    - start_date_i: Ngày bắt đầu phân tích theo định dạng "YYYY-MM-DD".
    - end_date_i: Ngày kết thúc phân tích theo định dạng "YYYY-MM-DD".
    Returns: Chuỗi JSON chứa kết quả thống kê về NDBI, bao gồm diện tích và phần trăm diện tích cho từng lớp, cùng với URL tải file raster kết quả.
    """

    try:
        if isinstance(aoi_input, str):
            try:
                clean_input = aoi_input.replace("[", "").replace("]", "")
                aoi_input = [float(coord.strip()) for coord in clean_input.split(",")]
            except ValueError:
                return json.dumps({
                    "status": "error",
                    "message": "Định dạng tọa độ bbox không hợp lệ từ Agent."
                }, ensure_ascii=False)

        with ProcessPool(max_workers=1) as pool:
            future = pool.schedule(
                _compute_ndbi_worker,
                args=(aoi_input, start_date_i, end_date_i),
                timeout=200.0
            )
            result_dict = await asyncio.wrap_future(future)
            return json.dumps(result_dict, ensure_ascii=False)

    except PebbleTimeoutError:
        logger.error("Hệ thống xử lý NDBI quá 200s, tiến trình đã bị hệ thống tiêu diệt.")
        return json.dumps({
            "status": "error",
            "message": (
                "Thời gian phân tích quá lâu (vượt quá 200 giây). Tác vụ đã tự động bị hủy "
                "để bảo vệ hệ thống. Vui lòng thử lại với một khu vực (bbox) nhỏ hơn."
            )
        }, ensure_ascii=False)
    except Exception as e:
        logging.getLogger("app").error(
            f"Lỗi hệ thống tính NDBI: {str(e)}",
            exc_info=True
        )
        return json.dumps({
            "status": "error",
            "message": "Không thể xử lý NDBI cho khu vực này.",
            "detail": str(e)
        }, ensure_ascii=False)

@function_tool(timeout=200.0)
async def compute_NDWI_tool(aoi_input: str, start_date_i: str,end_date_i: str) -> str:
    """
    Tính toán chỉ số NDWI cho một khu vực và khoảng thời gian nhất định dựa trên ảnh vệ tinh Sentinel-2 hoặc Landsat 8/9.

    Args:
    - aoi_input:tọa độ dạng string "min_lon, min_lat, max_lon, max_lat"
    - start_date_i: Ngày bắt đầu phân tích theo định dạng YYYY-MM-DD
    - end_date_i: Ngày kết thúc phân tích theo định dạng YYYY-MM-DD
    Returns: Chuỗi JSON chứa kết quả thống kê về NDBI, bao gồm diện tích và phần trăm diện tích cho từng lớp, cùng với URL tải file raster kết quả.
    """

    try:
        if isinstance(aoi_input, str):
            try:
                clean_input = (aoi_input.replace("[", "").replace("]", ""))
                aoi_input = [float(coord.strip())for coord in clean_input.split(",")]

            except ValueError:
                return json.dumps({
                    "status": "error",
                    "message": "Định dạng tọa độ bbox không hợp lệ từ Agent."
                }, ensure_ascii=False)

        with ProcessPool(max_workers=1) as pool:
            future = pool.schedule(
                _compute_ndwi_worker,
                args=(aoi_input, start_date_i, end_date_i),
                timeout=200.0
            )
            result_dict = await asyncio.wrap_future(future)
            return json.dumps(result_dict, ensure_ascii=False)

    except PebbleTimeoutError:
        logger.error("Hệ thống xử lý NDWI quá 200s, tiến trình đã bị hệ thống tiêu diệt.")
        return json.dumps({
            "status": "error",
            "message": (
                "Thời gian phân tích quá lâu (vượt quá 200 giây). Tác vụ đã tự động bị hủy "
                "để bảo vệ hệ thống. Vui lòng thử lại với một khu vực (bbox) nhỏ hơn."
            )
        }, ensure_ascii=False)
    except Exception as e:

        logging.getLogger("app").error(
            f"Lỗi hệ thống tính NDWI: {str(e)}",
            exc_info=True
        )
        return json.dumps({
            "status": "error",
            "message": "Không thể xử lý NDWI cho khu vực này.",
            "detail": str(e)

        }, ensure_ascii=False)

@function_tool(timeout=200.0)
async def compute_MNDWI_tool(aoi_input: str, start_date_i: str, end_date_i: str) -> str:
    """
    Tính toán chỉ số MNDWI cho một khu vực và khoảng thời gian nhất định.
    Args:
    - aoi_input: tọa độ [min_lon, min_lat, max_lon, max_lat] dạng string xác định khu vực quan tâm.
    - start_date_i: Ngày bắt đầu phân tích theo định dạng "YYYY-MM-DD".
    - end_date_i: Ngày kết thúc phân tích theo định dạng "YYYY-MM-DD".
    Returns: Chuỗi JSON chứa kết quả thống kê về MNDWI, bao gồm diện tích và phần trăm diện tích cho từng lớp, cùng với URL tải file raster kết quả.
    """
   
    try:
        if isinstance(aoi_input, str):
            try:
                clean_input = (aoi_input.replace("[", "").replace("]", ""))
                aoi_input = [float(coord.strip())for coord in clean_input.split(",")]

            except ValueError:
                return json.dumps({
                    "status": "error",
                    "message": "Định dạng tọa độ bbox không hợp lệ từ Agent."
                }, ensure_ascii=False)

        with ProcessPool(max_workers=1) as pool:
            future = pool.schedule(
                _compute_mndwi_worker,
                args=(aoi_input, start_date_i, end_date_i),
                timeout=200.0
            )
            result_dict = await asyncio.wrap_future(future)
            return json.dumps(result_dict, ensure_ascii=False)

    except PebbleTimeoutError:
        logger.error("Hệ thống xử lý MNDWI quá 200s, tiến trình đã bị hệ thống tiêu diệt.")
        return json.dumps({
            "status": "error",
            "message": (
                "Thời gian phân tích quá lâu (vượt quá 200 giây). Tác vụ đã tự động bị hủy "
                "để bảo vệ hệ thống. Vui lòng thử lại với một khu vực (bbox) nhỏ hơn."
            )
        }, ensure_ascii=False)
    except Exception as e:

        logging.getLogger("app").error(
            f"Lỗi hệ thống tính NDWI: {str(e)}",
            exc_info=True
        )
        return json.dumps({
            "status": "error",
            "message": "Không thể xử lý NDWI cho khu vực này.",
            "detail": str(e)

        }, ensure_ascii=False)

@function_tool(timeout=200.0)
async def compute_dNBR_tool(aoi_input: str, pre_start_date: str, pre_end_date: str, post_start_date: str, post_end_date: str) -> str:
    """
    Tính toán dNBR để đánh giá mức độ cháy rừng.
    Args:
    - aoi_input: tọa độ [min_lon, min_lat, max_lon, max_lat] dạng string xác định khu vực quan tâm.
    - pre_start_date: Ngày bắt đầu cho ảnh trước cháy (YYYY-MM-DD).
    - pre_end_date: Ngày kết thúc cho ảnh trước cháy (YYYY-MM-DD).
    - post_start_date: Ngày bắt đầu cho ảnh sau cháy (YYYY-MM-DD).
    - post_end_date: Ngày kết thúc cho ảnh sau cháy (YYYY-MM-DD).
    Returns: Chuỗi JSON chứa kết quả phân loại mức độ cháy, bao gồm diện tích và phần trăm diện tích cho từng lớp, cùng với URL tải file raster kết quả.
    """

    try:
        if isinstance(aoi_input, str):
            try:

                clean_input = (aoi_input.replace("[", "").replace("]", ""))
                aoi_input = [float(coord.strip()) for coord in clean_input.split(",")]
            except ValueError:

                return json.dumps({
                    "status": "error",
                    "message": "Định dạng tọa độ bbox không hợp lệ."
                }, ensure_ascii=False)

        with ProcessPool(max_workers=1) as pool:
            future = pool.schedule(
                _compute_dnbr_worker,
                args=(aoi_input, pre_start_date, pre_end_date, post_start_date, post_end_date),
                timeout=200.0
            )
            result_dict = await asyncio.wrap_future(future)
            return json.dumps(result_dict, ensure_ascii=False)

    except PebbleTimeoutError:
        logger.error("Hệ thống xử lý dNBR quá 200s, tiến trình đã bị hệ thống tiêu diệt.")
        return json.dumps({
            "status": "error",
            "message": (
                "Thời gian phân tích quá lâu (vượt quá 200 giây). Tác vụ đã tự động bị hủy "
                "để bảo vệ hệ thống. Vui lòng thử lại với một khu vực (bbox) nhỏ hơn."
            )
        }, ensure_ascii=False)
    except Exception as e:

        logging.getLogger("app").error(
            f"Lỗi hệ thống tính dNBR: {str(e)}",
            exc_info=True
        )

        return json.dumps({

            "status": "error",

            "message": "Không thể xử lý dNBR.",

            "detail": str(e)

        }, ensure_ascii=False)

@function_tool(timeout=200.0)
async def compute_DVDI_tool (bbox: str, start_date_1: str, end_date_1: str, start_date_2: str, end_date_2: str, scale: int = 10) -> str:
    """
    Tính toán chỉ số DVDI cho một khu vực nhất định.
    Args:
    - bbox: Danh sách tọa độ [min_lon, min_lat, max_lon, max_lat] xác định khu vực quan tâm.
    - start_date_1: Ngày bắt đầu của khoảng thời gian đầu tiên theo định dạng "YYYY-MM-DD".
    - end_date_1: Ngày kết thúc của khoảng thời gian đầu tiên theo định dạng "YYYY-MM-DD".
    - start_date_2: Ngày bắt đầu của khoảng thời gian thứ hai theo định dạng "YYYY-MM-DD".
    - end_date_2: Ngày kết thúc của khoảng thời gian thứ hai theo định dạng "YYYY-MM-DD".
    - scale: Độ phân giải của ảnh (mặc định là 10m).
    Returns: Chuỗi JSON chứa kết quả phân tích sự thay đổi NDVI, bao gồm thống kê về diện tích và phần trăm diện tích có sự thay đổi tích cực, tiêu cực và không đổi, cùng với URL link hiển thị kết quả.
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
                _compute_dvdi_worker,
                args=(aoi_geo, start_date_1, end_date_1, start_date_2, end_date_2, scale),
                timeout=200.0
            )
            result_dict = await asyncio.wrap_future(future)
            return json.dumps(result_dict, ensure_ascii=False)

    except PebbleTimeoutError:
        logger.error("Hệ thống xử lý DVDI quá 200s, tiến trình đã bị hệ thống tiêu diệt.")
        return json.dumps({
            "status": "error",
            "message": (
                "Thời gian phân tích quá lâu (vượt quá 200 giây). Tác vụ đã tự động bị hủy "
                "để bảo vệ hệ thống. Vui lòng thử lại với một khu vực (bbox) nhỏ hơn."
            )
        }, ensure_ascii=False)
    except Exception as e:
        logging.getLogger("app").error(f"Lỗi hệ thống tính phổ: {str(e)}", exc_info=True)
        return json.dumps({
            "status": "error",
            "message": "Không thể xử lý ảnh vệ tinh cho khu vực này.",
            "detail": str(e)
        }, ensure_ascii=False)


@function_tool(timeout=200.0)
async def compute_ActiveFire_FIRMS(bbox: str, start_date: str, end_date: str) -> str:
    """
    Phát hiện điểm cháy dựa trên dữ liệu nhiệt (băng tần T21) từ FIRMS.
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
                _compute_activefire_worker,
                args=(aoi_geo, bbox, start_date, end_date),
                timeout=200.0
            )
            result_dict = await asyncio.wrap_future(future)
            return json.dumps(result_dict, ensure_ascii=False)

    except PebbleTimeoutError:
        logger.error("Hệ thống xử lý FIRMS Active Fire quá 200s, tiến trình đã bị hệ thống tiêu diệt.")
        return json.dumps({
            "status": "error",
            "message": (
                "Thời gian phân tích quá lâu (vượt quá 200 giây). Tác vụ đã tự động bị hủy "
                "để bảo vệ hệ thống. Vui lòng thử lại với một khu vực (bbox) nhỏ hơn."
            )
        }, ensure_ascii=False)
    except Exception as e:
        logging.getLogger("app").error(f"Lỗi hệ thống tính FIRMS Active Fire: {str(e)}", exc_info=True)
        return json.dumps({
            "status": "error",
            "message": "Không thể xử lý dữ liệu nhiệt cho khu vực này.",
            "detail": str(e)
        }, ensure_ascii=False)
