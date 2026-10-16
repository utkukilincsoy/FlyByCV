"""ui/restart: yeniden başlatma komutu (.exe ve `python main.py`) ve başarısızlık durumu.

Gerçek bir süreç BAŞLATILMAZ: her testte sahte bir başlatıcı verilir; `conftest.no_real_relaunch`
de unutulan bir varsayılana karşı `QProcess.startDetached`'ı engeller.
"""
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess

from src.ui import restart


class _StarterSpy:
    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[tuple[str, list[str], str]] = []

    def __call__(self, program: str, arguments: list[str], working_dir: str):
        self.calls.append((program, arguments, working_dir))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_frozen_exe_relaunches_itself_with_same_arguments(monkeypatch):
    monkeypatch.setattr(restart.paths, "is_frozen", lambda: True)
    monkeypatch.setattr(restart.sys, "executable", r"C:\Program Files\FlybyCV\FlybyCV.exe")
    monkeypatch.setattr(restart.sys, "argv", [r"C:\Program Files\FlybyCV\FlybyCV.exe"])

    assert restart.relaunch_command() == (r"C:\Program Files\FlybyCV\FlybyCV.exe", [])


def test_dev_mode_relaunches_python_with_absolute_script_path(monkeypatch, tmp_path):
    monkeypatch.setattr(restart.paths, "is_frozen", lambda: False)
    monkeypatch.setattr(restart.sys, "executable", r"C:\proje\.venv\Scripts\python.exe")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(restart.sys, "argv", ["main.py", "--bir-bayrak"])

    program, arguments = restart.relaunch_command()

    assert program == r"C:\proje\.venv\Scripts\python.exe"
    assert arguments == [str((tmp_path / "main.py").resolve()), "--bir-bayrak"]
    assert Path(arguments[0]).is_absolute()


@pytest.mark.parametrize("result", [(True, 1234), True])
def test_relaunch_reports_success(result):
    starter = _StarterSpy(result)

    assert restart.relaunch(starter) is True
    assert len(starter.calls) == 1


@pytest.mark.parametrize("result", [(False, -1), False, OSError("başlatılamadı")])
def test_relaunch_reports_failure_without_raising(result, caplog):
    with caplog.at_level("WARNING", logger="src.ui.restart"):
        assert restart.relaunch(_StarterSpy(result)) is False
    assert "yeniden başlatılamadı" in caplog.text


def test_real_start_detached_is_blocked_in_tests():
    """`conftest.no_real_relaunch` güvencesi."""
    with pytest.raises(AssertionError):
        QProcess.startDetached("program", [], ".")
