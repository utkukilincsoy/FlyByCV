import logging
import smtplib
from datetime import datetime

from src.core.db import get_session
from src.core.models import (
    STATUS_APPROVED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_SENDING,
    STATUS_SENT,
    utcnow,
)
from src.core.settings import (
    KEY_DAILY_LIMIT,
    KEY_HOURLY_LIMIT,
    KEY_SEND_MODE,
    KEY_SENDING_PAUSED,
    KEY_TEST_MODE,
    MODE_AUTO,
    MODE_MANUAL,
    get_setting,
    set_setting,
)
from src.core.timeutil import ISTANBUL
from src.sender.worker import (
    EVENT_FAILED,
    EVENT_REFUSED,
    EVENT_RUN_STARTED,
    EVENT_SENT,
    EVENT_STOPPED,
    EVENT_WAITING,
    TEST_MODE_DELAY_SECONDS,
    process_queue,
)
from tests.conftest import FakeClock, FakeSleeper, FakeTransport, build_application_row, build_mail_account

# Sabit tarihler: 2026-09-28 Pazartesi (mesai içi), 2026-09-27 Pazar (hafta sonu).


def ist(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ISTANBUL)


# --- Manuel / otomatik mod --------------------------------------------------------------------


def test_manual_mode_does_not_send_unapproved(session, sample_profile, pdf_path):
    build_application_row(session, domain="firma.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED)
    transport = FakeTransport()
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=transport,
        account=build_mail_account(), sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
    )
    assert transport.calls == []
    assert result.sent_count == 0
    assert "onaylanmış" in result.stop_reason


def test_auto_mode_skips_ineligible_and_sends_eligible(session, sample_profile, pdf_path):
    build_application_row(
        session, domain="person.com", cv_pdf_path=pdf_path,
        company_overrides={"email_is_role": False},
    )
    build_application_row(session, domain="role.com", cv_pdf_path=pdf_path)
    account = build_mail_account()
    transport = FakeTransport()
    result = process_queue(
        session, sample_profile, mode=MODE_AUTO, transport=transport, account=account,
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
    )
    assert result.sent_count == 1
    assert result.sent_domains == ["role.com"]
    assert transport.calls[0][2] == [account.identity.email]  # test modu varsayılan açık
    assert "uygun başvuru kalmadı" in result.stop_reason


def test_approved_person_address_is_sent_via_manual_override(session, sample_profile, pdf_path):
    """`approved` durumundaki kişi adresli bir başvuru manuel onayla gönderilebilmeli."""
    build_application_row(
        session, domain="person.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED,
        company_overrides={"email_is_role": False},
    )
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(),
        account=build_mail_account(), sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
    )
    assert result.sent_count == 1


def test_uses_send_mode_setting_when_mode_is_none(session, sample_profile, pdf_path):
    set_setting(session, KEY_SEND_MODE, MODE_AUTO)
    build_application_row(session, domain="role.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED)
    result = process_queue(
        session, sample_profile, transport=FakeTransport(), account=build_mail_account(),
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
    )
    assert result.mode == MODE_AUTO
    assert result.sent_count == 1


def test_invalid_send_mode_falls_back_to_manual(session, sample_profile, pdf_path):
    build_application_row(session, domain="role.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED)
    result = process_queue(
        session, sample_profile, mode="otomatik", transport=FakeTransport(),
        account=build_mail_account(), sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
    )
    assert result.mode == MODE_MANUAL
    assert result.sent_count == 0  # manuel moda düştü, queued seçilmedi


# --- Reddedilme (guard) --------------------------------------------------------------------------


def test_refused_does_not_sleep_or_count_as_error(session, sample_profile, pdf_path):
    """Guard reddi (örn. e-posta yok) ağa çıkmaz: bekleme yok, hata sayılmaz."""
    build_application_row(
        session, domain="noemail.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED,
        company_overrides={"email": None},
    )
    transport = FakeTransport()
    sleeper = FakeSleeper()
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=transport, account=build_mail_account(),
        sleeper=sleeper, clock=FakeClock(ist(2026, 9, 28, 10, 0)),
    )
    assert result.refused_count == 1
    assert result.sent_count == 0
    assert result.failed_count == 0
    assert sleeper.sleeps == []
    assert transport.calls == []
    assert result.paused_by_worker is False


def test_five_refusals_do_not_trigger_pause(session, sample_profile, pdf_path):
    for index in range(5):
        build_application_row(
            session, domain=f"noemail{index}.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED,
            company_overrides={"email": None},
        )
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(),
        account=build_mail_account(), sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
    )
    assert result.refused_count == 5
    assert result.paused_by_worker is False
    assert get_setting(session, KEY_SENDING_PAUSED) is False


# --- Limit / mesai saati --------------------------------------------------------------------------


def test_stops_when_daily_limit_reached(session, sample_profile, pdf_path):
    set_setting(session, KEY_DAILY_LIMIT, 0)
    build_application_row(session, domain="role.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(),
        account=build_mail_account(), sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
    )
    assert result.sent_count == 0
    assert "günlük" in result.stop_reason


def test_stops_outside_working_hours(session, sample_profile, pdf_path):
    build_application_row(session, domain="role.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(),
        account=build_mail_account(), sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 27, 12, 0)),
    )
    assert result.sent_count == 0
    assert "hafta sonu" in result.stop_reason


def test_hourly_limit_stops_mid_run(session, sample_profile, pdf_path):
    set_setting(session, KEY_HOURLY_LIMIT, 1)
    set_setting(session, KEY_TEST_MODE, False)
    build_application_row(session, domain="role1.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    build_application_row(session, domain="role2.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    clock = FakeClock(ist(2026, 9, 28, 10, 0))
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(),
        account=build_mail_account(), sleeper=FakeSleeper(clock=clock), clock=clock,
    )
    assert result.sent_count == 1
    assert "saatlik" in result.stop_reason


def test_real_mode_daily_limit_enforced_end_to_end_and_wait_not_shortened(session, sample_profile, pdf_path):
    """Uçtan uca doğrulama (hata raporu madde 2): GERÇEK modda günlük limit kesin uygulanır
    (limit 3 iken 4. gönderim olmaz) VE gönderimler arası bekleme test moduna göre
    KISALTILMAZ (60-180 sn arasında kalır, `TEST_MODE_DELAY_SECONDS` kullanılmaz)."""
    set_setting(session, KEY_TEST_MODE, False)
    set_setting(session, KEY_DAILY_LIMIT, 3)
    set_setting(session, KEY_HOURLY_LIMIT, 20)
    for index in range(4):
        build_application_row(session, domain=f"role{index}.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    clock = FakeClock(ist(2026, 9, 28, 10, 0))
    transport = FakeTransport()
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=transport, account=build_mail_account(),
        sleeper=FakeSleeper(clock=clock), clock=clock,
    )
    assert result.sent_count == 3
    assert len(transport.calls) == 3  # 4. başvuruya hiç ağa çıkılmadı
    assert "günlük" in result.stop_reason
    # max_count verilmediği için (Otopilot'un çağırdığı gibi) her gönderimden sonra beklenir --
    # "son gönderim" olduğu ancak BİR SONRAKİ turda (günlük limit) anlaşılır.
    assert len(result.waits) == 3
    assert all(60 <= wait <= 180 for wait in result.waits)


# --- Test modu: tekrarsızlık ve limit ------------------------------------------------------------


def test_test_mode_sends_each_application_once(session, sample_profile, pdf_path):
    app1, _ = build_application_row(session, domain="role1.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    app2, _ = build_application_row(session, domain="role2.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    transport = FakeTransport()
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=transport, account=build_mail_account(),
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)), max_count=10,
    )
    assert result.sent_count == 2
    assert len(transport.calls) == 2
    assert app1.status == STATUS_APPROVED  # test modunda kayda dokunulmadı
    assert app1.sent_at is None
    assert app2.sent_at is None


def test_test_mode_send_counts_toward_daily_limit(session, sample_profile, pdf_path):
    set_setting(session, KEY_DAILY_LIMIT, 2)
    for index in range(5):
        build_application_row(session, domain=f"role{index}.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(), account=build_mail_account(),
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
    )
    assert result.sent_count == 2
    assert "günlük" in result.stop_reason


def test_test_mode_daily_limit_persists_across_process_queue_calls(session, sample_profile, pdf_path):
    """Hata raporu: Otopilot `process_queue()`'yu defalarca çağırır (her hazırlık biriminden
    sonra); günlük limit tek bir çağrıya değil o GÜNE ait olmalı, yoksa her çağrıda sıfırlanıp
    limit fiilen hiç uygulanmaz (3 limitken 40+ mail gitmesinin sebebi buydu)."""
    set_setting(session, KEY_DAILY_LIMIT, 2)
    for index in range(5):
        build_application_row(session, domain=f"role{index}.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    clock = FakeClock(ist(2026, 9, 28, 10, 0))
    transport = FakeTransport()

    first = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=transport, account=build_mail_account(),
        sleeper=FakeSleeper(), clock=clock,
    )
    assert first.sent_count == 2

    second = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=transport, account=build_mail_account(),
        sleeper=FakeSleeper(), clock=clock,
    )
    assert second.sent_count == 0
    assert "günlük" in second.stop_reason
    assert len(transport.calls) == 2  # ikinci çağrı hiç göndermedi


def test_test_mode_repeated_refusal_is_summarized_not_spammed(session, sample_profile, pdf_path, caplog):
    """Hata raporu: zaten test modunda gönderilmiş çok sayıda başvuru tek tek loglanmamalı,
    turun sonunda TEK bir özet satırına toplanmalı."""
    # Günlük/saatlik limitin kendisini değil, YALNIZCA log toplama davranışını test eder --
    # `test_sent_at` bilinçli olarak FakeClock'un simüle ettiği günden ÇOK ÖNCE: aksi halde
    # bu zaten-gönderilmiş kayıtlar `can_send_now`'ın kendi limit sayımına da girip (doğru
    # şekilde) turu daha ilk adımda "saatlik/günlük limit doldu" ile durdururdu.
    old_test_sent_at = datetime(2026, 9, 20, 10, 0)
    for index in range(5):
        build_application_row(
            session, domain=f"role{index}.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED,
            test_sent_at=old_test_sent_at,
        )
    with caplog.at_level(logging.INFO):
        result = process_queue(
            session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(), account=build_mail_account(),
            sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)), max_count=10,
        )
    assert result.sent_count == 0
    assert result.refused_count == 5
    summary_lines = [r.message for r in caplog.records if "zaten gönderildi" in r.message]
    assert summary_lines == ["5 başvuru test modunda zaten gönderildi, atlandı"]


def test_switching_to_real_mode_sends_previously_test_sent_application(session, sample_profile, pdf_path):
    """Test modunda gönderilmiş (`test_sent_at` dolu) bir başvuru GERÇEK moda geçilince
    engellenmemeli — test modu yalnızca kendi tekrarını engeller (CLAUDE.md kural 5)."""
    app, _ = build_application_row(
        session, domain="role.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED, test_sent_at=utcnow(),
    )
    set_setting(session, KEY_TEST_MODE, False)
    transport = FakeTransport()
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=transport, account=build_mail_account(),
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
    )
    assert result.sent_count == 1
    assert len(transport.calls) == 1
    assert app.status == STATUS_SENT
    assert app.sent_at is not None


# --- Gerçek mod: sonuç sınıflandırma --------------------------------------------------------------


def test_real_mode_marks_sent_and_is_not_reselected(session, sample_profile, pdf_path):
    set_setting(session, KEY_TEST_MODE, False)
    app, _ = build_application_row(session, domain="role.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(), account=build_mail_account(),
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)), max_count=5,
    )
    assert result.sent_count == 1
    assert app.status == STATUS_SENT
    assert app.sent_at is not None


def test_transport_failure_marks_failed_and_continues(session, sample_profile, pdf_path):
    set_setting(session, KEY_TEST_MODE, False)
    app1, _ = build_application_row(session, domain="fail.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    app2, _ = build_application_row(session, domain="ok.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    transport = FakeTransport(errors=[smtplib.SMTPException("hata")])
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=transport, account=build_mail_account(),
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)), max_count=5,
    )
    assert result.failed_count == 1
    assert result.sent_count == 1
    assert app1.status == STATUS_FAILED
    assert app2.status == STATUS_SENT


def test_three_consecutive_errors_pause_sending(session, sample_profile, pdf_path):
    set_setting(session, KEY_TEST_MODE, False)
    for index in range(4):
        build_application_row(session, domain=f"fail{index}.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    transport = FakeTransport(errors=[smtplib.SMTPException("x")] * 3)
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=transport, account=build_mail_account(),
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)), max_count=10,
    )
    assert result.failed_count == 3
    assert result.attempted == 3  # 4. başvuruya hiç bakılmadı
    assert result.paused_by_worker is True
    assert get_setting(session, KEY_SENDING_PAUSED) is True


def test_auth_error_pauses_immediately_without_waiting_for_three(session, sample_profile, pdf_path):
    """535 kimlik hatası GEÇİCİ değil (Faz 9.4) -- art arda 3'ü beklemeden İLK denemede durmalı."""
    set_setting(session, KEY_TEST_MODE, False)
    build_application_row(session, domain="fail.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    build_application_row(session, domain="other.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    transport = FakeTransport(errors=[smtplib.SMTPAuthenticationError(535, b"5.7.8 auth failed")])
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=transport, account=build_mail_account(),
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)), max_count=10,
    )
    assert result.failed_count == 1
    assert result.attempted == 1  # "other.com"a hiç bakılmadı
    assert result.paused_by_worker is True
    assert get_setting(session, KEY_SENDING_PAUSED) is True
    assert "535" in result.stop_reason


def test_error_counter_resets_after_success(session, sample_profile, pdf_path):
    """hata, hata, başarı, hata, hata -> art arda en fazla 2, sending_paused tetiklenmemeli."""
    set_setting(session, KEY_TEST_MODE, False)
    for index in range(5):
        build_application_row(session, domain=f"app{index}.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    errors = [smtplib.SMTPException("a"), smtplib.SMTPException("b"), None,
              smtplib.SMTPException("c"), smtplib.SMTPException("d")]
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(errors=errors),
        account=build_mail_account(), sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
        max_count=10,
    )
    assert result.failed_count == 4
    assert result.sent_count == 1
    assert result.paused_by_worker is False


# --- Acil durdur (döngü ortasında) -----------------------------------------------------------------


def test_paused_mid_loop_stops_run(file_engine, sample_profile, pdf_path):
    """`sending_paused`, başka bir bağlantıdan değiştirilse bile bir sonraki gönderimden önce
    görülmeli — gerçek dosya veritabanı ve AYRI bir oturumla test ediliyor."""
    with get_session(engine=file_engine) as session:
        build_application_row(session, domain="role1.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
        build_application_row(session, domain="role2.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
        session.commit()

        clock = FakeClock(ist(2026, 9, 28, 10, 0))

        def pause_after_first_sleep(sleep_count):
            if sleep_count == 1:
                with get_session(engine=file_engine) as other_session:
                    set_setting(other_session, KEY_SENDING_PAUSED, True)
                    other_session.commit()

        sleeper = FakeSleeper(clock=clock, on_sleep=pause_after_first_sleep)
        result = process_queue(
            session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(),
            account=build_mail_account(), sleeper=sleeper, clock=clock, max_count=10,
        )

    assert result.sent_count == 1
    assert "duraklat" in result.stop_reason


# --- Bekleme süreleri -------------------------------------------------------------------------------


def test_no_wait_after_last_send_when_max_count_reached(session, sample_profile, pdf_path):
    build_application_row(session, domain="role1.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    build_application_row(session, domain="role2.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    sleeper = FakeSleeper()
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(), account=build_mail_account(),
        sleeper=sleeper, clock=FakeClock(ist(2026, 9, 28, 10, 0)), max_count=1,
    )
    assert result.sent_count == 1
    assert sleeper.sleeps == []


def test_test_mode_wait_is_short_and_fixed(session, sample_profile, pdf_path):
    build_application_row(session, domain="role1.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    build_application_row(session, domain="role2.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    sleeper = FakeSleeper()
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(), account=build_mail_account(),
        sleeper=sleeper, clock=FakeClock(ist(2026, 9, 28, 10, 0)), max_count=2,
    )
    assert result.sent_count == 2
    assert sleeper.sleeps == [TEST_MODE_DELAY_SECONDS]


def test_real_mode_waits_within_configured_range(session, sample_profile, pdf_path):
    set_setting(session, KEY_TEST_MODE, False)
    build_application_row(session, domain="role1.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    build_application_row(session, domain="role2.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    clock = FakeClock(ist(2026, 9, 28, 10, 0))
    sleeper = FakeSleeper(clock=clock)
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(), account=build_mail_account(),
        sleeper=sleeper, clock=clock, max_count=2,
    )
    assert result.sent_count == 2
    assert len(sleeper.sleeps) == 1
    assert 60 <= sleeper.sleeps[0] <= 180


def test_max_count_counts_only_successful_sends(session, sample_profile, pdf_path):
    set_setting(session, KEY_TEST_MODE, False)
    build_application_row(session, domain="fail.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    build_application_row(session, domain="ok.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    transport = FakeTransport(errors=[smtplib.SMTPException("x")])
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=transport, account=build_mail_account(),
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)), max_count=1,
    )
    assert result.failed_count == 1
    assert result.sent_count == 1


# --- Ctrl+C ------------------------------------------------------------------------------------------


def test_keyboard_interrupt_during_sleep_returns_partial_result(session, sample_profile, pdf_path):
    build_application_row(session, domain="role1.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    build_application_row(session, domain="role2.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)

    def interrupting_sleeper(seconds):
        raise KeyboardInterrupt()

    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(), account=build_mail_account(),
        sleeper=interrupting_sleeper, clock=FakeClock(ist(2026, 9, 28, 10, 0)), max_count=10,
    )
    assert result.sent_count == 1
    assert result.stop_reason == "kullanıcı Ctrl+C ile durdurdu"


def test_keyboard_interrupt_during_transport_leaves_sending_and_is_not_reselected(
    session, sample_profile, pdf_path
):
    """§6: transport() sırasında Ctrl+C -> kayıt 'sending' kalır, bir daha seçilmez."""
    set_setting(session, KEY_TEST_MODE, False)
    app, _ = build_application_row(session, domain="role.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    transport = FakeTransport(errors=[KeyboardInterrupt()])
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=transport, account=build_mail_account(),
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
    )
    assert result.stop_reason == "kullanıcı Ctrl+C ile durdurdu"
    assert result.interrupted_domain == "role.com"
    assert app.status == STATUS_SENDING

    second = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(), account=build_mail_account(),
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 5)),
    )
    assert second.sent_count == 0


# --- Şifre eksik ---------------------------------------------------------------------------------


def test_stops_cleanly_when_password_missing(session, sample_profile, monkeypatch):
    monkeypatch.setattr("src.sender.worker.load_account", lambda identity, connection=None: None)
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(),
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
    )
    assert result.sent_count == 0
    assert "şifre" in result.stop_reason.lower()


# --- on_event / should_stop (5.2b: arayüzün canlı log paneli + "Durdur" butonu) -------------------


def test_on_event_emits_run_started_sent_waiting_and_stopped_in_order(session, sample_profile, pdf_path):
    build_application_row(session, domain="role.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    events = []
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(),
        account=build_mail_account(), sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
        on_event=events.append,
    )
    kinds = [event.kind for event in events]
    assert kinds[0] == EVENT_RUN_STARTED
    assert EVENT_SENT in kinds
    assert kinds[-1] == EVENT_STOPPED

    sent_event = next(event for event in events if event.kind == EVENT_SENT)
    assert sent_event.domain == "role.com"
    assert sent_event.message == "role.com: gönderildi"
    assert result.sent_count == 1


def test_waiting_event_seconds_matches_recorded_wait(session, sample_profile, pdf_path):
    build_application_row(session, domain="role1.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    build_application_row(session, domain="role2.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    events = []
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(),
        account=build_mail_account(), sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
        max_count=2, on_event=events.append,
    )
    waiting_events = [event for event in events if event.kind == EVENT_WAITING]
    assert len(waiting_events) == 1
    assert waiting_events[0].seconds == result.waits[0]


def test_refused_event_message_matches_result_reason(session, sample_profile, pdf_path):
    build_application_row(
        session, domain="noemail.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED,
        company_overrides={"email": None},
    )
    events = []
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(),
        account=build_mail_account(), sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
        on_event=events.append,
    )
    refused_event = next(event for event in events if event.kind == EVENT_REFUSED)
    assert result.refused[0][1] in refused_event.message
    assert refused_event.domain == "noemail.com"


def test_failed_event_message_matches_result_reason(session, sample_profile, pdf_path):
    set_setting(session, KEY_TEST_MODE, False)
    build_application_row(session, domain="fail.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    events = []
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(errors=[smtplib.SMTPException("x")]),
        account=build_mail_account(), sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
        max_count=1, on_event=events.append,
    )
    failed_event = next(event for event in events if event.kind == EVENT_FAILED)
    assert result.failed[0][1] in failed_event.message
    assert failed_event.domain == "fail.com"


def test_should_stop_true_from_start_sends_nothing(session, sample_profile, pdf_path):
    build_application_row(session, domain="role.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    transport = FakeTransport()
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=transport, account=build_mail_account(),
        sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
        should_stop=lambda: True,
    )
    assert transport.calls == []
    assert result.sent_count == 0
    assert result.stop_reason == "kullanıcı gönderimi durdurdu"


def test_should_stop_flipped_during_wait_stops_after_current_send(session, sample_profile, pdf_path):
    """Bekleme sırasında basılan 'Durdur': sürmekte olan gönderim biter, sıradaki BAŞLAMAZ."""
    build_application_row(session, domain="role1.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    build_application_row(session, domain="role2.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)

    stop_flag = {"stop": False}

    def on_sleep(sleep_count):
        stop_flag["stop"] = True  # ilk bekleme sırasında "Durdur"a basıldı

    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(),
        account=build_mail_account(), sleeper=FakeSleeper(on_sleep=on_sleep),
        clock=FakeClock(ist(2026, 9, 28, 10, 0)), max_count=10,
        should_stop=lambda: stop_flag["stop"],
    )
    assert result.sent_count == 1
    assert result.stop_reason == "kullanıcı gönderimi durdurdu"


def test_no_hooks_given_behaves_exactly_as_before(session, sample_profile, pdf_path):
    """`on_event`/`should_stop` verilmezse davranış (ve dönüş) hiç değişmemeli."""
    build_application_row(session, domain="role.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
    result = process_queue(
        session, sample_profile, mode=MODE_MANUAL, transport=FakeTransport(),
        account=build_mail_account(), sleeper=FakeSleeper(), clock=FakeClock(ist(2026, 9, 28, 10, 0)),
    )
    assert result.sent_count == 1
