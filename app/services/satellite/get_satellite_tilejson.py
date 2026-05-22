import httpx
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


async def get_satellite_tilejson_url(
    items: List[Dict[str, Any]],
    bbox: List[float],
    target_date: str
) -> Dict[str, Any]:
    """
    Tạo TileJSON URL từ danh sách STAC Item.
    Nếu có nhiều Item sẽ đăng ký Mosaic.
    """

    if not items:
        logger.warning("Không có Item nào để lấy ảnh vệ tinh.")

        return {
            "status": "error",
            "message": "Không có ảnh vệ tinh phù hợp."
        }

    assets_param = "assets=visual"
    collection = "sentinel-2-l2a"

    async with httpx.AsyncClient(timeout=15.0) as client:

        if len(items) == 1:

            item_id = items[0].get("id")

            if not item_id:
                return {
                    "status": "error",
                    "message": "Item không có id."
                }

            logger.info(
                f"Chỉ có 1 ảnh. Dùng Item API trực tiếp: {item_id}"
            )

            final_tile_url = (
                f"https://planetarycomputer.microsoft.com/api/data/v1/item/tilejson.json"
                f"?collection={collection}&item={item_id}&{assets_param}"
            )

        else:

            item_ids = [
                item.get("id")
                for item in items
                if item.get("id")
            ]

            if not item_ids:
                return {
                    "status": "error",
                    "message": "Danh sách item id rỗng."
                }

            logger.info(
                f"Có {len(item_ids)} ảnh. Bắt đầu đăng ký Mosaic..."
            )

            register_url = (
                "https://planetarycomputer.microsoft.com/api/data/v1/mosaic/register"
            )

            search_payload = {
                "collections": [collection],
                "ids": item_ids
            }

            try:

                reg_resp = await client.post(
                    register_url,
                    json=search_payload
                )

                if reg_resp.status_code != 200:

                    logger.error(
                        f"Lỗi đăng ký Mosaic: "
                        f"{reg_resp.status_code} - {reg_resp.text}"
                    )

                    return {
                        "status": "error",
                        "message": (
                            f"Mosaic register failed: "
                            f"{reg_resp.status_code}"
                        )
                    }

                response_json = reg_resp.json()

                search_id = response_json.get("id")

                if not search_id:

                    logger.error("Không nhận được search_id từ Mosaic API.")


                    return {
                        "status": "error",
                        "message": "Không nhận được mosaic search_id."
                    }

                final_tile_url = (
                    f"https://planetarycomputer.microsoft.com/api/data/v1/mosaic/"
                    f"{search_id}/tilejson.json"
                    f"?collection={collection}&{assets_param}"
                )

            except Exception as e:

                logger.exception("Lỗi mạng khi xử lý Mosaic")

                return {
                    "status": "error",
                    "message": str(e)
                }

        if not final_tile_url:

            return {
                "status": "error",
                "message": "Không thể tạo TileJSON URL."
            }

    logger.info("Đã tạo thành công TileJSON URL.")

    return {
        "status": "success",
        "data": {
            "tilejson_url": final_tile_url,
            "metadata": {
                "source": collection,
                "date": target_date,
                "bbox_used": bbox,
            }
        }
    }
