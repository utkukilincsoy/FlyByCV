"""src/ui/autopilot_tab.py testleri (qtbot; gerçek arka plan işçisi/ağ/QMessageBox exec YOK).

Gerçek bir çalıştırma (`autopilot_run_job` üzerinden bir `QueueWorker` başlatmak) burada
TETİKLENMİYOR -- kararsız bir test üretmemek için (arka plan iş parçacığı + gerçek zamanlama,
`test_ui_queue_tab.py`'nin aynı gerekçesi). O akışın kendisi zaten `test_ui_jobs.py`
(`autopilot_run_job`) ve `test_autopilot_runner.py` (`run_autopilot`) içinde kapsandı. Burada
yalnızca "Başlat" öncesi karar mantığı (diğer sekmelerdeki iş kilidi, onay diyalogları) ve
dinlenme durumundaki davranış (sayaçlar, log, kalıcı durma sebebi etiketi) test ediliyor --
bunun için `_start_worker` monkeypatch'lenip gerçek işçi hiç başlatılmıyor.
"""
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from src.core.db import get_session
from src.core.settings import KEY_SENDING_PAUSED, KEY_TEST_MODE, set_setting
from src.ui.autopilot_data import DEFAULT_STAGE_TEXT, DEFAULT_STOP_REASON_TEXT
from src.ui.autopilot_tab import AutopilotTab
from src.ui.companies_tab import CompaniesTab
from src.ui.queue_tab import QueueTab

EXAMPLE_PROFILE_PATH = Path("data/master_profile.example.json")


def _stub_clicked_button(monkeypatch, text: str) -> None:
    """`box.exec()`'i atlar, `box.clickedButton()`'ı METNİYLE eşleşen butona sabitler.

    `test_ui_settings_tab.py::_stub_clicked_button` ile AYNI kalıp -- `QMessageBox.buttons()`'ın
    sırası eklenme sırasıyla aynı değil (Qt rol'e göre platforma özgü bir sırayla düzenliyor).
    """

    def _clicked(self):
        for button in self.buttons():
            if button.text() == text:
                return button
        raise AssertionError(f"'{text}' metinli buton bulunamadı: {[b.text() for b in self.buttons()]}")

    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(QMessageBox, "clickedButton", _clicked)


def _make_tab(qtbot, engine, profile_path: Path = EXAMPLE_PROFILE_PATH):
    companies_tab = CompaniesTab(engine, profile_path)
    queue_tab = QueueTab(engine, profile_path)
    tab = AutopilotTab(engine, profile_path, companies_tab, queue_tab)
    qtbot.addWidget(companies_tab)
    qtbot.addWidget(queue_tab)
    qtbot.addWidget(tab)
    tab.show()  # isVisible() denetimleri için -- widget hiç gösterilmeden hep False döner
    return tab, companies_tab, queue_tab


# ---------------------------------------------------------------------------
# Dinlenme durumu
# ---------------------------------------------------------------------------


def test_autopilot_tab_starts_with_stop_disabled_and_start_enabled(qtbot, engine):
    tab, _, _ = _make_tab(qtbot, engine)

    assert tab._start_button.isEnabled()
    assert not tab._stop_button.isEnabled()


def test_autopilot_tab_shows_default_stage_and_stop_reason_before_any_run(qtbot, engine):
    tab, _, _ = _make_tab(qtbot, engine)

    assert tab._stage_label.text() == DEFAULT_STAGE_TEXT
    assert tab._stop_reason_label.text() == DEFAULT_STOP_REASON_TEXT


def test_autopilot_tab_test_mode_banner_visible_by_default(qtbot, engine):
    """`test_mode` varsayılan AÇIK (Faz 6.1) -- şerit açılışta görünür olmalı."""
    tab, _, _ = _make_tab(qtbot, engine)

    assert tab._test_mode_banner.isVisible()


def test_autopilot_tab_test_mode_banner_hidden_when_test_mode_off(qtbot, engine):
    with get_session(engine) as session:
        set_setting(session, KEY_TEST_MODE, False)
        session.commit()

    tab, _, _ = _make_tab(qtbot, engine)

    assert not tab._test_mode_banner.isVisible()


def test_autopilot_tab_is_autopilot_running_false_when_no_run(qtbot, engine):
    tab, _, _ = _make_tab(qtbot, engine)

    assert tab.is_autopilot_running() is False


def test_autopilot_tab_stop_autopilot_run_is_noop_without_active_run(qtbot, engine):
    tab, _, _ = _make_tab(qtbot, engine)

    tab.stop_autopilot_run()  # patlamamalı

    assert tab.is_autopilot_running() is False


def test_autopilot_tab_on_emergency_stop_appends_log_without_active_run(qtbot, engine):
    tab, _, _ = _make_tab(qtbot, engine)

    tab.on_emergency_stop()

    assert "ACİL DURDUR açıldı" in tab._log_view.toPlainText()


def test_autopilot_tab_wait_for_autopilot_run_returns_true_when_no_run(qtbot, engine):
    tab, _, _ = _make_tab(qtbot, engine)

    assert tab.wait_for_autopilot_run(100) is True


# ---------------------------------------------------------------------------
# "Başlat": diğer sekmelerdeki iş kilidi
# ---------------------------------------------------------------------------


def test_autopilot_tab_start_blocked_when_companies_tab_job_running(qtbot, engine, monkeypatch):
    tab, companies_tab, _ = _make_tab(qtbot, engine)
    monkeypatch.setattr(companies_tab, "is_job_running", lambda: True)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    started = []
    monkeypatch.setattr(tab, "_start_worker", lambda profile: started.append(profile))

    tab._on_start()

    assert started == []


def test_autopilot_tab_start_blocked_when_queue_tab_job_running(qtbot, engine, monkeypatch):
    tab, _, queue_tab = _make_tab(qtbot, engine)
    monkeypatch.setattr(queue_tab, "is_job_running", lambda: True)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    started = []
    monkeypatch.setattr(tab, "_start_worker", lambda profile: started.append(profile))

    tab._on_start()

    assert started == []


def test_autopilot_tab_start_with_broken_profile_shows_error(qtbot, engine, tmp_path, monkeypatch):
    tab, _, _ = _make_tab(qtbot, engine, profile_path=tmp_path / "eksik_profil.json")
    criticals = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: criticals.append(a) or QMessageBox.Ok)
    started = []
    monkeypatch.setattr(tab, "_start_worker", lambda profile: started.append(profile))

    tab._on_start()

    assert criticals
    assert started == []


# ---------------------------------------------------------------------------
# "Başlat": ACİL DURDUR / test modu / gerçek gönderim onayı
# ---------------------------------------------------------------------------


def test_autopilot_tab_start_blocked_when_sending_paused(qtbot, engine, monkeypatch):
    with get_session(engine) as session:
        set_setting(session, KEY_SENDING_PAUSED, True)
        session.commit()

    tab, _, _ = _make_tab(qtbot, engine)
    criticals = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: criticals.append(a) or QMessageBox.Ok)
    started = []
    monkeypatch.setattr(tab, "_start_worker", lambda profile: started.append(profile))

    tab._on_start()

    assert criticals
    assert started == []


def test_autopilot_tab_start_shows_no_confirmation_when_test_mode_on(qtbot, engine, monkeypatch):
    tab, _, _ = _make_tab(qtbot, engine)  # test_mode varsayılan AÇIK
    started = []
    monkeypatch.setattr(tab, "_start_worker", lambda profile: started.append(profile))

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("Test modunda GERÇEK GÖNDERİM onay kutusu ÇIKMAMALI")

    monkeypatch.setattr(QMessageBox, "critical", _fail_if_called)

    tab._on_start()

    assert len(started) == 1


def test_autopilot_tab_start_proceeds_when_real_send_confirmed(qtbot, engine, monkeypatch):
    with get_session(engine) as session:
        set_setting(session, KEY_TEST_MODE, False)
        session.commit()

    tab, _, _ = _make_tab(qtbot, engine)
    started = []
    monkeypatch.setattr(tab, "_start_worker", lambda profile: started.append(profile))
    _stub_clicked_button(monkeypatch, "Evet, başlat")

    tab._on_start()

    assert len(started) == 1


def test_autopilot_tab_start_cancelled_when_real_send_declined(qtbot, engine, monkeypatch):
    with get_session(engine) as session:
        set_setting(session, KEY_TEST_MODE, False)
        session.commit()

    tab, _, _ = _make_tab(qtbot, engine)
    started = []
    monkeypatch.setattr(tab, "_start_worker", lambda profile: started.append(profile))
    _stub_clicked_button(monkeypatch, "Vazgeç")

    tab._on_start()

    assert started == []


# ---------------------------------------------------------------------------
# Çalışırken diğer sekmelerin kilitlenmesi
# ---------------------------------------------------------------------------


def test_autopilot_tab_set_running_ui_locks_other_tabs(qtbot, engine):
    tab, companies_tab, queue_tab = _make_tab(qtbot, engine)

    tab._set_running_ui(True)

    assert not tab._start_button.isEnabled()
    assert tab._stop_button.isEnabled()
    assert not companies_tab._import_button.isEnabled()
    assert not queue_tab._start_button.isEnabled()

    tab._set_running_ui(False)

    assert tab._start_button.isEnabled()
    assert not tab._stop_button.isEnabled()
    assert companies_tab._import_button.isEnabled()
    assert queue_tab._start_button.isEnabled()


# ---------------------------------------------------------------------------
# Kaynak özeti + Google Places maliyet önizlemesi (PLAN.md 10.6h)
# ---------------------------------------------------------------------------


def _configure_sources(engine, monkeypatch, *, places: bool):
    from src.autopilot import runner
    from src.core.settings import (
        KEY_AUTOPILOT_CITY,
        KEY_PROFESSION,
        KEY_USE_GOOGLE_PLACES,
        KEY_USE_OSM,
    )

    monkeypatch.setattr(runner.secrets, "get_api_key", lambda provider: "sahte" if places else None)
    with get_session(engine) as session:
        set_setting(session, KEY_PROFESSION, "yazilim")
        set_setting(session, KEY_AUTOPILOT_CITY, "06")
        set_setting(session, KEY_USE_OSM, False)
        set_setting(session, KEY_USE_GOOGLE_PLACES, places)
        session.commit()


def test_source_preview_label_shows_sources_and_is_gray_when_places_off(qtbot, engine, monkeypatch):
    _configure_sources(engine, monkeypatch, places=False)

    tab, _, _ = _make_tab(qtbot, engine)

    text = tab._source_preview_label.text()
    assert "Teknokent firma rehberleri" in text and "Google" not in text
    assert "color: gray" in tab._source_preview_label.styleSheet()


def test_source_preview_label_shows_cost_and_turns_red_when_free_quota_exceeded(qtbot, engine, monkeypatch):
    from src.core.models import utcnow
    from src.core.settings import (
        KEY_PLACES_MONTHLY_LIMIT,
        KEY_PLACES_REQUESTS_MONTH_KEY,
        KEY_PLACES_REQUESTS_THIS_MONTH,
    )
    from src.core.timeutil import pacific_month_key

    _configure_sources(engine, monkeypatch, places=True)
    with get_session(engine) as session:
        set_setting(session, KEY_PLACES_MONTHLY_LIMIT, 2000)
        set_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH, 990)
        set_setting(session, KEY_PLACES_REQUESTS_MONTH_KEY, pacific_month_key(utcnow()))
        session.commit()

    tab, _, _ = _make_tab(qtbot, engine)

    text = tab._source_preview_label.text()
    assert "Google Places: en fazla" in text and "990 / 2000" in text and "ÜCRETSİZ KOTA AŞILIR" in text
    assert "#b91c1c" in tab._source_preview_label.styleSheet()


def test_start_with_places_asks_confirmation_and_declining_does_not_start(qtbot, engine, monkeypatch):
    _configure_sources(engine, monkeypatch, places=True)
    tab, _, _ = _make_tab(qtbot, engine)  # test modu açık: gerçek gönderim kutusu yok
    started = []
    monkeypatch.setattr(tab, "_start_worker", lambda profile: started.append(profile))
    _stub_clicked_button(monkeypatch, "Vazgeç")

    tab._on_start()

    assert started == []


def test_start_with_places_proceeds_when_confirmed(qtbot, engine, monkeypatch):
    _configure_sources(engine, monkeypatch, places=True)
    tab, _, _ = _make_tab(qtbot, engine)
    started = []
    monkeypatch.setattr(tab, "_start_worker", lambda profile: started.append(profile))
    _stub_clicked_button(monkeypatch, "Evet, başlat")

    tab._on_start()

    assert len(started) == 1


def test_start_with_places_off_shows_no_places_confirmation(qtbot, engine, monkeypatch):
    """Benim kurulumum (Places kapalı): "Başlat" ekstra bir onay sormaz, davranış değişmez."""
    _configure_sources(engine, monkeypatch, places=False)
    tab, _, _ = _make_tab(qtbot, engine)
    started = []
    monkeypatch.setattr(tab, "_start_worker", lambda profile: started.append(profile))

    def _no_dialog(self):
        raise AssertionError("Places kapalıyken onay penceresi çıkmamalı")

    monkeypatch.setattr(QMessageBox, "exec", _no_dialog)

    tab._on_start()

    assert len(started) == 1
