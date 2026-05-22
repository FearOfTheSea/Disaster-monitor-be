from typing import List
from pydantic import BaseModel

class SatelliteSearchRequest(BaseModel):
    satellite_type: str
    date: str
    bbox: list[float]  # [min_lon, min_lat, max_lon, max_lat]

class SatelliteSearchResponse(BaseModel):
    search_id: str
    tilejson_url: str
    xyz_url: str
    bounds: List[float]
    items_count: int
