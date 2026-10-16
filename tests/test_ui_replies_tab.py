"""src/ui/replies_tab.py testleri (qtbot; gerçek IMAP yok, `check_inbox_job` sahtelenir).

"Şimdi kontrol et" akışının kendisi (`check_inbox_job`) zaten `test_ui_jobs.py`de test edildi;
burada yalnızca sekmenin tablo/filtre/önizleme/elle düzeltme davranışı ve butonun doğru
sonuçları doğru diyaloglarla gösterip gösterMEDİĞİ kapsanıyor.

Periyodik otomatik kontrol testleri (en altta) gerçek `QTimer`'ı hiç BEKLEMEZ: `_on_auto_tick`
doğrudan çağrılır ve "şu an" `FakeClock` ile enjekte edilir (`src/sender/worker.py`nin test
kalıbıyla aynı, bkz. `tests/conftest.py::FakeClock`) -- gerçek saate bağlı değildir.
"""
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QMessageBox

from src.core.db import get_session
from src.core.models import CLASSIFIED_BY_USER, REPLY_BOUNCE, REPLY_INTERVIEW, REPLY_PENDING, EmailReply
from src.core.settings import KEY_INBOX_CHECK_MINUTES, set_setting
from src.inbox.service import InboxCheckResult
from src.ui.jobs import CheckInboxJobResult
from src.ui.replies_data import FILTER_ALL
from src.ui.replies_tab import RepliesTab
from tests.conftest import FakeClock, build_application_row, build_email_reply

EXAMPLE_PROFILE_PATH = Path("data/master_profile.example.json")


def _make_tab(qtbot, engine, profile_path: Optional[Path] = None, **kwargs) -> RepliesTab:
    tab = RepliesTab(engine, profile_path or Path("olmayan_profil.json"), **kwargs)
    qtbot.addWidget(tab)
    return tab


def _run_worker(qtbot, tab) -> None:
    """Tıklamayla başlayan işçinin bitmesini bekler (bkz. `test_ui_settings_tab._run_worker`)."""
    assert tab._worker is not None
    qtbot.waitUntil(lambda: tab._worker is None, timeout=2000)


# ---------------------------------------------------------------------------
# tablo / filtre / önizleme
# ---------------------------------------------------------------------------


def test_replies_tab_opens_with_all_filter_selected(qtbot, engine):
    tab = _make_tab(qtbot, engine)

    assert tab._filter_box.currentData() == FILTER_ALL


def test_replies_tab_loads_existing_replies(qtbot, engine, pdf_path):
    with get_session(engine) as session:
        application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
        build_email_reply(session, application_id=application.id, message_id="<r@acme.com>")
        session.commit()

    tab = _make_tab(qtbot, engine)

    assert tab._proxy.rowCount() == 1


def test_replies_tab_selecting_row_fills_preview_panel(qtbot, engine, pdf_path):
    with get_session(engine) as session:
        application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
        build_email_reply(
            session, application_id=application.id, message_id="<r@acme.com>", subject="Mülakat daveti"
        )
        session.commit()

    tab = _make_tab(qtbot, engine)
    tab._table.selectRow(0)

    assert tab._preview._subject_label.text() == "Mülakat daveti"


def test_replies_tab_no_selection_clears_preview(qtbot, engine, pdf_path):
    with get_session(engine) as session:
        application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
        build_email_reply(session, application_id=application.id, message_id="<r@acme.com>")
        session.commit()

    tab = _make_tab(qtbot, engine)
    tab._table.selectRow(0)
    tab._table.clearSelection()

    assert tab._selected_row() is None


def test_replies_tab_category_filter_hides_non_matching_rows(qtbot, engine, pdf_path):
    with get_session(engine) as session:
        application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
        build_email_reply(
            session, application_id=application.id, message_id="<a@acme.com>", category=REPLY_PENDING
        )
        build_email_reply(
            session, application_id=application.id, message_id="<b@acme.com>", category=REPLY_BOUNCE
        )
        session.commit()

    tab = _make_tab(qtbot, engine)
    index = tab._filter_box.findData(REPLY_BOUNCE)
    tab._filter_box.setCurrentIndex(index)

    assert tab._proxy.rowCount() == 1


# ---------------------------------------------------------------------------
# elle düzeltme (kısa ömürlü oturum, GUI iş parçacığı)
# ---------------------------------------------------------------------------


def test_replies_tab_manual_correction_updates_category_and_classified_by(qtbot, engine, pdf_path):
    with get_session(engine) as session:
        application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
        reply = build_email_reply(
            session, application_id=application.id, message_id="<r@acme.com>", category=REPLY_PENDING
        )
        session.commit()
        reply_id = reply.id

    tab = _make_tab(qtbot, engine)
    tab._on_category_corrected(reply_id, REPLY_INTERVIEW)

    with get_session(engine) as session:
        refreshed = session.get(EmailReply, reply_id)
        assert refreshed.category == REPLY_INTERVIEW
        assert refreshed.classified_by == CLASSIFIED_BY_USER


def test_replies_tab_manual_correction_unknown_reply_shows_warning(qtbot, engine, monkeypatch):
    tab = _make_tab(qtbot, engine)

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    tab._on_category_corrected(999, REPLY_INTERVIEW)

    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# "Şimdi kontrol et" (arka plan işçisi, `check_inbox_job` sahtelenir)
# ---------------------------------------------------------------------------


def test_check_now_shows_summary_and_reloads_on_success(qtbot, engine, monkeypatch):
    tab = _make_tab(qtbot, engine, EXAMPLE_PROFILE_PATH)

    fake_result = CheckInboxJobResult(
        result=InboxCheckResult(headers_fetched=5, candidates=1, new_replies=1)
    )
    monkeypatch.setattr(
        "src.ui.replies_tab.check_inbox_job",
        lambda engine, profile, *, on_progress, token: fake_result,
    )
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a) or QMessageBox.Ok)

    tab._on_check_now()
    _run_worker(qtbot, tab)

    assert len(infos) == 1
    assert "5 başlık tarandı" in infos[0][2]


def test_check_now_no_applications_sent_shows_info_not_warning(qtbot, engine, monkeypatch):
    """"Henüz gönderilmiş başvuru yok" bir HATA değil -- bilgilendirme kutusuyla gösterilmeli."""
    tab = _make_tab(qtbot, engine, EXAMPLE_PROFILE_PATH)

    fake_result = CheckInboxJobResult(
        error="Henüz gönderilmiş başvuru yok, taranacak bir şey yok", no_applications_sent=True
    )
    monkeypatch.setattr(
        "src.ui.replies_tab.check_inbox_job",
        lambda engine, profile, *, on_progress, token: fake_result,
    )
    infos = []
    warnings = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a) or QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    tab._on_check_now()
    _run_worker(qtbot, tab)

    assert len(infos) == 1
    assert warnings == []
    assert "Henüz gönderilmiş" in infos[0][2]


def test_check_now_setup_error_shows_warning(qtbot, engine, monkeypatch):
    tab = _make_tab(qtbot, engine, EXAMPLE_PROFILE_PATH)

    fake_result = CheckInboxJobResult(error="Gmail uygulama şifresi kayıtlı değil")
    monkeypatch.setattr(
        "src.ui.replies_tab.check_inbox_job",
        lambda engine, profile, *, on_progress, token: fake_result,
    )
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    tab._on_check_now()
    _run_worker(qtbot, tab)

    assert len(warnings) == 1
    assert "şifresi" in warnings[0][2]


def test_check_now_connection_error_shows_critical(qtbot, engine, monkeypatch):
    tab = _make_tab(qtbot, engine, EXAMPLE_PROFILE_PATH)

    fake_result = CheckInboxJobResult(connection_error="bağlantı zaman aşımına uğradı")
    monkeypatch.setattr(
        "src.ui.replies_tab.check_inbox_job",
        lambda engine, profile, *, on_progress, token: fake_result,
    )
    criticals = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: criticals.append(a) or QMessageBox.Ok)

    tab._on_check_now()
    _run_worker(qtbot, tab)

    assert len(criticals) == 1


def test_check_now_missing_profile_shows_critical_without_starting_worker(qtbot, engine, monkeypatch):
    tab = _make_tab(qtbot, engine, Path("olmayan_profil.json"))

    criticals = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: criticals.append(a) or QMessageBox.Ok)

    tab._on_check_now()

    assert len(criticals) == 1
    assert tab._worker is None


def test_check_now_guard_running_blocks_second_click(qtbot, engine, monkeypatch):
    tab = _make_tab(qtbot, engine, EXAMPLE_PROFILE_PATH)

    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a) or QMessageBox.Ok)
    monkeypatch.setattr(
        "src.ui.replies_tab.check_inbox_job",
        lambda engine, profile, *, on_progress, token: CheckInboxJobResult(
            result=InboxCheckResult()
        ),
    )

    tab._on_check_now()
    tab._on_check_now()  # hâlâ sürüyor -- "İş sürüyor" ile reddedilmeli
    _run_worker(qtbot, tab)

    # "İş sürüyor" uyarısı (ikinci tıklama) + sonuç kutusu (iş bitince): toplam 2.
    assert len(infos) == 2


# ---------------------------------------------------------------------------
# Periyodik otomatik kontrol (Adım 7.7) -- `_on_auto_tick` DOĞRUDAN çağrılır, gerçek `QTimer`
# hiç beklenmez; "şu an" `FakeClock` ile enjekte edilir, gerçek saate bağlı DEĞİLDİR.
# ---------------------------------------------------------------------------


def test_auto_tick_starts_job_silently_and_reloads_without_dialog(qtbot, engine, session, monkeypatch):
    set_setting(session, KEY_INBOX_CHECK_MINUTES, 30)
    session.commit()
    clock = FakeClock(datetime(2026, 9, 28, 10, 0))
    tab = _make_tab(qtbot, engine, EXAMPLE_PROFILE_PATH, clock=clock)

    calls = []
    monkeypatch.setattr(
        "src.ui.replies_tab.check_inbox_job",
        lambda engine, profile, *, on_progress, token: calls.append(1)
        or CheckInboxJobResult(result=InboxCheckResult(headers_fetched=3, new_replies=1)),
    )
    boxes = []
    for method in ("information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, method, lambda *a, **k: boxes.append(a) or QMessageBox.Ok
        )

    tab._on_auto_tick()
    qtbot.waitUntil(lambda: tab._worker is None, timeout=2000)

    assert calls == [1]
    assert boxes == []  # otomatik tur hiçbir diyalog GÖSTERMEZ


def test_auto_tick_disabled_when_interval_is_zero(qtbot, engine, session, monkeypatch):
    set_setting(session, KEY_INBOX_CHECK_MINUTES, 0)
    session.commit()
    clock = FakeClock(datetime(2026, 9, 28, 10, 0))
    tab = _make_tab(qtbot, engine, EXAMPLE_PROFILE_PATH, clock=clock)

    calls = []
    monkeypatch.setattr(
        "src.ui.replies_tab.check_inbox_job",
        lambda engine, profile, *, on_progress, token: calls.append(1)
        or CheckInboxJobResult(result=InboxCheckResult()),
    )

    tab._on_auto_tick()

    assert calls == []
    assert tab._worker is None


def test_auto_tick_second_tick_before_interval_elapsed_does_not_start_another_job(
    qtbot, engine, session, monkeypatch
):
    set_setting(session, KEY_INBOX_CHECK_MINUTES, 30)
    session.commit()
    clock = FakeClock(datetime(2026, 9, 28, 10, 0))
    tab = _make_tab(qtbot, engine, EXAMPLE_PROFILE_PATH, clock=clock)

    calls = []
    monkeypatch.setattr(
        "src.ui.replies_tab.check_inbox_job",
        lambda engine, profile, *, on_progress, token: calls.append(1)
        or CheckInboxJobResult(result=InboxCheckResult()),
    )

    tab._on_auto_tick()
    qtbot.waitUntil(lambda: tab._worker is None, timeout=2000)

    clock.advance(60)  # yalnızca 1 dakika ilerledi, 30 dakikalık aralık henüz dolmadı
    tab._on_auto_tick()

    assert calls == [1]  # ikinci turda YENİ bir iş BAŞLAMADI


def test_auto_tick_third_tick_after_interval_elapsed_starts_another_job(
    qtbot, engine, session, monkeypatch
):
    set_setting(session, KEY_INBOX_CHECK_MINUTES, 30)
    session.commit()
    clock = FakeClock(datetime(2026, 9, 28, 10, 0))
    tab = _make_tab(qtbot, engine, EXAMPLE_PROFILE_PATH, clock=clock)

    calls = []
    monkeypatch.setattr(
        "src.ui.replies_tab.check_inbox_job",
        lambda engine, profile, *, on_progress, token: calls.append(1)
        or CheckInboxJobResult(result=InboxCheckResult()),
    )

    tab._on_auto_tick()
    qtbot.waitUntil(lambda: tab._worker is None, timeout=2000)

    clock.advance(30 * 60)  # tam 30 dakika ilerledi -- aralık doldu
    tab._on_auto_tick()
    qtbot.waitUntil(lambda: tab._worker is None, timeout=2000)

    assert calls == [1, 1]


def test_auto_tick_skips_while_manual_check_running(qtbot, engine, session, monkeypatch):
    """`QThread.start()`'tan hemen sonra `isRunning()` GÜVENİLİR şekilde `True`dür (Qt
    sözleşmesi) -- gerçek işin arka planda ne zaman bittiği önemli değil, bu yüzden `_on_auto_
    tick()`'i hemen ardından çağırmak yarış durumu YARATMAZ. Asıl işin GERÇEKTEN çalışıp
    çalışmadığını görmek için `_run_worker` ile bitmesi beklenir, sonra `calls` kontrol edilir.
    """
    set_setting(session, KEY_INBOX_CHECK_MINUTES, 30)
    session.commit()
    clock = FakeClock(datetime(2026, 9, 28, 10, 0))
    tab = _make_tab(qtbot, engine, EXAMPLE_PROFILE_PATH, clock=clock)

    calls = []
    monkeypatch.setattr(
        "src.ui.replies_tab.check_inbox_job",
        lambda engine, profile, *, on_progress, token: calls.append(1)
        or CheckInboxJobResult(result=InboxCheckResult()),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)

    tab._on_check_now()  # elle tetiklenmiş
    assert tab._worker is not None and tab._worker.isRunning()
    tab._on_auto_tick()  # işçi "running" -- otomatik tur hiçbir şey BAŞLATMAMALI
    _run_worker(qtbot, tab)

    assert calls == [1]  # yalnızca elle tetiklenen çağrı GERÇEKTEN çalıştı


def test_auto_tick_missing_profile_skips_silently(qtbot, engine, session, monkeypatch):
    set_setting(session, KEY_INBOX_CHECK_MINUTES, 30)
    session.commit()
    clock = FakeClock(datetime(2026, 9, 28, 10, 0))
    tab = _make_tab(qtbot, engine, Path("olmayan_profil.json"), clock=clock)

    boxes = []
    for method in ("information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, method, lambda *a, **k: boxes.append(a) or QMessageBox.Ok
        )

    tab._on_auto_tick()  # profil okunamıyor, sessizce atlanmalı -- İSTİSNA fırlatmamalı

    assert boxes == []
    assert tab._worker is None


def test_auto_tick_connection_error_does_not_show_dialog(qtbot, engine, session, monkeypatch):
    set_setting(session, KEY_INBOX_CHECK_MINUTES, 30)
    session.commit()
    clock = FakeClock(datetime(2026, 9, 28, 10, 0))
    tab = _make_tab(qtbot, engine, EXAMPLE_PROFILE_PATH, clock=clock)

    monkeypatch.setattr(
        "src.ui.replies_tab.check_inbox_job",
        lambda engine, profile, *, on_progress, token: CheckInboxJobResult(
            connection_error="bağlantı zaman aşımına uğradı"
        ),
    )
    boxes = []
    for method in ("information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, method, lambda *a, **k: boxes.append(a) or QMessageBox.Ok
        )

    tab._on_auto_tick()
    qtbot.waitUntil(lambda: tab._worker is None, timeout=2000)

    assert boxes == []
