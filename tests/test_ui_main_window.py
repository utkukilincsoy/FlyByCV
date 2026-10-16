"""src/ui/main_window.py için küçük bir duman (smoke) testi."""
import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox
from sqlmodel import create_engine, select

from src.core.backup import backups_dir_for, create_backup
from src.core.db import configure_sqlite_engine, get_session, init_db
from src.core.models import Company
from src.core.update_check import UpdateInfo
from src.core.version import APP_NAME, __version__
from src.ui import main_window
from src.ui.main_window import AUTOPILOT_TAB_INDEX, TAB_TITLES, MainWindow
from src.ui.update_banner import UpdateBanner
from tests.conftest import build_company


def test_main_window_has_all_tabs_with_status_strip_on_top(qtbot, engine, tmp_path):
    profile_path = tmp_path / "master_profile.json"  # 5.1a'da içeriği okunmuyor, sadece saklanıyor
    window = MainWindow(engine, profile_path)
    qtbot.addWidget(window)

    assert window.tab_widget.count() == len(TAB_TITLES)
    titles = [window.tab_widget.tabText(i) for i in range(window.tab_widget.count())]
    assert titles == list(TAB_TITLES)
    assert titles[0] == "Otopilot"  # PLAN Adım 9.6: tek "Başlat" ile uçtan uca, İLK sekme

    # Durum şeridi, sekmelerden ÖNCE (yerleşimde 0. sırada) olmalı -- "en üstte" gereksinimi.
    central_layout = window.centralWidget().layout()
    assert central_layout.itemAt(0).widget() is window.status_strip
    assert central_layout.itemAt(1).widget() is window.update_banner  # 10.9d: başta gizli
    assert central_layout.itemAt(2).widget() is window.tab_widget
    assert window.update_banner.isHidden()


def test_constructing_the_window_starts_no_update_check(qtbot, engine, tmp_path, monkeypatch):
    """Kurucu ağ/arka plan işi başlatmaz; kontrolü yalnızca `start_update_check()` (main.py) başlatır."""
    started = []
    monkeypatch.setattr(UpdateBanner, "start_check", lambda self: started.append(True))
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)
    assert started == []

    window.start_update_check()
    assert started == [True]


def test_update_found_from_settings_shows_the_banner(qtbot, engine, tmp_path):
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)

    window.settings_tab.update_available.emit(UpdateInfo(version="1.2.0", url="https://github.com/a/b/releases/latest"))

    assert not window.update_banner.isHidden()
    assert window.update_banner.info.version == "1.2.0"


def test_window_title_shows_app_name_and_version_from_the_single_source(qtbot, engine, tmp_path):
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)

    assert window.windowTitle() == f"{APP_NAME} {__version__}"


def test_window_opens_on_the_autopilot_tab_and_the_wizard_can_send_the_user_back_to_it(
    qtbot, engine, tmp_path
):
    """Taze kurulumda sihirbazdan sonra "Otopilot'a git" için ana pencere zaten Otopilot'ta açılır; Ayarlar'dan
    yeniden açılan sihirbaz bitince `autopilot_requested` sekmeyi geri çevirir (PLAN.md Adım 10.2m)."""
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)
    assert window.tab_widget.currentIndex() == AUTOPILOT_TAB_INDEX == 0

    window.tab_widget.setCurrentIndex(TAB_TITLES.index("Dashboard"))
    window.settings_tab.autopilot_requested.emit()

    assert window.tab_widget.currentIndex() == AUTOPILOT_TAB_INDEX


# ---------------------------------------------------------------------------
# closeEvent: gönderim turu sürerken kapanış koruması (5.2b)
# ---------------------------------------------------------------------------


def test_close_event_accepted_when_no_queue_run(qtbot, engine, tmp_path):
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()


def test_close_event_rejected_when_queue_running_and_user_declines(qtbot, engine, tmp_path, monkeypatch):
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)

    monkeypatch.setattr(window.queue_tab, "is_queue_running", lambda: True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.No)

    event = QCloseEvent()
    window.closeEvent(event)

    assert not event.isAccepted()


def test_close_event_stops_run_and_waits_when_user_confirms(qtbot, engine, tmp_path, monkeypatch):
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)

    stop_calls = []
    monkeypatch.setattr(window.queue_tab, "is_queue_running", lambda: True)
    monkeypatch.setattr(window.queue_tab, "stop_queue_run", lambda: stop_calls.append(1))
    monkeypatch.setattr(window.queue_tab, "wait_for_queue_run", lambda timeout_ms: True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
    assert stop_calls == [1]


def test_close_event_still_closes_after_wait_times_out(qtbot, engine, tmp_path, monkeypatch):
    """Bekleme süresi aşılsa bile pencere kapanır -- bütünlük kurtarma butonlarıyla korunur."""
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)

    monkeypatch.setattr(window.queue_tab, "is_queue_running", lambda: True)
    monkeypatch.setattr(window.queue_tab, "stop_queue_run", lambda: None)
    monkeypatch.setattr(window.queue_tab, "wait_for_queue_run", lambda timeout_ms: False)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()


# ---------------------------------------------------------------------------
# Durum şeridindeki ACİL DURDUR -> Onay Kuyruğu sekmesine bağlanması (5.2b)
# ---------------------------------------------------------------------------


def test_emergency_stop_signal_reaches_queue_tab(qtbot, engine, tmp_path):
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)

    window.status_strip.emergency_stop_activated.emit()

    assert "ACİL DURDUR açıldı" in window.queue_tab._log_view.toPlainText()


# ---------------------------------------------------------------------------
# Otopilot sekmesi (9.6): kapanış koruması + ACİL DURDUR bağlantısı
# ---------------------------------------------------------------------------


def test_emergency_stop_signal_reaches_autopilot_tab(qtbot, engine, tmp_path):
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)

    window.status_strip.emergency_stop_activated.emit()

    assert "ACİL DURDUR açıldı" in window.autopilot_tab._log_view.toPlainText()


def test_close_event_rejected_when_autopilot_running_and_user_declines(qtbot, engine, tmp_path, monkeypatch):
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)

    monkeypatch.setattr(window.autopilot_tab, "is_autopilot_running", lambda: True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.No)

    event = QCloseEvent()
    window.closeEvent(event)

    assert not event.isAccepted()


def test_close_event_stops_autopilot_and_waits_when_user_confirms(qtbot, engine, tmp_path, monkeypatch):
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)

    stop_calls = []
    monkeypatch.setattr(window.autopilot_tab, "is_autopilot_running", lambda: True)
    monkeypatch.setattr(window.autopilot_tab, "stop_autopilot_run", lambda: stop_calls.append(1))
    monkeypatch.setattr(window.autopilot_tab, "wait_for_autopilot_run", lambda timeout_ms: True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
    assert stop_calls == [1]


def test_close_event_still_closes_after_autopilot_wait_times_out(qtbot, engine, tmp_path, monkeypatch):
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)

    monkeypatch.setattr(window.autopilot_tab, "is_autopilot_running", lambda: True)
    monkeypatch.setattr(window.autopilot_tab, "stop_autopilot_run", lambda: None)
    monkeypatch.setattr(window.autopilot_tab, "wait_for_autopilot_run", lambda timeout_ms: False)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()


# --- Yedekten geri yükleme (Ayarlar > Yedekleme) ---------------------------------------------


class _Messages:
    """`QMessageBox.information/warning` çağrılarını kaydeder (modal pencere açılmasın)."""

    def __init__(self, monkeypatch) -> None:
        self.shown: list[tuple[str, str]] = []
        monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a: self.shown.append(("info", text)))
        monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a: self.shown.append(("warning", text)))


@pytest.fixture
def file_home(tmp_path):
    """Dosya tabanlı (WAL) veritabanı + bir yedek: geri yükleme gerçekten denenebilsin."""
    db_path = tmp_path / "data" / "app.db"
    db_path.parent.mkdir()
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)
    init_db(engine=engine)
    with get_session(engine) as session:
        session.add(build_company(domain="eski.com"))
        session.commit()
    zip_path = create_backup(db_path, tmp_path / "data" / "master_profile.json", backups_dir_for(db_path))
    with get_session(engine) as session:
        session.add(build_company(domain="yeni.com"))
        session.commit()
    yield engine, zip_path, tmp_path / "data" / "master_profile.json"
    engine.dispose()


def _domains(engine) -> list[str]:
    with get_session(engine) as session:
        return sorted(company.domain for company in session.exec(select(Company)).all())


@pytest.mark.parametrize("relaunched", [True, False])
def test_restore_replaces_data_then_restarts_or_asks_manual_open(
    relaunched, qtbot, file_home, monkeypatch
):
    engine, zip_path, profile_path = file_home
    window = MainWindow(engine, profile_path)
    qtbot.addWidget(window)
    messages = _Messages(monkeypatch)
    quit_calls: list[bool] = []
    monkeypatch.setattr(main_window, "relaunch", lambda: relaunched)
    monkeypatch.setattr(main_window.QApplication, "quit", lambda: quit_calls.append(True))

    window.settings_tab.restore_requested.emit(zip_path)

    assert _domains(engine) == ["eski.com"]
    assert messages.shown[0] == ("info", main_window.RESTARTING_TEXT)
    if relaunched:
        assert len(messages.shown) == 1
    else:
        assert messages.shown[1] == ("warning", main_window.MANUAL_RESTART_TEXT)
        assert "Lütfen uygulamayı elle açın" in main_window.MANUAL_RESTART_TEXT
    assert quit_calls == [True]  # her iki durumda da kapanır


@pytest.mark.parametrize(
    "tab_attr, method",
    [
        ("autopilot_tab", "is_autopilot_running"),
        ("queue_tab", "is_job_running"),
        ("companies_tab", "is_job_running"),
        ("replies_tab", "is_job_running"),
    ],
)
def test_restore_refused_while_a_job_is_running(tab_attr, method, qtbot, file_home, monkeypatch):
    engine, zip_path, profile_path = file_home
    window = MainWindow(engine, profile_path)
    qtbot.addWidget(window)
    messages = _Messages(monkeypatch)
    monkeypatch.setattr(getattr(window, tab_attr), method, lambda: True)
    restored: list[bool] = []
    monkeypatch.setattr(main_window, "restore_backup", lambda *a, **k: restored.append(True))
    monkeypatch.setattr(main_window.QApplication, "quit", lambda: pytest.fail("kapanmamalıydı"))

    window.settings_tab.restore_requested.emit(zip_path)

    assert restored == []
    assert _domains(engine) == ["eski.com", "yeni.com"]
    assert messages.shown[0][0] == "warning" and "çalışan bir iş var" in messages.shown[0][1]
    # Yamalar geri alınmazsa test sonunda pencere kapanırken closeEvent "çalışan iş" için modal
    # soru açar ve test sonsuza dek bekler.
    monkeypatch.undo()


def test_invalid_backup_shows_error_and_keeps_app_open(qtbot, file_home, monkeypatch, tmp_path):
    engine, _, profile_path = file_home
    window = MainWindow(engine, profile_path)
    qtbot.addWidget(window)
    messages = _Messages(monkeypatch)
    monkeypatch.setattr(main_window.QApplication, "quit", lambda: pytest.fail("kapanmamalıydı"))
    bogus = tmp_path / "bozuk.zip"
    bogus.write_bytes(b"zip degil")

    window.settings_tab.restore_requested.emit(bogus)

    assert _domains(engine) == ["eski.com", "yeni.com"]
    assert messages.shown == [("warning", "Seçilen dosya geçerli bir yedek değil (zip açılamadı).")]


def test_restore_unavailable_with_in_memory_database(qtbot, engine, tmp_path, monkeypatch):
    window = MainWindow(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(window)
    messages = _Messages(monkeypatch)

    window.settings_tab.restore_requested.emit(tmp_path / "herhangi.zip")

    assert messages.shown == [("warning", main_window.RESTORE_UNAVAILABLE_TEXT)]
