from skydio_transfer import retry_policy


def test_exhausted_attempts_do_not_retry():
    should_retry, delay = retry_policy(
        exception=None, status_code=500, retry_after=None,
        attempt=3, max_retries=3,
    )
    assert should_retry is False
    assert delay == 0.0


def test_network_exception_retries_with_exponential_backoff():
    should_retry, delay = retry_policy(
        exception=ConnectionError("boom"), status_code=None, retry_after=None,
        attempt=1, max_retries=3,
    )
    assert should_retry is True
    assert delay == 2.0  # 2 ** 1


def test_429_with_retry_after_honors_header():
    should_retry, delay = retry_policy(
        exception=None, status_code=429, retry_after="15",
        attempt=1, max_retries=3,
    )
    assert should_retry is True
    assert delay >= 15.0


def test_429_caps_delay_at_60_seconds():
    should_retry, delay = retry_policy(
        exception=None, status_code=429, retry_after="600",
        attempt=1, max_retries=3,
    )
    assert should_retry is True
    assert delay == 60.0


def test_429_without_retry_after_uses_backoff():
    should_retry, delay = retry_policy(
        exception=None, status_code=429, retry_after=None,
        attempt=2, max_retries=3,
    )
    assert should_retry is True
    assert delay == 4.0  # 2 ** 2


def test_5xx_retries_with_backoff():
    should_retry, delay = retry_policy(
        exception=None, status_code=503, retry_after=None,
        attempt=0, max_retries=3,
    )
    assert should_retry is True
    assert delay == 1.0  # 2 ** 0


def test_4xx_non_429_does_not_retry():
    should_retry, delay = retry_policy(
        exception=None, status_code=401, retry_after=None,
        attempt=0, max_retries=3,
    )
    assert should_retry is False


def test_success_does_not_retry():
    should_retry, _ = retry_policy(
        exception=None, status_code=200, retry_after=None,
        attempt=0, max_retries=3,
    )
    assert should_retry is False


def test_malformed_retry_after_falls_back_to_backoff():
    should_retry, delay = retry_policy(
        exception=None, status_code=429, retry_after="not-a-number",
        attempt=1, max_retries=3,
    )
    assert should_retry is True
    assert delay == 2.0  # falls back to 2 ** 1
