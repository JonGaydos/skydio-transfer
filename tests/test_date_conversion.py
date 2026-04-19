from datetime import timezone, timedelta

from skydio_transfer import local_date_to_utc_iso


EST = timezone(timedelta(hours=-5))
UTC = timezone.utc
JST = timezone(timedelta(hours=9))


def test_est_midnight_becomes_0500_utc():
    # A user in EST typing 2024-03-15 means local midnight,
    # which is 2024-03-15 05:00 UTC — the server should filter
    # from that instant, not from 2024-03-15 00:00 UTC (which
    # is actually 2024-03-14 19:00 EST).
    assert local_date_to_utc_iso("2024-03-15", tz=EST) == "2024-03-15T05:00:00Z"


def test_est_end_of_day_rolls_into_next_utc_day():
    # 23:59:59 EST = 04:59:59 UTC the next day.
    assert local_date_to_utc_iso("2024-03-15", end_of_day=True, tz=EST) == "2024-03-16T04:59:59Z"


def test_utc_user_sees_no_shift():
    assert local_date_to_utc_iso("2024-03-15", tz=UTC) == "2024-03-15T00:00:00Z"
    assert local_date_to_utc_iso("2024-03-15", end_of_day=True, tz=UTC) == "2024-03-15T23:59:59Z"


def test_jst_user_shifts_backward():
    # JST is UTC+9, so JST midnight = previous-day 15:00 UTC.
    assert local_date_to_utc_iso("2024-03-15", tz=JST) == "2024-03-14T15:00:00Z"


def test_invalid_date_raises_valueerror():
    import pytest
    with pytest.raises(ValueError):
        local_date_to_utc_iso("not-a-date", tz=UTC)
