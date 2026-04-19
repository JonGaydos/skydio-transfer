from skydio_transfer import parse_captured_time


def test_parses_zulu_timestamp():
    d, t = parse_captured_time("2024-03-15T14:32:18Z")
    assert d == "2024-03-15"
    assert t == "14:32"


def test_parses_timestamp_without_z():
    d, t = parse_captured_time("2024-03-15T14:32:18")
    assert d == "2024-03-15"
    assert t == "14:32"


def test_parses_timestamp_with_fractional_seconds():
    d, t = parse_captured_time("2024-03-15T14:32:18.123456Z")
    assert d == "2024-03-15"
    assert t == "14:32"


def test_date_only_returns_date_and_em_dash():
    d, t = parse_captured_time("2024-03-15")
    assert d == "2024-03-15"
    assert t == "—"


def test_empty_returns_unknown():
    assert parse_captured_time("") == ("unknown", "—")


def test_none_returns_unknown():
    assert parse_captured_time(None) == ("unknown", "—")


def test_garbage_returns_unknown():
    assert parse_captured_time("not a date") == ("unknown", "—")
