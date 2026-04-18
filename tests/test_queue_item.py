from skydio_transfer import build_queue_item, api_from_item


MEDIA = {
    "uuid": "uuid-1",
    "filename": "clip.mp4",
    "date": "2026-04-17",
    "size": 1024,
    "download_url": "https://example.test/u/uuid-1",
}


def test_build_queue_item_snapshots_credentials():
    item = build_queue_item(
        media=MEDIA,
        output_folder=r"C:\Out",
        use_subfolders=True,
        api_token="TOKEN_AT_QUEUE_TIME",
        token_id="TID_AT_QUEUE_TIME",
        q_id=7,
    )
    assert item["api_token"] == "TOKEN_AT_QUEUE_TIME"
    assert item["token_id"] == "TID_AT_QUEUE_TIME"


def test_build_queue_item_carries_media_and_settings():
    item = build_queue_item(
        media=MEDIA,
        output_folder=r"C:\Out",
        use_subfolders=False,
        api_token="t",
        token_id="",
        q_id=7,
    )
    assert item["q_id"] == "7"
    assert item["uuid"] == "uuid-1"
    assert item["filename"] == "clip.mp4"
    assert item["date"] == "2026-04-17"
    assert item["size"] == 1024
    assert item["download_url"] == "https://example.test/u/uuid-1"
    assert item["output_folder"] == r"C:\Out"
    assert item["use_date_subfolders"] is False
    assert item["status"] == "Queued"


def test_api_from_item_uses_item_credentials_not_globals():
    item = {
        "api_token": "FROM_ITEM",
        "token_id": "FROM_ITEM_ID",
    }
    api = api_from_item(item)
    assert api.api_token == "FROM_ITEM"
    assert api.token_id == "FROM_ITEM_ID"


def test_api_from_item_tolerates_missing_token_id():
    item = {"api_token": "just-a-token"}
    api = api_from_item(item)
    assert api.api_token == "just-a-token"
    assert api.token_id == ""
