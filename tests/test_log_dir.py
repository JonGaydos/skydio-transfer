import sys
from pathlib import Path

from skydio_transfer import log_dir


def test_log_dir_is_next_to_frozen_exe(monkeypatch, tmp_path):
    exe = tmp_path / "SkydioTransfer.exe"
    exe.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert log_dir() == tmp_path


def test_log_dir_is_next_to_script_when_not_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    import skydio_transfer
    expected = Path(skydio_transfer.__file__).resolve().parent
    assert log_dir().resolve() == expected
