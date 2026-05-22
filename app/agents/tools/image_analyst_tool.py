from agents import Agent, function_tool, Runner
from app.agents.tools.generate_image_tool import get_satellite_image_base64
from app.core.llm_clients import get_gemini_client, get_gemini_model
from app.services.satellite.stac_search_service import search_target_image
import json
import logging
import httpx


instruction = """Bạn là một chuyên gia phân tích không gian địa lý chuyên phân tích ảnh vệ tinh RGB.
Bạn sẽ nhận được đầu vào gồm ảnh ở dạng base64 và câu hỏi hoặc yêu cầu của người dùng về khu vực này.
Nhiệm vụ của bạn:
    Phân tích ảnh để giải quyết câu hỏi hoặc yêu cầu từ người dùng. Nếu ảnh quá lớn hoặc không thấy ảnh hoặc quá mờ, hãy phản hồi lại chi tiết nguyên nhân.
Quy tắc phân tích:
    1. Giải quyết trực tiếp câu hỏi/yêu cầu của người dùng dựa trên nội dung bức ảnh.
    2. Mô tả lớp phủ bề mặt tổng thể (khu vực đô thị, thảm thực vật, mặt nước, đất trống, v.v.).
    3. Xác định các cấu trúc có thể quan sát được (đường sá, công trình xây dựng, sông ngòi, đồng ruộng, khu công nghiệp, v.v.).
    4. Phát hiện các sự kiện thiên tai hoặc môi trường nếu có thể quan sát thấy rõ ràng (ví dụ: ngập lụt, hỏa hoạn, tàn phá rừng, khói, v.v.).
    5. Phân tích của bạn phải dựa hoàn toàn vào các bằng chứng trực quan quan sát được trong ảnh.
    6. KHÔNG suy đoán vượt ra ngoài những gì có thể nhìn thấy bằng mắt thường.
    7. Nếu hình ảnh bị lỗi, quá mờ, bị mây che phủ hoàn toàn hoặc không thể phân tích, hãy trả về phản hồi tại sao bạn không thể phân tích hình ảnh.
    * Lưu ý: Mọi kết quả phân tích PHẢI dựa trên bằng chứng trực quan từ hình ảnh được cung cấp. Nếu hình ảnh không có đủ dữ liệu hoặc không hiển thị rõ, hãy báo cáo lại.
Trả về kết quả theo định dạng cấu trúc sau:
    Trả lời người dùng: [Đưa ra câu trả lời trực tiếp cho yêu cầu/câu hỏi đầu vào]
    Mô tả chung: [Mô tả chi tiết lớp phủ bề mặt và bối cảnh bức ảnh]
    Các đối tượng phát hiện được: [Liệt kê các cấu trúc hoặc dấu hiệu thiên tai/ngập lụt nhìn thấy được]"""

logger = logging.getLogger(__name__)


gemini_client = get_gemini_client()
gemini_model = get_gemini_model("gemini-2.5-flash")

image_analyst_agent = Agent(name="Satellite Image Analyzer", model=gemini_model,
                                                             instructions=instruction)

@function_tool(timeout=180.0)
async def analyze_image(bbox: str, target_date: str, question: str) -> str:
    """
    Công cụ này dùng để phân tích ảnh vệ tinh của một khu vực.

    Args:
        bbox (str): Tọa độ khu vực cần phân tích (min_lon,min_lat,max_lon,max_lat)
        target_date (str): Ngày cần lấy ảnh (YYYY-MM-DD)
        question (str): Câu hỏi hoặc yêu cầu chi tiết của người dùng về khu vực này
    Returns:
        str: Kết quả phân tích ảnh vệ tinh dựa trên câu hỏi của người dùng và url của hình ảnh
    """

    try:
        bbox_coords = [float(x) for x in bbox.split(",")]
        items = search_target_image(bbox=bbox_coords, target_date=target_date)
        if not items:
            logger.warning(f"Không tìm thấy ảnh nào cho bbox {bbox} vào ngày {target_date}.")
            return json.dumps({"status": "error",
                               "message": f"Không tìm thấy ảnh vệ tinh nào cho khu vực này vào ngày {target_date}. Hãy báo cáo lại rõ ràng cho người dùng."}, ensure_ascii=False)

        best_item = min(items, key=lambda item: item.get("properties", {}).get("eo:cloud_cover", 100))
        item_id = best_item["id"]
        minx, miny, maxx, maxy = bbox_coords
        image_url = (
            f"https://planetarycomputer.microsoft.com/api/data/v1/item/bbox/"
            f"{minx},{miny},{maxx},{maxy}/512x512.png"
            f"?collection=sentinel-2-l2a"
            f"&item={item_id}"
            f"&assets=visual"
        )

        import base64

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(image_url)
                response.raise_for_status()
                image_data = response.content
                image_base64 = base64.b64encode(image_data).decode("utf-8")
            except httpx.HTTPStatusError as e:
                logger.error(f"Lỗi HTTP khi tải ảnh vệ tinh: {e.response.status_code} - {e.response.text}")
                return json.dumps({"status": "error",
                                "message": f"Lỗi khi tải ảnh vệ tinh: Mã lỗi {e.response.status_code}."}, ensure_ascii=False)
            except httpx.RequestError as e:
                logger.error(f"Lỗi mạng khi tải ảnh vệ tinh: {str(e)}")
                return json.dumps({"status": "error",
                                "message": "Kết nối mạng bị gián đoạn khi tải ảnh vệ tinh. Vui lòng thử lại sau."}, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Lỗi không xác định khi tải ảnh vệ tinh: {str(e)}")
                return json.dumps({"status": "error",
                                "message": "Đã xảy ra lỗi không xác định khi tải ảnh vệ tinh."}, ensure_ascii=False)
        

        messages = [
            {"role": "system", "content": instruction},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_base64}"}
                    }
                ]
            }
        ]

        response = await gemini_client.chat.completions.create(
                    model="gemini-2.5-flash",
                    messages=messages,
                )
        result = {
            "status": "success",
            "analysis_type": "satellite_image_analysis",
            "analysis": response.choices[0].message.content,
            "image_url": image_url,
        }
        return json.dumps(result, ensure_ascii=False)

    except ValueError:
        logger.exception("Analyze image failed")

        return json.dumps({
            "status": "error",
            "message": str(e)
        }, ensure_ascii=False)
