from skydio_transfer import sanitize_windows_filename


def test_preserves_safe_filename():
    assert sanitize_windows_filename("normal_file-2024.mp4") == "normal_file-2024.mp4"


def test_replaces_illegal_chars():
    assert sanitize_windows_filename('a<b>c.txt') == "a_b_c.txt"
    assert sanitize_windows_filename('a:b"c|d?e*.mp4') == "a_b_c_d_e_.mp4"


def test_replaces_path_separators():
    assert sanitize_windows_filename("a/b\\c.mp4") == "a_b_c.mp4"


def test_reserved_name_is_escaped():
    result = sanitize_windows_filename("CON.txt")
    assert result != "CON.txt"


def test_reserved_name_without_extension_is_escaped():
    result = sanitize_windows_filename("PRN")
    assert result != "PRN"


def test_reserved_name_case_insensitive():
    result = sanitize_windows_filename("con.txt")
    assert result.lower() != "con.txt"


def test_strips_trailing_dots_and_spaces():
    assert not sanitize_windows_filename("file...").endswith(".")
    assert not sanitize_windows_filename("file   ").endswith(" ")


def test_empty_or_all_illegal_returns_fallback():
    assert sanitize_windows_filename("") != ""
    assert sanitize_windows_filename("...") != ""
    assert sanitize_windows_filename("   ") != ""


def test_non_reserved_prefix_left_alone():
    # "CONFIG" starts with CON but isn't the CON reserved name
    assert sanitize_windows_filename("CONFIG.json") == "CONFIG.json"
