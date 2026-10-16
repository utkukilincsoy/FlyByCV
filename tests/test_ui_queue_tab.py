"""src/ui/queue_tab.py testleri (qtbot; gerçek SMTP yok).

"Tek gönder" akışının kendisi burada tetiklenmiyor -- onu oluşturan parçalar zaten
`test_ui_queue_data.py` (`plan_send`) ve `test_ui_jobs.py` (`send_one_job`) içinde test
edildi. Burada tablo/filtre/önizleme/onayla-reddet-kurtarma davranışı kapsanıyor.
"""
from PySide6.QtWidgets import QMessageBox

from src.core.db import get_session
from src.core.models import (
    STATUS_APPROVED,
    STATUS_QUEUED,
    STATUS_REJECTED,
    STATUS_SENDING,
    STATUS_SENT,
    Application,
)
from src.ui.queue_data import FILTER_ACTIVE
from src.ui.queue_tab import QueueTab
from tests.conftest import build_application_row


def _make_tab(qtbot, engine, tmp_path) -> QueueTab:
    profile_path = tmp_path / "master_profile.json"  # bu testlerde okunmuyor (gönderim yok)
    tab = QueueTab(engine, profile_path)
    qtbot.addWidget(tab)
    return tab


def test_queue_tab_opens_with_active_filter_selected(qtbot, engine, tmp_path):
    tab = _make_tab(qtbot, engine, tmp_path)

    assert tab._filter_box.currentData() == FILTER_ACTIVE


def test_queue_tab_active_filter_hides_terminal_statuses(qtbot, engine, tmp_path, pdf_path):
    with get_session(engine) as session:
        build_application_row(session, domain="a.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED)
        build_application_row(session, domain="b.com", cv_pdf_path=pdf_path, status=STATUS_SENT)
        session.commit()

    tab = _make_tab(qtbot, engine, tmp_path)

    assert tab._proxy.rowCount() == 1


def test_queue_tab_filter_switch_shows_all_rows(qtbot, engine, tmp_path, pdf_path):
    with get_session(engine) as session:
        build_application_row(session, domain="a.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED)
        build_application_row(session, domain="b.com", cv_pdf_path=pdf_path, status=STATUS_SENT)
        session.commit()

    tab = _make_tab(qtbot, engine, tmp_path)
    tab._filter_box.setCurrentIndex(1)  # "Hepsi"

    assert tab._proxy.rowCount() == 2


def test_queue_tab_selecting_row_fills_preview_panel(qtbot, engine, tmp_path, pdf_path):
    with get_session(engine) as session:
        build_application_row(
            session, domain="a.com", cv_pdf_path=pdf_path, mail_subject="Staj Başvurusu – a.com"
        )
        session.commit()

    tab = _make_tab(qtbot, engine, tmp_path)
    tab._table.selectRow(0)

    assert tab._preview._subject_label.text() == "Staj Başvurusu – a.com"


def test_queue_tab_no_selection_clears_preview(qtbot, engine, tmp_path, pdf_path):
    with get_session(engine) as session:
        build_application_row(session, domain="a.com", cv_pdf_path=pdf_path)
        session.commit()

    tab = _make_tab(qtbot, engine, tmp_path)
    tab._table.selectRow(0)
    tab._table.clearSelection()

    assert tab._selected_row() is None


def test_queue_tab_sending_row_enables_only_recovery_buttons(qtbot, engine, tmp_path, pdf_path):
    with get_session(engine) as session:
        build_application_row(session, domain="a.com", cv_pdf_path=pdf_path, status=STATUS_SENDING)
        session.commit()

    tab = _make_tab(qtbot, engine, tmp_path)
    tab._table.selectRow(0)

    assert tab._mark_sent_button.isEnabled()
    assert tab._mark_queued_button.isEnabled()
    assert not tab._approve_button.isEnabled()
    assert not tab._reject_button.isEnabled()
    assert not tab._send_button.isEnabled()


def test_queue_tab_queued_row_enables_approve_reject_send_only(qtbot, engine, tmp_path, pdf_path):
    with get_session(engine) as session:
        build_application_row(session, domain="a.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED)
        session.commit()

    tab = _make_tab(qtbot, engine, tmp_path)
    tab._table.selectRow(0)

    assert tab._approve_button.isEnabled()
    assert tab._reject_button.isEnabled()
    assert tab._send_button.isEnabled()
    assert not tab._mark_sent_button.isEnabled()
    assert not tab._mark_queued_button.isEnabled()


def test_queue_tab_mark_queued_recovers_sending_status(qtbot, engine, tmp_path, pdf_path):
    with get_session(engine) as session:
        application, _ = build_application_row(
            session, domain="a.com", cv_pdf_path=pdf_path, status=STATUS_SENDING
        )
        session.commit()
        application_id = application.id

    tab = _make_tab(qtbot, engine, tmp_path)
    tab._table.selectRow(0)
    tab._on_mark_queued()

    with get_session(engine) as session:
        refreshed = session.get(Application, application_id)
        assert refreshed.status == STATUS_QUEUED


def test_queue_tab_mark_sent_recovers_sending_status(qtbot, engine, tmp_path, pdf_path):
    with get_session(engine) as session:
        application, _ = build_application_row(
            session, domain="a.com", cv_pdf_path=pdf_path, status=STATUS_SENDING
        )
        session.commit()
        application_id = application.id

    tab = _make_tab(qtbot, engine, tmp_path)
    tab._table.selectRow(0)
    tab._on_mark_sent()

    with get_session(engine) as session:
        refreshed = session.get(Application, application_id)
        assert refreshed.status == STATUS_SENT
        assert refreshed.sent_at is not None


def test_queue_tab_approve_changes_status(qtbot, engine, tmp_path, pdf_path):
    with get_session(engine) as session:
        application, _ = build_application_row(
            session, domain="a.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED
        )
        session.commit()
        application_id = application.id

    tab = _make_tab(qtbot, engine, tmp_path)
    tab._table.selectRow(0)
    tab._on_approve()

    with get_session(engine) as session:
        refreshed = session.get(Application, application_id)
        assert refreshed.status == STATUS_APPROVED


def test_queue_tab_reject_changes_status(qtbot, engine, tmp_path, pdf_path):
    with get_session(engine) as session:
        application, _ = build_application_row(
            session, domain="a.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED
        )
        session.commit()
        application_id = application.id

    tab = _make_tab(qtbot, engine, tmp_path)
    tab._table.selectRow(0)
    tab._on_reject()

    with get_session(engine) as session:
        refreshed = session.get(Application, application_id)
        assert refreshed.status == STATUS_REJECTED


# ---------------------------------------------------------------------------
# Toplu gönderim: başlat/durdur, canlı log, geri sayım (5.2b)
#
# Turun kendisi (gerçek `QueueWorker` başlatarak) burada TETİKLENMİYOR -- kararsız bir test
# üretmemek için (arka plan iş parçacığı + gerçek zamanlama). Turun mantığı zaten
# `test_ui_jobs.py::queue_run_job` testlerinde kapsandı. Burada yalnızca sekmenin dinlenme
# durumundaki davranışı (butonlar, log paneli, ACİL DURDUR bağlantısı) test ediliyor.
# ---------------------------------------------------------------------------


def test_queue_tab_starts_with_stop_disabled_and_start_enabled(qtbot, engine, tmp_path):
    tab = _make_tab(qtbot, engine, tmp_path)

    assert tab._start_button.isEnabled()
    assert not tab._stop_button.isEnabled()


def test_queue_tab_is_queue_running_false_when_no_run(qtbot, engine, tmp_path):
    tab = _make_tab(qtbot, engine, tmp_path)

    assert tab.is_queue_running() is False


def test_queue_tab_stop_queue_run_is_noop_without_active_run(qtbot, engine, tmp_path):
    tab = _make_tab(qtbot, engine, tmp_path)

    tab.stop_queue_run()  # patlamamalı

    assert tab.is_queue_running() is False


def test_queue_tab_on_emergency_stop_appends_log_without_active_run(qtbot, engine, tmp_path):
    tab = _make_tab(qtbot, engine, tmp_path)

    tab.on_emergency_stop()  # tur yokken de çağrılabilir, patlamamalı

    assert "ACİL DURDUR açıldı" in tab._log_view.toPlainText()


def test_queue_tab_wait_for_queue_run_returns_true_when_no_run(qtbot, engine, tmp_path):
    tab = _make_tab(qtbot, engine, tmp_path)

    assert tab.wait_for_queue_run(100) is True


# ---------------------------------------------------------------------------
# Otopilot çalışırken kilitlenme (PLAN Adım 9.6) -- "Tek gönder"/"Gönderimi başlat" kilitlenir,
# Onayla/Reddet gibi yalnızca DB yazan butonlar ETKİLENMEZ.
# ---------------------------------------------------------------------------


def test_queue_tab_is_job_running_false_when_no_run(qtbot, engine, tmp_path):
    tab = _make_tab(qtbot, engine, tmp_path)

    assert tab.is_job_running() is False


def test_queue_tab_set_autopilot_active_locks_start_button(qtbot, engine, tmp_path):
    tab = _make_tab(qtbot, engine, tmp_path)

    tab.set_autopilot_active(True)
    assert not tab._start_button.isEnabled()

    tab.set_autopilot_active(False)
    assert tab._start_button.isEnabled()


def test_queue_tab_set_autopilot_active_locks_send_button_but_not_approve(
    qtbot, engine, tmp_path, pdf_path
):
    with get_session(engine) as session:
        build_application_row(session, domain="a.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED)
        session.commit()

    tab = _make_tab(qtbot, engine, tmp_path)
    tab._table.selectRow(0)

    tab.set_autopilot_active(True)
    assert not tab._send_button.isEnabled()
    assert tab._approve_button.isEnabled()
    assert tab._reject_button.isEnabled()

    tab.set_autopilot_active(False)
    assert tab._send_button.isEnabled()


def test_queue_tab_guard_running_blocks_and_warns_while_autopilot_active(
    qtbot, engine, tmp_path, monkeypatch
):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    tab = _make_tab(qtbot, engine, tmp_path)
    tab.set_autopilot_active(True)

    assert tab._guard_running() is True
