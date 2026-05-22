import ee
def compute_area_statistics(class_img: ee.Image, labels: dict, geometry: ee.Geometry, scale: int, tileScale: int) -> dict:
    """
    Tính diện tích và phần trăm theo từng lớp.

    Args:
        class_img: Ảnh đã classify
        labels: Dict label config
        geometry: AOI
        scale: Pixel size
    Returns:
        Dict statistics
    """

    area_img = ee.Image.pixelArea()
    area_stats = area_img.addBands(class_img).reduceRegion(
            reducer=ee.Reducer.sum().group(
                groupField=1,
                groupName='class'
            ),
            geometry=geometry,
            scale= scale,
            tileScale=tileScale,
            maxPixels=1e13
        ).getInfo()


    area_dict = {}

    if ("groups" in area_stats and area_stats["groups"] is not None):
        for group in area_stats["groups"]:
            class_id = int(group["class"])
            area_dict[class_id] = group["sum"]

    total_area_sqm = sum(area_dict.values())

    final_statistics = {}

    for class_id, conf in labels.items():
        area_sqm = area_dict.get(class_id, 0)
        area_ha = area_sqm / 10000
        percentage = ((area_sqm / total_area_sqm) * 100 if total_area_sqm > 0 else 0)

        final_statistics[class_id] = {
            "label": conf["label"],
            "range": conf["range"],
            "area_ha": round(area_ha, 2),
            "percentage": round(percentage, 2)
        }

    return final_statistics


def build_visualization(class_img: ee.Image, palette: list, geometry: ee.Geometry, dimensions: int = 800) -> dict:
    """
    Tạo tile URL + thumbnail URL cho ảnh classify.
    Args:
        class_img: Ảnh đã classify
        palette: Color palette
        geometry: AOI
        dimensions: Kích thước thumbnail

    Returns:
        Dict chứa:
            - tile_url
            - image_url
    """

    vis_params = {
        "min": 1,
        "max": len(palette),
        "palette": [
            color.replace("#", "") for color in palette
        ]
    }

    map_id_dict = class_img.getMapId(vis_params)

    tile_url = map_id_dict["tile_fetcher"].url_format

    thumb_url = class_img.getThumbURL({
        **vis_params,
        "region": geometry,
        "dimensions": dimensions,
        "format": "png"
    })

    return {
        "tile_url": tile_url,
        "image_url": thumb_url
    }
