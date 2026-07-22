from datetime import datetime

from app.core.opening_hours import verify_open_for_visit


SATURDAY_NOON = datetime.fromisoformat("2026-07-25T12:00:00+08:00")


def test_open_for_at_least_one_hour_is_verified() -> None:
    assert verify_open_for_visit("周一至周日 10:00-22:00", SATURDAY_NOON) is True


def test_closed_at_arrival_is_rejected() -> None:
    assert verify_open_for_visit("周一至周日 17:00-23:00", SATURDAY_NOON) is False


def test_closing_too_soon_is_rejected() -> None:
    assert verify_open_for_visit("周一至周日 09:00-12:30", SATURDAY_NOON) is False


def test_cross_midnight_hours_are_supported() -> None:
    late = datetime.fromisoformat("2026-07-25T23:30:00+08:00")
    assert verify_open_for_visit("18:00-次日02:00", late) is True


def test_unparseable_hours_are_not_claimed_as_verified() -> None:
    assert verify_open_for_visit("营业时间请电话咨询", SATURDAY_NOON) is None
