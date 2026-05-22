from typing import List, Optional, Dict, Any
import logging
from app.services.satellite.planetary_client import get_planetary_client


logger = logging.getLogger(__name__)


def search_target_image(bbox: List[float], target_date: str) -> Optional[List[Dict[str, Any]]]:
    """Tìm kiếm ảnh vệ tinh Sentinel-2 cho ngày cần phân tích với đầu vào là bbox và ngày.
    Trả về danh sách các Item thỏa mãn điều kiện."""
    try:
        catalog = get_planetary_client()

        search = catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=bbox,
            datetime=f"{target_date}T00:00:00Z/{target_date}T23:59:59Z",
            query={"eo:cloud_cover": {"lt": 65}},
            limit=20,
        )

        items = [item.to_dict() for item in search.get_items()]

        if not items:
            logger.warning(f"Không tìm thấy ảnh Sentinel-2 nào thỏa mãn điều kiện vào ngày {target_date}")
            return None

        return items
    except Exception as e:
        logger.error(f"Lỗi khi tìm kiếm ảnh vệ tinh: {str(e)}")
        return None
