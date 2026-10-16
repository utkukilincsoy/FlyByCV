"""src/ui/crash_handler.py testleri."""
import logging
import sys
import threading

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.core.logging_setup import LOG_FILE_NAME, configure_logging
from src.ui.crash_handler import CRASH_DIALOG_TITLE, CrashNotifier, install_crash_handler


def _reset_root_handlers():
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)


@pytest.fixture
def _clean_excepthooks():
    original_excepthook = sys.excepthook
    original_thread_excepthook = threading.excepthook
    yield
    sys.excepthook = original_excepthook
    threading.excepthook = original_thread_excepthook
    _reset_root_handlers()


def _make_exc_info(message: str = "çöktü"):
    try:
        raise ValueError(message)
    except ValueError:
        return sys.exc_info()


def test_install_crash_handler_without_app_only_logs(monkeypatch, tmp_path, _clean_excepthooks):
    """`QApplication.instance()` yoksa dialog denenmemeli, yalnızca log dosyasına yazılmalı."""
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path))
    monkeypatch.setattr(QApplication, "instance", lambda: None)
    dialog_calls = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: dialog_calls.append((a, k)))

    configure_logging()
    install_crash_handler()

    exc_type, exc_value, exc_tb = _make_exc_info("konsolsuz çökme")
    sys.excepthook(exc_type, exc_value, exc_tb)
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert dialog_calls == []
    content = (tmp_path / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "konsolsuz çökme" in content


def test_install_crash_handler_with_app_shows_dialog(qtbot, monkeypatch, tmp_path, _clean_excepthooks):
    """`QApplication` gerçekten varsa (`qtbot`) hata, log dosyasının yolunu içeren bir
    `QMessageBox`la kullanıcıya gösterilmeli."""
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path))
    dialog_calls = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: dialog_calls.append(a))

    configure_logging()
    install_crash_handler()

    exc_type, exc_value, exc_tb = _make_exc_info("gösterilecek hata")
    sys.excepthook(exc_type, exc_value, exc_tb)

    assert len(dialog_calls) == 1
    _parent, title, message = dialog_calls[0]
    assert title == CRASH_DIALOG_TITLE
    assert "gösterilecek hata" in message
    assert str(tmp_path / "logs" / LOG_FILE_NAME) in message


def test_notify_from_background_thread_shows_dialog_on_main_thread(qtbot, monkeypatch, tmp_path):
    """Arka plandaki bir iş parçacığından `notify()` çağrılsa bile `QMessageBox` HER ZAMAN
    ana iş parçacığında açılmalı -- Qt widget'ları başka bir iş parçacığından çağrılamaz."""
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path))
    dialog_threads = []

    def _fake_critical(*args, **kwargs):
        dialog_threads.append(threading.current_thread())

    monkeypatch.setattr(QMessageBox, "critical", _fake_critical)

    configure_logging()
    notifier = CrashNotifier()

    exc_type, exc_value, exc_tb = _make_exc_info("arka plandan bildirim")
    background = threading.Thread(target=lambda: notifier.notify(exc_value))
    background.start()
    background.join()

    qtbot.waitUntil(lambda: len(dialog_threads) == 1, timeout=2000)
    assert dialog_threads[0] is threading.main_thread()
    _reset_root_handlers()
