from skydio_transfer import extract_legacy_credentials


def test_extracts_both_credentials_from_legacy_config():
    cfg = {"api_token": "T", "token_id": "I", "output_folder": "/out"}
    token, token_id, sanitized = extract_legacy_credentials(cfg)
    assert token == "T"
    assert token_id == "I"
    assert sanitized == {"output_folder": "/out"}


def test_returns_empty_strings_on_already_migrated_config():
    cfg = {"output_folder": "/out"}
    token, token_id, sanitized = extract_legacy_credentials(cfg)
    assert token == ""
    assert token_id == ""
    assert sanitized == {"output_folder": "/out"}


def test_handles_empty_config():
    token, token_id, sanitized = extract_legacy_credentials({})
    assert token == ""
    assert token_id == ""
    assert sanitized == {}


def test_does_not_mutate_input():
    cfg = {"api_token": "T", "token_id": "I", "output_folder": "/out"}
    extract_legacy_credentials(cfg)
    assert cfg == {"api_token": "T", "token_id": "I", "output_folder": "/out"}


def test_treats_explicit_none_as_missing():
    cfg = {"api_token": None, "token_id": None, "output_folder": "/out"}
    token, token_id, sanitized = extract_legacy_credentials(cfg)
    assert token == ""
    assert token_id == ""
    assert sanitized == {"output_folder": "/out"}


def test_signals_migration_needed_only_when_secrets_present():
    from skydio_transfer import has_legacy_credentials
    assert has_legacy_credentials({"api_token": "T"}) is True
    assert has_legacy_credentials({"token_id": "I"}) is True
    assert has_legacy_credentials({"api_token": "", "token_id": ""}) is False
    assert has_legacy_credentials({"output_folder": "/out"}) is False
    assert has_legacy_credentials({}) is False
