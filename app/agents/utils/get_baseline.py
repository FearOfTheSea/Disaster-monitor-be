def get_historical_baseline(start_date: str, min_year: int, requested_years_back: int = 10, source_name: str = "MODIS") -> dict:
    """
    Tính toán khoảng thời gian baseline an toàn dựa trên năm hoạt động của vệ tinh.
    """
    target_year = int(start_date.split('-')[0])
    max_possible_back = target_year - min_year

    if max_possible_back >= requested_years_back:
        actual_years_back = requested_years_back
    elif max_possible_back >= 1:
        actual_years_back = max_possible_back
    else:
        return {
            "is_valid": False,
            "error_msg": (
                f"Sự kiện xảy ra năm {target_year}. Không có đủ dữ liệu lịch sử {source_name} (bắt đầu từ năm {min_year}) để xây dựng đường cơ sở (baseline). "
            )
        }

    return {
        "is_valid": True,
        "hist_year_start": target_year - actual_years_back,
        "hist_year_end": target_year - 1
    }
