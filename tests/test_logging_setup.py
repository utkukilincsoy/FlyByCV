import logging
import os
import sys
import threading

import pytest

from src.core.logging_setup import (
    LOG_FILE_NAME,
    NOISY_LOGGERS,
    _ensure_std_streams,
    configure_logging,
    install_exception_logging,
    quiet_noisy_loggers,
)


def test_noisy_loggers_are_raised_to_warning():
    """httpx ve google_genai INFO satırları görünmemeli."""
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)

    quiet_noisy_loggers()

    for name in NOISY_LOGGERS:
        logger = logging.getLogger(name)
        assert not logger.isEnabledFor(logging.INFO)
        assert logger.isEnabledFor(logging.WARNING)


def test_our_own_loggers_are_untouched():
    """Kısıtlama yalnızca listedeki kütüphaneleri etkilemeli."""
    quiet_noisy_loggers()
    assert logging.getLogger("src.cv_builder.application_builder").level == logging.NOTSET


def _reset_root_handlers():
    root = logging.getLogger()
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)


def test_configure_logging_writes_to_log_file(monkeypatch, tmp_path):
    """Dosya handler'ı her koşulda eklenir ve mesaj gerçekten dosyaya yazılır."""
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path))
    try:
        configure_logging()
        logging.getLogger("test.logger").info("merhaba dünya")
        for handler in logging.getLogger().handlers:
            handler.flush()

        log_path = tmp_path / "logs" / LOG_FILE_NAME
        assert log_path.exists()
        assert "merhaba dünya" in log_path.read_text(encoding="utf-8")
    finally:
        _reset_root_handlers()


def test_configure_logging_skips_console_handler_without_stderr(monkeypatch, tmp_path):
    """Konsol yoksa (`sys.stderr` `None`, paketlenmiş .exe) konsol handler'ı hiç eklenmemeli."""
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path))
    monkeypatch.setattr("src.core.logging_setup.sys.stderr", None)
    try:
        configure_logging()

        handlers = logging.getLogger().handlers
        assert len(handlers) == 1
        assert isinstance(handlers[0], logging.FileHandler)
    finally:
        _reset_root_handlers()


def test_configure_logging_does_not_stack_handlers(monkeypatch, tmp_path):
    """Tekrar çağrıldığında eski handler'lar temizlenmeli, aksi halde satırlar tekrarlanır."""
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path))
    try:
        configure_logging()
        configure_logging()
        assert len(logging.getLogger().handlers) <= 2
    finally:
        _reset_root_handlers()


def test_ensure_std_streams_binds_none_to_devnull(monkeypatch):
    """Konsolsuz ortamda (`sys.stdout`/`sys.stderr` `None`) `print()`/`.write()` çökmemeli."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    _ensure_std_streams()

    try:
        assert sys.stdout is not None
        assert sys.stderr is not None
        assert sys.stdout.name == os.devnull
        assert sys.stderr.name == os.devnull
        print("çökmemeli", file=sys.stdout)  # gerçek çöküş senaryosu: AttributeError vermemeli
    finally:
        sys.stdout.close()
        sys.stderr.close()


def test_ensure_std_streams_leaves_existing_streams_untouched(monkeypatch):
    """Konsol GERÇEKTEN varsa (dev ortamı) dokunulmamalı."""
    sentinel_out, sentinel_err = object(), object()
    monkeypatch.setattr(sys, "stdout", sentinel_out)
    monkeypatch.setattr(sys, "stderr", sentinel_err)

    _ensure_std_streams()

    assert sys.stdout is sentinel_out
    assert sys.stderr is sentinel_err


@pytest.fixture
def _clean_excepthooks():
    """Testler `sys.excepthook`/`threading.excepthook`'u değiştirir; testten sonra gerçek
    değerlere dönülmeli, aksi halde pytest'in kendi hata raporlaması bozulabilir."""
    original_excepthook = sys.excepthook
    original_thread_excepthook = threading.excepthook
    yield
    sys.excepthook = original_excepthook
    threading.excepthook = original_thread_excepthook
    _reset_root_handlers()


def _make_exc_info(message: str = "test hatası"):
    try:
        raise ValueError(message)
    except ValueError:
        return sys.exc_info()


def test_install_exception_logging_writes_full_traceback(monkeypatch, tmp_path, _clean_excepthooks):
    """`sys.excepthook` tetiklenince tam traceback log dosyasına yazılmalı."""
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path))
    configure_logging()
    install_exception_logging()

    exc_type, exc_value, exc_tb = _make_exc_info("beklenmeyen kırılma")
    sys.excepthook(exc_type, exc_value, exc_tb)
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = (tmp_path / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "Yakalanmamış hata" in content
    assert "beklenmeyen kırılma" in content
    assert "Traceback" in content


def test_install_exception_logging_calls_on_exception_callback(monkeypatch, tmp_path, _clean_excepthooks):
    """Verilen `on_exception` geri çağırması loglamadan SONRA, hatanın kendisiyle çağrılmalı."""
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path))
    configure_logging()
    received = []
    install_exception_logging(on_exception=received.append)

    exc_type, exc_value, exc_tb = _make_exc_info()
    sys.excepthook(exc_type, exc_value, exc_tb)

    assert received == [exc_value]


def test_install_exception_logging_skips_keyboard_interrupt(monkeypatch, tmp_path, _clean_excepthooks):
    """Ctrl+C bir çökme değildir: loglanmamalı, geri çağırma tetiklenmemeli, varsayılana bırakılmalı."""
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path))
    configure_logging()
    received = []
    install_exception_logging(on_exception=received.append)

    default_hook_calls = []
    monkeypatch.setattr(sys, "__excepthook__", lambda *args: default_hook_calls.append(args))

    try:
        raise KeyboardInterrupt()
    except KeyboardInterrupt:
        exc_type, exc_value, exc_tb = sys.exc_info()
    sys.excepthook(exc_type, exc_value, exc_tb)
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert received == []
    assert len(default_hook_calls) == 1
    content = (tmp_path / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "Yakalanmamış hata" not in content


def test_threading_excepthook_logs_thread_name_and_calls_callback(monkeypatch, tmp_path, _clean_excepthooks):
    """`threading.excepthook` de aynı şekilde dosyaya yazmalı ve geri çağırmayı tetiklemeli."""
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path))
    configure_logging()
    received = []
    install_exception_logging(on_exception=received.append)

    exc_type, exc_value, exc_tb = _make_exc_info("arka plan hatası")
    worker_thread = threading.Thread(name="arka-plan-isi")
    args = threading.ExceptHookArgs((exc_type, exc_value, exc_tb, worker_thread))
    threading.excepthook(args)
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = (tmp_path / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "arka-plan-isi" in content
    assert "arka plan hatası" in content
    assert received == [exc_value]
