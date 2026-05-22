from fastapi import logger
import httpx
import os
from agents import function_tool

@function_tool(timeout=15.0)
async def get_bbox_from_input(string_input: str) -> str | None:
    """
    Hàm chuyển đổi tên địa điểm thành tọa độ bbox.

    Args:
        string_input (str): Chuỗi đầu vào chứa tên địa điểm.

    Returns:
        str | None: Trả về chuỗi bbox đã chuẩn hóa nếu tìm thấy, ngược lại trả về None.
    """

    base_url = "https://us1.locationiq.com/v1/search"
    iq_api_key = os.getenv("IQ_LOCATION_API_KEY")
    params = {
        "key": iq_api_key,
        "q": string_input,
        "format": "json",
        "limit": 1
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(base_url, params=params)
        response.raise_for_status()
        data = response.json()

        if not data:
            logger.warning(f"Không tìm thấy kết quả cho '{string_input}'")
            return {"error": f"Không tìm thấy tọa độ cho địa điểm: {string_input}"}

        bbox_list = data[0].get("boundingbox")

        if bbox_list:
            min_lat, max_lat, min_lon, max_lon = bbox_list
            formatted_bbox = f"{min_lon},{min_lat},{max_lon},{max_lat}"
            return formatted_bbox
        else:
            logger.warning(f"Không tìm thấy bounding box cho '{string_input}'")
            return {"error": f"Không tìm thấy tọa độ cho địa điểm: {string_input}"}
    except httpx.TimeoutException:
        logger.warning(f"Timeout khi gọi {base_url}")
        return {"error": "Server đang quá tải. Vui lòng thử lại sau."}

    except httpx.HTTPStatusError as exc:
        logger.error(f"Lỗi phản hồi HTTP {exc.response.status_code}")
        return {"error": f"Dịch vụ báo lỗi: {exc.response.status_code}"}

    except Exception as exc:
        logger.critical(f"Lỗi hệ thống không xác định: {str(exc)}")
        return {"error": "Đã xảy ra lỗi hệ thống nghiêm trọng."}
