from typing import List

from fastapi import logger
import httpx
import os
from agents import function_tool

@function_tool(timeout=15.0)
async def get_coordinates_from_input(string_input: str) -> List[float] | None:
    """
    Hàm chuyển đổi tên địa điểm thành tọa độ latitude, longitude.

    Args:
        string_input (str): Chuỗi đầu vào chứa tên địa điểm.

    Returns:
        List[float, float] | None: Trả về list chứa latitude và longitude nếu tìm thấy, ngược lại trả về None.
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
            return None

        lat_raw = data[0].get("lat")
        lon_raw = data[0].get("lon")

        if lat_raw is not None and lon_raw is not None:
            lat = round(float(lat_raw), 2)
            lon = round(float(lon_raw), 2)
            return [lat, lon]
        else:
            logger.warning(f"Không tìm thấy tọa độ cho '{string_input}'")
            return None
    except httpx.TimeoutException:
        logger.warning(f"Timeout khi gọi {base_url}")
        return None

    except httpx.HTTPStatusError as exc:
        logger.error(f"Lỗi phản hồi HTTP {exc.response.status_code}")
        return None

    except Exception as exc:
        logger.critical(f"Lấy tọa độ thấy bại: {str(exc)}")
        return None
