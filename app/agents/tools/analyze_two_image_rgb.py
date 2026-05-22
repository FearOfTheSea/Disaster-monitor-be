import asyncio
from datetime import datetime, timedelta
import json
import math
from typing import Any, Dict, List, Optional
from pystac_client import Client
import planetary_computer
from agents import function_tool
import httpx
import logging
from app.core.llm_clients import get_gemini_client
from app.services.satellite.planetary_client import get_planetary_client
from app.services.satellite.stac_search_service import search_target_image
from app.services.satellite.get_satellite_tilejson import get_satellite_tilejson_url

logger = logging.getLogger(__name__)
gemini_client = get_gemini_client()

async def _fetch_tile_image(tilejson_url: str, lat: float, lon: float) -> Optional[str]:
    """
    Trích xuất 1 tile bản đồ  dạng PNG ở mức zoom 13 từ dịch vụ TileJSON dựa trên tọa độ địa lý.
    Hàm thực hiện quy trình:
    1. Truy vấn TileJSON để lấy mẫu URL của các ô lưới .
    2. Chuyển đổi tọa độ Kinh độ/Vĩ độ sang hệ thống chỉ số ô lưới XYZtheo chuẩn Web Mercator
        tại mức thu phóng (zoom level) 13.
    3. Tải dữ liệu ảnh PNG thực tế từ máy chủ cung cấp bản đồ.
    4. Mã hóa nội dung ảnh sang định dạng Base64.

    Args:
        tilejson_url (str): Đường dẫn API trả về cấu hình TileJSON.
        lat (float): Vĩ độ (Latitude) của điểm cần trích xuất hình ảnh.
        lon (float): Kinh độ (Longitude) của điểm cần trích xuất hình ảnh.

    Returns:
        Optional[str]: Link tile ảnh nếu thành công.
                       Trả về None nếu có lỗi mạng, lỗi HTTP hoặc không tìm thấy tile ảnh.
    """
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            logger.info(f"Đang tải ảnh từ TileJSON URL: {tilejson_url}")
            res = await client.get(tilejson_url)
            if res.status_code !=200:
                logger.info(f"Không tải ảnh thành công!")
                return None
            else:
                tilejson = res.json()
                tiles_template = tilejson.get("tiles", [None])[0]

            if not tiles_template:
                logger.error("TileJSON không chứa đường link 'tiles' template hợp lệ.")
                return None

            z = 13
            n = 2.0 ** z
            lat_rad = math.radians(lat)
            x = int((lon + 180.0) / 360.0 * n)
            y = int((1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi) / 2.0 * n)

            tile_image_url = tiles_template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
            logger.info(f"Đang tải ảnh PNG tại ô lưới XYZ ({x}, {y}, {z})...")
            img_res = await client.get(tile_image_url)

            if img_res.status_code != 200:
                logger.error("Tile image không tồn tại.")
                return None

            return tile_image_url
    except Exception as e:
        logger.error(f"Lỗi khi lấy tile ảnh: {e}")
        return None

def _search_before_image(bbox: List[float], target_date_str: str) -> Optional[List[Dict[str, Any]]]:
    """
    Tìm kiếm ảnh vệ tinh Sentinel-2 trước ngày cần phân tích nếu không có ngày cho trước.
    Nếu không tìm thấy, sẽ lùi dần xuống 30 ngày một lần, tối đa 2 lần (tìm trong khoảng 60 ngày trước đó).
    """
    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
    except ValueError:
        logger.error("Định dạng ngày không hợp lệ. Vui lòng dùng YYYY-MM-DD.")
        return None

    try:
        catalog = get_planetary_client()
    except Exception as e:
        logger.error(f"Lỗi khởi tạo STAC Client: {e}")
        return None

    search_body = {
        "collections": ["sentinel-2-l2a"],
        "bbox": bbox,
        "query": {"eo:cloud_cover": {"lt": 65}},
        "limit": 20,
        "sortby": [{"field": "datetime", "direction": "desc"}]
    }

    current_end_date = target_date - timedelta(days=1)
    max_retries = 2

    for attempt in range(max_retries):
        current_start_date = current_end_date - timedelta(days=30)
        time_range = f"{current_start_date.strftime('%Y-%m-%d')}T00:00:00Z/{current_end_date.strftime('%Y-%m-%d')}T23:59:59Z"

        search_body["datetime"] = time_range
        logger.info(f"Lần thử {attempt + 1}/{max_retries}: Tìm ảnh trong khoảng thời gian {time_range}")

        try:
            search = catalog.search(**search_body)
            items = [item.to_dict() for item in search.get_items()]

            if items:
                selected_item = min(items, key=lambda x: x.get("properties", {}).get("eo:cloud_cover", 100))
                return [selected_item]
            else:
                logger.warning(f"Không có ảnh. Lùi xuống 30 ngày...")

        except Exception as e:
            logger.error(f"Lỗi STAC API ở lần thử {attempt + 1}: {e}")

        current_end_date = current_start_date - timedelta(days=1)

    logger.error("Thất bại: Đã thử tìm trong hai tháng nhưng không có ảnh.")
    return None

def _parse_coordinates_to_bbox(lat: float, lon: float, buffer_deg: float = 0.2) -> Optional[List[float]]:
    """
    Tạo khung bbox từ một điểm tọa độ.
    Args:
        lat (float): Vĩ độ tâm.
        lon (float): Kinh độ tâm.
        buffer_deg (float): Vùng đệm mở rộng (mặc định 0.2 xấp xỉ 22 km).

    Returns:
        Optional[List[float]]: Danh sách tọa độ bbox theo định dạng [min_lon, min_lat, max_lon, max_lat] hoặc None nếu tọa độ sai.
    """
    try:
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return [lon - buffer_deg, lat - buffer_deg, lon + buffer_deg, lat + buffer_deg]
        if -90 <= lon <= 90 and -180 <= lat <= 180:
            return [lat - buffer_deg, lon - buffer_deg, lat + buffer_deg, lon + buffer_deg]
    except ValueError:
        pass
    return None


async def _tile_url_to_base64(tile_url: str) -> Optional[str]:
    """Chuyển đổi URL của tile ảnh thành chuỗi Base64.
    Args:  tile_url (str): URL của tile ảnh cần chuyển đổi.
    Returns: Optional[str]: Chuỗi Base64 của ảnh nếu thành công, hoặc None nếu có lỗi.
    """
    import base64
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:

            response = await client.get(tile_url)

            response.raise_for_status()

            image_b64 = base64.b64encode(response.content).decode("utf-8")

            return image_b64

    except Exception as e:
        logger.error(f"Lỗi convert tile sang base64: {e}")
        return None

@function_tool
async def search_two_satellite_rgb_image_and_get_tilejson(lat: float, lon: float, target_date: str, before_date: Optional[str] = None) -> str:
    """
    Tìm kiếm và truy xuất TileJSON của ảnh vệ tinh RGB tại một tọa độ cho hai thời điểm (Trước và Sau).

    Sử dụng công cụ (Tool) này khi:
    - Người dùng yêu cầu phân tích tại thiệt hại hoặc thay đổi tại một địa điểm tại khoảng thời gian hoặc một ngày cố đinh bằng ảnh rgb.
    - Cần so sánh trực quan sự thay đổi cảnh quan (Before & After) do tác động của thiên tai (bão, lụt, sạt lở).
    - Cần lấy dữ liệu để hiển thị bản đồ so sánh hai thời điểm trên giao diện người dùng.

    Logic hoạt động của ngày đối chứng (before_date):
    - Nếu có 'before_date': Hệ thống sẽ cố gắng tìm ảnh vệ tinh chính xác tại ngày đó.
    - Nếu KHÔNG có 'before_date': Hệ thống tự động lùi lại 30 ngày từ 'target_date' để quét tìm tấm ảnh quá khứ tốt nhất.

    Args:
        lat (float): Vĩ độ tâm của khu vực cần quan sát.
        lon (float): Kinh độ tâm của khu vực cần quan sát.
        target_date (str): Ngày xảy ra sự kiện hoặc ngày mục tiêu (định dạng YYYY-MM-DD).
        before_date (Optional[str]): Ngày đối chứng trong quá khứ (định dạng YYYY-MM-DD). Mặc định là None.

    Returns:
        str: Chuỗi JSON chứa kết quả truy xuất.
            Nếu thành công, trả về trạng thái, tọa độ khung nhìn (bbox), tọa độ tâm và dữ liệu TileJSON của hai thời điểm:
            {
                "status": "success",
                "bbox": [min_lon, min_lat, max_lon, max_lat],
                "center": {"lat": lat, "lng": lon},
                "before": {
                    "tilejson_url": "...",
                    "metadata": {
                        "source": "collection_name",
                        "date": "actual_before_date",
                        "bbox_used": [min_lon, min_lat, max_lon, max_lat]
                    }
                },
                "after": {
                    "tilejson_url": "...",
                    "metadata": {
                        "source": "collection_name",
                        "date": "actual_target_date",
                        "bbox_used": [min_lon, min_lat, max_lon, max_lat]
                    }
                }
            }
            Nếu thất bại, trả về JSON báo lỗi: {"status": "error", "message": "lý do lỗi"}.
    """
    bbox = _parse_coordinates_to_bbox(lat, lon)
    if not bbox:
        logger.error("Không thể tạo bbox từ tọa độ đã cho.")
        return json.dumps({"status": "error", "message": f"Không thể xác định vị trí: '{lat}, {lon}'"})

    target_items = search_target_image(bbox, target_date)
    if not target_items:
        logger.warning(f"Không tìm thấy ảnh vệ tinh cho ngày {target_date}.")
        return json.dumps({"status": "error", "message": f"Không tìm thấy ảnh vệ tinh cho ngày {target_date}."})

    if not before_date:
        before_date = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        before_items = _search_before_image(bbox, before_date)
        if not before_items:
            logger.warning(f"Không tìm thấy ảnh vệ tinh trước ngày {target_date}.")
            return json.dumps({"status": "error", "message": f"Không tìm thấy ảnh vệ tinh trước ngày {target_date}."})
        actual_before_date = before_items[0].get("properties", {}).get("datetime", "")[:10]
    else:
        before_items = search_target_image(bbox, before_date)
        if not before_items:
            logger.warning(f"Không tìm thấy ảnh vệ tinh cho ngày trước đó {before_date}.")
            return json.dumps({"status": "error", "message": f"Không tìm thấy ảnh vệ tinh cho ngày trước đó {before_date}."})
        actual_before_date = before_date

    before_tilejson_info = await get_satellite_tilejson_url(before_items, bbox, actual_before_date)
    target_tilejson_info = await get_satellite_tilejson_url(target_items, bbox, target_date)
    if not before_tilejson_info or not target_tilejson_info:
        logger.error("Không thể tạo TileJSON URL cho một trong hai ngày.")
        return json.dumps({"status": "error", "message": "Không thể tạo TileJSON URL cho một trong hai ngày."})

    result = {
        "status": "success",
        "bbox": bbox,
        "center": {"lat": lat, "lng": lon},
        "before": {
            "tilejson_url": before_tilejson_info.get("tilejson_url"),
            "source": "collection_name",
            "date": before_date,
            },
        "after": {
            "tilejson_url": target_tilejson_info.get("tilejson_url"),
            "source": "collection_name",
            "date": target_date,
                }
            }
    return json.dumps(result)

@function_tool
async def analyze_image_comparison(before_tilejson_url: str, target_tilejson_url: str, lat: float, lon: float, before_date: Optional[str], target_date: str) -> str:
    """
    Phân tích sự thay đổi giữa hai ảnh vệ tinh (Trước và Sau) bằng AI.

    KHI NÀO SỬ DỤNG CÔNG CỤ NÀY:
    - Khi người dùng yêu cầu đánh giá về sự thay đổi hoặc tác động của thiên tai tại một địa điểm cụ thể dựa trên ảnh vệ tinh RGB.
    - Khi cần phân tích trực quan thay đổi hoặc tác động thiên tai bằng ảnh vệ tinh rgb.

    LƯU Ý QUAN TRỌNG CHO AGENT (ĐIỀU KIỆN TIÊN QUYẾT):
    - KHÔNG gọi hàm này đầu tiên.
    - Hàm này PHẢI được gọi SAU KHI đã sử dụng công cụ tìm kiếm ảnh: search_two_satellite_rgb_image_and_get_tilejson để lấy được các URL bản đồ hợp lệ.

    Args:
        before_tilejson_url (str): Liên kết TileJSON của ảnh trước sự kiện (quá khứ) có thể có hoặc không.
        target_tilejson_url (str): Liên kết TileJSON của ảnh sau sự kiện (hiện tại/mục tiêu).
        lat (float): Vĩ độ tâm khu vực cần phân tích.
        lon (float): Kinh độ tâm khu vực cần phân tích.
        before_date (Optional[str]): Ngày của ảnh trước sự kiện.
        target_date (str): Ngày của ảnh sau sự kiện.
    Returns:
        str: Chuỗi JSON.
        - Thành công: {"status": "success", "analysis": "Báo cáo phân tích chi tiết từ AI đánh giá..."}
        - Lỗi: {"status": "error", "message": "lý do"}
    """

    before_tile_url, target_tile_url = await asyncio.gather(
        _fetch_tile_image(before_tilejson_url, lat, lon),
        _fetch_tile_image(target_tilejson_url, lat, lon)
    )

    before_image_b64 = await _tile_url_to_base64(before_tile_url) if before_tile_url else None
    target_image_b64 = await _tile_url_to_base64(target_tile_url) if target_tile_url else None

    if not before_image_b64 or not target_image_b64:
        logger.error("Không thể tải ảnh để phân tích.")
        return json.dumps({"status": "error", "message": "Không thể tải ảnh để phân tích."})

    prompt = (
        f"Bạn là một chuyên gia phân tích viễn thám và đánh giá rủi ro thiên tai. "
        f"Dưới đây là hai bức ảnh vệ tinh chụp tại tọa độ ({lat}, {lon}). "
        f"Bức ảnh thứ nhất là TRƯỚC SỰ KIỆN. Bức ảnh thứ hai là SAU SỰ KIỆN.\n\n"
        f"Nhiệm vụ của bạn là đối chiếu hai bức ảnh để phát hiện, phân tích và đánh giá mức độ ảnh hưởng của thiên tai (như bão, ngập lụt, sạt lở, cháy rừng, v.v.). "
        f"Hãy trình bày báo cáo chi tiết theo các khía cạnh sau:\n"
        f"- **Dấu hiệu thiên tai**: Chỉ ra hiện tượng bất thường (VD: nước bùn đục, vệt đất lở, vùng tro đen,...\n"
        f"- **Thủy văn & Ngập lụt**: Sự thay đổi về diện tích mặt nước, sông ngòi, hoặc mức độ ngập lụt tại khu vực dân cư/nông nghiệp.\n"
        f"- **Thảm thực vật & Địa hình**: Tình trạng mất mát mảng xanh, cây cối bị tàn phá hoặc các vết sạt lở đồi núi.\n"
        f"- **Cơ sở hạ tầng & Đất đai**: Đánh giá sự biến đổi của cấu trúc công trình, đường sá, và mục đích sử dụng đất.\n\n"
        f" **LƯU Ý QUAN TRỌNG TRONG ĐÁNH GIÁ**:\n"
        f"1. Khách quan: Chỉ mô tả những gì bạn THỰC SỰ nhìn thấy trên ảnh. Tuyệt đối KHÔNG suy diễn hoặc bịa đặt thiệt hại.\n"
        f"2. Cản trở tầm nhìn: Nếu một trong hai ảnh bị mây che phủ, bóng râm quá tối, hoặc chất lượng ảnh kém làm cản trở việc quan sát bề mặt, hãy DỪNG phân tích và phản hồi chính xác: 'Ảnh bị mây che khuất hoặc không đủ chất lượng để đánh giá sự thay đổi'."
    )

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{before_image_b64}",
                        "detail": "high"
                    }
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{target_image_b64}",
                        "detail": "high"
                    }
                }
            ]
        }
    ]

    response =await gemini_client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=messages,
        max_tokens=1000,
        temperature=0.7
    )

    result = response.choices[0].message.content
    return json.dumps({"status": "success",
                       "analysis_type": "so sánh ảnh vệ tinh RGB trước/sau",
                       "analysis": result,
                       "visualizations": [
                            {
                                "label": f"Ảnh trước sự kiện ngày {before_date}",
                                "image_url": before_tile_url
                            },
                            {
                                "label": f"Ảnh sau sự kiện ngày {target_date}",
                                "image_url": target_tile_url
                            }
                        ]
                    }, ensure_ascii=False)
# async def fetch_satellite_image(bbox: List[float], target_date: str) -> tuple[Optional[bytes], Dict[str, Any]]:
#     """
#     Công cụ lấy ảnh vệ tinh từ hệ thống dựa trên tọa độ bbox và ngày xác định.
#     Args:
#         bbox (List[float]): Danh sách chứa 4 giá trị [min_lon, min_lat, max_lon, max_lat]
#         target_date (str): Ngày cần lấy ảnh (định dạng YYYY-MM-DD)
#     Returns:
#         Tuple[Optional[bytes], Dict[str, Any]]: imgae_bytes, metadata
#     """

#     try:
#         catalog = Client.open(
#             "https://planetarycomputer.microsoft.com/api/stac/v1",
#             modifier=planetary_computer.sign_inplace
#         )

#         search = catalog.search(
#             collections=["sentinel-2-l2a"],
#             bbox=bbox,
#             datetime=f"{target_date}T00:00:00Z/{target_date}T23:59:59Z",
#             query={"eo:cloud_cover": {"lt": 80}},
#             limit=20,
#         )

#         items = list(search.get_items())
#         if not items:
#             logger.warning(f"Không tìm thấy ảnh Sentinel-2 nào thỏa mãn điều kiện vào ngày {target_date}")
#             return None, {"error": f"Không tìm thấy ảnh cho ngày {target_date}"}

#         best_item = sorted(items, key=lambda x: x.properties.get("eo:cloud_cover", 100))[0]

#         async with httpx.AsyncClient(timeout=60.0) as client:
#             bbox_str = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"

#             tile_url = (
#                 f"https://planetarycomputer.microsoft.com/api/data/v1/item/bbox/{bbox_str}.png"
#                 f"?collection=sentinel-2-l2a&item={best_item.id}"
#                 f"&assets=visual&width=512&height=512"
#             )

#             signed_url = planetary_computer.sign_url(tile_url)
#             logger.info(f"Đang tải ảnh cắt theo Bbox từ URL: {signed_url}")

#             img_resp = await client.get(signed_url)
#             if img_resp.status_code == 200:
#                 image_bytes = img_resp.content
#                 metadata = {
#                     "source": "Sentinel-2 L2A",
#                     "date": target_date,
#                     "item_id": best_item.id,
#                     "cloud_cover": best_item.properties.get("eo:cloud_cover"),
#                     "bbox_used": bbox
#                 }
#                 logger.info(f"Đã cắt và tải ảnh thành công!")
#                 return image_bytes, metadata
#             else:
#                 logger.error(f"Lỗi khi tải ảnh: {img_resp.status_code} - {img_resp.text}")
#                 return None, {"error": f"Failed to download tile: {img_resp.status_code}"}
#     except Exception as e:
#         logger.error(f"Lỗi khi tìm kiếm ảnh vệ tinh: {str(e)}")
#         return None, {"error": f"Lỗi khi tìm kiếm ảnh vệ tinh: {str(e)}"}
