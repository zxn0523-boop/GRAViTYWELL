import re
from datetime import datetime, time, timedelta


WEEKDAY_NAMES = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
TIME_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*[-~至到]\s*(次日)?\s*(\d{1,2}):(\d{2})")
DAY_RANGE = re.compile(r"周([一二三四五六日天])\s*[-~至到]\s*周?([一二三四五六日天])")


def verify_open_for_visit(
    opening_hours: str | None,
    arrival: datetime | None,
    stay_minutes: int = 60,
) -> bool | None:
    """Return True/False only when supplied hours can be interpreted reliably."""

    if not opening_hours or arrival is None:
        return None
    text = opening_hours.strip()
    if "24小时" in text or "全天" in text:
        return True
    if any(marker in text for marker in ("歇业", "暂停营业", "已关闭")):
        return False

    departure = arrival + timedelta(minutes=stay_minutes)
    applicable_intervals: list[tuple[datetime, datetime]] = []
    for segment in re.split(r"[;；]", text):
        if not _day_applies(segment, arrival.weekday()):
            continue
        for match in TIME_RANGE.finditer(segment):
            start_hour, start_minute = int(match.group(1)), int(match.group(2))
            end_hour, end_minute = int(match.group(4)), int(match.group(5))
            if start_hour > 23 or end_hour > 24 or start_minute > 59 or end_minute > 59:
                continue
            start = datetime.combine(arrival.date(), time(start_hour, start_minute), arrival.tzinfo)
            normalized_end_hour = 0 if end_hour == 24 else end_hour
            end = datetime.combine(arrival.date(), time(normalized_end_hour, end_minute), arrival.tzinfo)
            if match.group(3) or end <= start or end_hour == 24:
                end += timedelta(days=1)
            applicable_intervals.append((start, end))

    if not applicable_intervals:
        return None
    return any(start <= arrival and departure <= end for start, end in applicable_intervals)


def _day_applies(segment: str, weekday: int) -> bool:
    range_match = DAY_RANGE.search(segment)
    if range_match:
        start = WEEKDAY_NAMES[range_match.group(1)]
        end = WEEKDAY_NAMES[range_match.group(2)]
        allowed = set(range(start, end + 1)) if start <= end else set(range(start, 7)) | set(range(0, end + 1))
        return weekday in allowed
    single_days = re.findall(r"周([一二三四五六日天])", segment)
    if single_days:
        return weekday in {WEEKDAY_NAMES[name] for name in single_days}
    return True
