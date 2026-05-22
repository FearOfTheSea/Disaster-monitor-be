from agents import ToolOutputImage, function_tool
import httpx
import requests
import base64
import logging
from urllib.parse import urlencode
import asyncio

logger = logging.getLogger(__name__)

# @function_tool(timeout=120.0)
async def get_satellite_image_base64(bbox: str, target_date: str, style: str = "simple_rgb") -> dict | str:
    """
    Hàm tải ảnh vệ tinh từ hệ thống Datacube OWS và chuyển sang định dạng Base64.

    Args:
        bbox (str): Tọa độ khung cắt ảnh (min_lon, min_lat, max_lon, max_lat). VD: "107.54,16.41,107.64,16.51"
        target_date (str): Ngày cần lấy ảnh (YYYY-MM-DD). VD: "2025-10-01"
        style (str): Kiểu hiển thị ảnh (simple_rgb, ndvi, false_color...). Mặc định là "simple_rgb"

    Returns:
        ToolOutputImage | str:
        - Trả về đối tượng ToolOutputImage chứa ảnh vệ tinh nếu lấy dữ liệu thành công.
        - Trả về chuỗi văn bản (str) chứa thông báo lỗi chi tiết
    """
    base_url = "http://localhost:8000/wms"
    params = {
        "service": "WMS",
        "version": "1.3.0",
        "request": "GetMap",
        "layers": "sentinel_2_l2a",
        "styles": style,
        "crs": "EPSG:4326",
        "bbox": bbox,
        "width": 512,
        "height": 512,
        "format": "image/png",
        "time": target_date
    }

    try:
        url = f"{base_url}?{urlencode(params)}"
        print(f"Đang gọi WMS: {url}")

        async with httpx.AsyncClient(timeout=115.0) as client:
            response = await client.get(url)

        if response.status_code != 200:
            return f"Lỗi Server {response.status_code}: Không thể tải ảnh. Chi tiết: {response.text[:200]}"

        content_type = response.headers.get('content-type', '')
        if 'image' not in content_type:
            return f"Lỗi: Thông báo: Không tìm thấy dữ liệu ảnh vệ tinh cho khu vực và ngày đã chọn. Content-Type: {content_type}"

        image_bytes = response.content
        base64_string = base64.b64encode(image_bytes).decode('utf-8')

        data_uri =  f"![image](data:image/png;base64,{base64_string})"
        return {
            "image_base64": base64_string,
            "image_url": url
        }

    except httpx.TimeoutException:
        logger.error("Timeout khi gửi WMS")
        return "Lỗi: Thời gian xử lý quá lâu (Timeout). Vui lòng thử lại hoặc chọn khu vực khác."
    except httpx.HTTPError:
        logger.error("Lỗi kết nối tới WMS")
        return "Lỗi: Không thể kết nối tới server. Vui lòng kiểm tra kết nối mạng."
    except Exception as e:
        logger.error(f"Lỗi hệ thống không xác định: {str(e)}")
        return f"Lỗi hệ thống không xác định: {str(e)}"
