import asyncio
import json
from typing import Any, Dict, List, Optional
from agents import function_tool
import httpx
import logging
from app.core.llm_clients import get_gemini_client
from app.services.satellite.stac_search_service import search_target_image

logger = logging.getLogger(__name__)


async def _tile_url_to_base64(tile_url: str) -> Optional[str]:
    """Chuyển đổi URL của tile ảnh thành chuỗi Base64.
    Args:  tile_url (str): URL của tile ảnh cần chuyển đổi.
    Returns: Optional[str]: Chuỗi Base64 của ảnh nếu thành công, hoặc None nếu có lỗi.
    """
    import base64
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:

            response = await client.get(tile_url)

            response.raise_for_status()

            image_b64 = base64.b64encode(response.content).decode("utf-8")

            return image_b64
    except httpx.HTTPStatusError as e:
        logger.error(f"Lỗi HTTP khi tải tile ảnh: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logger.error(f"Lỗi convert tile sang base64: {e}")
        return None

@function_tool(timeout=200.0)
async def analyze_image_comparison(bbox: str, before_date: str, target_date: str) -> str:
    """
    Phân tích sự thay đổi giữa hai ảnh vệ tinh (Trước và Sau) bằng AI.

    KHI NÀO SỬ DỤNG CÔNG CỤ NÀY:
    - Khi người dùng yêu cầu đánh giá về sự thay đổi hoặc tác động của thiên tai tại một địa điểm cụ thể dựa trên ảnh vệ tinh RGB.
    - Khi cần phân tích trực quan thay đổi hoặc tác động thiên tai bằng ảnh vệ tinh rgb.
    Args:
        bbox (str): Tọa độ khu vực cần phân tích dạng chuỗi "min_lon,min_lat,max_lon,max_lat".
        before_date (Optional[str]): Ngày của ảnh trước sự kiện.
        target_date (str): Ngày của ảnh sau sự kiện.
    Returns:
        str: Chuỗi JSON.
        - Thành công: {"status": "success", "analysis": "Báo cáo phân tích chi tiết từ AI đánh giá..."}
        - Lỗi: {"status": "error", "message": "lý do"}
    """
    try:
        bbox_coords = [float(x) for x in bbox.split(",")]
        if len(bbox_coords) != 4:
            logger.error("Định dạng bbox không hợp lệ.")
            return json.dumps({"status": "error", "message": "Định dạng bbox không hợp lệ."})
        try:
            target_items = search_target_image(bbox, target_date)
        except Exception as e:
            logger.error(f"Lỗi khi kết nối API tìm kiếm ảnh vệ tinh (target_date): {str(e)}")
            return json.dumps({
                "status": "error",
                "message": f"Có lỗi xảy ra khi tìm kiếm ảnh vệ tinh (target): {str(e)}."
            })

        try:
            before_items = search_target_image(bbox, before_date)
        except Exception as e:
            logger.error(f"Lỗi khi kết nối API tìm kiếm ảnh vệ tinh (before_date): {str(e)}")
            return json.dumps({
                "status": "error",
                "message": f"Có lỗi xảy ra khi tìm kiếm ảnh vệ tinh (before): {str(e)}."
            })
        if not target_items:
            logger.warning(f"Không tìm thấy ảnh vệ tinh cho ngày {target_date}.")
            return json.dumps({"status": "error", "message": f"Không tìm thấy ảnh vệ tinh cho ngày {target_date}."})

        if not before_items:
            logger.warning(f"Không tìm thấy ảnh vệ tinh cho ngày trước đó {before_date}.")
            return json.dumps({"status": "error", "message": f"Không tìm thấy ảnh vệ tinh cho ngày trước đó {before_date}."})

        best_target_item = min(target_items, key=lambda item: item.get("properties", {}).get("eo:cloud_cover", 100))
        best_before_item = min(before_items, key=lambda item: item.get("properties", {}).get("eo:cloud_cover", 100))

        item_before_id = best_before_item["id"]
        item_target_id = best_target_item["id"]

        minx, miny, maxx, maxy = bbox_coords

        before_image_url = (
            f"https://planetarycomputer.microsoft.com/api/data/v1/item/bbox/"
            f"{minx},{miny},{maxx},{maxy}/512x512.png"
            f"?collection=sentinel-2-l2a"
            f"&item={item_before_id}"
            f"&assets=visual"
        )

        target_image_url = (
            f"https://planetarycomputer.microsoft.com/api/data/v1/item/bbox/"
            f"{minx},{miny},{maxx},{maxy}/512x512.png"
            f"?collection=sentinel-2-l2a"
            f"&item={item_target_id}"
            f"&assets=visual"
        )

        before_image_b64 = await _tile_url_to_base64(before_image_url)
        target_image_b64 = await _tile_url_to_base64(target_image_url)

        if not before_image_b64 or not target_image_b64:
            logger.error("Không thể tải ảnh để phân tích.")
            return json.dumps({"status": "error", "message": "Không thể tải ảnh để phân tích."})

        prompt = (
            f"Bạn là một chuyên gia phân tích viễn thám và đánh giá rủi ro thiên tai. "
            f"Dưới đây là hai bức ảnh vệ tinh chụp tại khu vực {bbox}. "
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

        gemini_client = get_gemini_client()

        try:
                response = await gemini_client.chat.completions.create(
                    model="gemini-3.5-flash",
                    messages=messages,
                    temperature=0.3
                )
                result = response.choices[0].message.content
                
                return json.dumps({
                    "status": "success",
                    "analysis_type": "so sánh ảnh vệ tinh RGB trước/sau",
                    "analysis": result,
                    "visualizations": [
                        {
                            "label": f"Ảnh trước sự kiện ngày {before_date}",
                            "image_url": before_image_url
                        },
                        {
                            "label": f"Ảnh sau sự kiện ngày {target_date}",
                            "image_url": target_image_url
                        }
                    ]
                }, ensure_ascii=False)

        except Exception as e:
            logger.exception("Lỗi Crash khi gọi Gemini API hoặc xử lý kết quả:") 

            return json.dumps({
                "status": "error", 
                "message": f"Lỗi khi gọi VLM để phân tích ảnh: {str(e)}"
            })
    except Exception as e:
        logger.exception("Lỗi trong quá trình phân tích ảnh:")
        return json.dumps({
            "status": "error",
            "message": f"Đã xảy ra lỗi không xác định trong quá trình phân tích ảnh: {str(e)}"
        }, ensure_ascii=False)
