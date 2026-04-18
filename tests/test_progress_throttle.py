from skydio_transfer import ProgressThrottle


def test_emits_on_first_tick():
    t = ProgressThrottle(min_interval=0.25)
    assert t.tick(now=100.0) is True


def test_suppresses_within_interval():
    t = ProgressThrottle(min_interval=0.25)
    t.tick(now=100.0)
    assert t.tick(now=100.1) is False


def test_emits_after_interval_elapsed():
    t = ProgressThrottle(min_interval=0.25)
    t.tick(now=100.0)
    assert t.tick(now=100.30) is True


def test_repeated_suppressed_ticks_do_not_reset_last_emit():
    t = ProgressThrottle(min_interval=0.25)
    t.tick(now=100.0)
    t.tick(now=100.1)
    t.tick(now=100.2)
    assert t.tick(now=100.26) is True
