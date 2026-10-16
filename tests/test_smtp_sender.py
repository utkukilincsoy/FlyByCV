import logging
import smtplib
from datetime import datetime
from email import message_from_bytes, policy

import pytest
from sqlmodel import create_engine

from src.cv_builder.renderer import cv_attachment_name
from src.core.db import configure_sqlite_engine, get_session, init_db
from src.core.models import (
    Application,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_REJECTED,
    STATUS_SENDING,
    STATUS_SENT,
    utcnow,
)
from src.core.settings import (
    KEY_MAIL_TEMPLATE_BODY,
    KEY_MAIL_TEMPLATE_SUBJECT,
    KEY_SENDER_EMAIL,
    KEY_SENDER_NAME,
    KEY_TEST_MODE,
    set_setting,
)
from src.sender import smtp_sender
from src.sender.mail_connection import SECURITY_SSL, MailConnection
from src.sender.smtp_sender import (
    MailAccount,
    MailIdentity,
    build_message,
    build_message_for_application,
    check_login,
    classify_smtp_error,
    resolve_identity,
    save_eml,
    send_application,
)
from tests.conftest import build_company, build_sample_profile

SUBJECT = "Staj Başvurusu – Gülşah Çiçek"
BODY = "Sayın İnsan Kaynakları Ekibi,\n\nMerhaba, ben Gülşah Çiçek, öğrenciyim.\n\nSaygılarımla."


class _FakeTransport:
    """Ağa hiç çıkmayan, çağrıları saklayan sahte gönderim fonksiyonu."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, account, message, recipients):
        self.calls.append((account, message, list(recipients)))
        if self.error is not None:
            raise self.error


class _FakeSMTP:
    """`smtplib.SMTP`'nin yerine geçen, hiç ağa çıkmayan sahte sınıf."""

    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls = []
        self.tls_context = None
        _FakeSMTP.instances.append(self)

    def starttls(self, context=None):
        self.tls_context = context
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message, from_addr=None, to_addrs=None):
        self.calls.append(("send_message", from_addr, list(to_addrs)))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeSMTPSSL:
    """`smtplib.SMTP_SSL`nin yerine geçen sahte sınıf -- Adım 10.7c (SSL sağlayıcılar, ör. Yandex).

    `_FakeSMTP`den tek farkı `starttls()` YOK: SSL bağlantı baştan şifrelidir, `_open_smtp`
    onu hiç çağırmaz (bkz. `smtp_sender._open_smtp`)."""

    instances: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.tls_context = context
        self.calls = []
        _FakeSMTPSSL.instances.append(self)

    def starttls(self, context=None):  # pragma: no cover - SSL yolunda hiç çağrılmamalı
        raise AssertionError("SSL bağlantısında starttls() çağrılmamalı")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message, from_addr=None, to_addrs=None):
        self.calls.append(("send_message", from_addr, list(to_addrs)))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def profile():
    return build_sample_profile()


@pytest.fixture
def identity() -> MailIdentity:
    return MailIdentity(email="ben@gmail.com", name="Gülşah Çiçek")


@pytest.fixture
def account(identity) -> MailAccount:
    return MailAccount(identity=identity, password="gizli-sifre-1234")


@pytest.fixture
def pdf_path(tmp_path):
    path = tmp_path / "cv.pdf"
    path.write_bytes(b"%PDF-1.4 test icerik")
    return path


@pytest.fixture
def company(session):
    company = build_company(
        name="Örnek Firma", domain="firma.com", email="kariyer@firma.com", email_is_role=True
    )
    session.add(company)
    session.flush()
    return company


def _pin_mail_template(session) -> None:
    """`send_application`/`build_message_for_application` artık gönderim anında GÜNCEL
    şablondan metin üretiyor (bkz. bu dosyadaki "Güncel mail şablonu" testleri) — burada
    şablonu sabit `SUBJECT`/`BODY` metinlerine (yer tutucusuz, düz metin) kilitleyerek diğer
    testlerin (zarf, kodlama, ek, hata durumları...) o sabit metinlere güvenmeye devam
    etmesini sağlıyoruz.
    """
    set_setting(session, KEY_MAIL_TEMPLATE_SUBJECT, SUBJECT)
    set_setting(session, KEY_MAIL_TEMPLATE_BODY, BODY)


@pytest.fixture
def application(session, company, pdf_path) -> Application:
    _pin_mail_template(session)
    application = Application(
        company_id=company.id,
        status=STATUS_QUEUED,
        mail_subject=SUBJECT,
        mail_body=BODY,
        cv_pdf_path=str(pdf_path),
    )
    session.add(application)
    session.flush()
    return application


def _make_application(session, *, domain, email, pdf_path, email_is_role=None, mx_valid=None):
    _pin_mail_template(session)
    company = build_company(domain=domain, email=email, email_is_role=email_is_role, mx_valid=mx_valid)
    session.add(company)
    session.flush()
    application = Application(
        company_id=company.id,
        status=STATUS_QUEUED,
        mail_subject=SUBJECT,
        mail_body=BODY,
        cv_pdf_path=str(pdf_path),
    )
    session.add(application)
    session.flush()
    return application, company


# --- Test modu ------------------------------------------------------------------------------


def test_test_mode_redirects_envelope_recipient(session, application, company, profile, account):
    """Test modunda zarf alıcısı kendi adresimiz, şirketin adresi hiçbir yerde geçmez."""
    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.sent is True
    assert result.test_mode is True
    envelope_to = transport.calls[0][2]
    assert envelope_to == [account.identity.email]
    assert company.email not in envelope_to


def test_test_mode_subject_shows_real_recipient(session, application, company, profile, account):
    """Konu gerçek hedefi gösteren önekle başlar, gerçek hedef ayrıca bir başlıkta durur."""
    transport = _FakeTransport()
    send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    message = transport.calls[0][1]
    subject = str(message["Subject"])
    assert subject == f"[TEST → {company.email}] {SUBJECT}"
    assert message[smtp_sender.HEADER_INTENDED_TO] == company.email
    assert message["To"].addresses[0].addr_spec == account.identity.email


def test_test_mode_leaves_record_untouched(session, application, profile, account):
    """Test modunda başarı, GÖNDERİM alanlarına dokunmamalı (karar: Faz 6.1)."""
    transport = _FakeTransport()
    send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert application.status == STATUS_QUEUED
    assert application.sent_at is None
    assert application.message_id is None
    assert application.error is None


def test_test_mode_success_sets_test_sent_at(session, application, profile, account):
    """Başarılı test gönderimi `test_sent_at`'i doldurmalı (Faz 9 — tekrar-gönderim işareti)."""
    transport = _FakeTransport()
    send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert application.test_sent_at is not None


def test_test_mode_refuses_second_send_to_same_application(session, application, profile, account):
    """Aynı başvuruya test modunda ikinci kez gönderim denenmemeli (kalıcı işaret, Faz 9)."""
    transport = _FakeTransport()
    first = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert first.sent is True

    second = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert second.sent is False
    assert second.refused_reason is not None
    assert len(transport.calls) == 1  # ikinci çağrıda transport'a hiç çıkılmadı


def test_real_mode_ignores_test_sent_at(session, application, profile, account):
    """`test_sent_at` dolu olsa bile GERÇEK modda gönderim engellenmemeli (ayrık alanlar)."""
    application.test_sent_at = utcnow()
    session.add(application)
    session.flush()
    set_setting(session, KEY_TEST_MODE, False)

    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.sent is True
    assert application.status == STATUS_SENT


def test_test_mode_off_uses_company_address(session, application, company, profile, account):
    """Test modu kapatılınca zarf alıcısı şirketin gerçek adresi olmalı."""
    set_setting(session, KEY_TEST_MODE, False)
    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert transport.calls[0][2] == [company.email]
    assert result.test_mode is False


def test_test_mode_error_leaves_record_untouched(session, application, profile, account):
    """Test modunda arıza çıksa da kayıt 'failed' yapılmamalı."""
    transport = _FakeTransport(error=smtplib.SMTPException("hata"))
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.error is not None
    assert application.status == STATUS_QUEUED
    assert application.error is None


# --- Gerçek gönderim -------------------------------------------------------------------------


def test_real_send_marks_sent(session, application, company, profile, account):
    """Gerçek gönderim başarılıysa status 'sent', sent_at ve message_id dolmalı."""
    set_setting(session, KEY_TEST_MODE, False)
    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.sent is True
    assert application.status == STATUS_SENT
    assert application.sent_at is not None
    assert application.message_id is not None
    assert application.error is None
    assert transport.calls[0][2] == [company.email]


def test_real_send_does_not_log_recipient_address(session, application, company, profile, account, caplog):
    """Alıcı adresi kişi adresi olabilir (kişisel veri): loga yazılmamalı, alan adı yeterli."""
    set_setting(session, KEY_TEST_MODE, False)
    with caplog.at_level(logging.DEBUG):
        result = send_application(
            session, application, profile, auto_mode=True, transport=_FakeTransport(), account=account
        )
    assert result.sent is True
    assert company.email not in caplog.text
    assert company.domain in caplog.text


def test_real_send_regenerates_mail_text_from_current_template(
    session, application, company, profile, account
):
    """Gönderim anında, kayıttaki eski metin değil GÜNCEL şablon kullanılmalı (kural: kodda
    sabit mail metni yok, PLAN.md Adım 10.4 — 'henüz gönderilmemişse güncel şablondan üret')."""
    set_setting(session, KEY_TEST_MODE, False)
    set_setting(session, KEY_MAIL_TEMPLATE_SUBJECT, "Yeni konu: {sirket_adi}")
    set_setting(session, KEY_MAIL_TEMPLATE_BODY, "Yeni gövde, {ad_soyad}.")
    transport = _FakeTransport()

    send_application(session, application, profile, auto_mode=True, transport=transport, account=account)

    sent_message = transport.calls[0][1]
    assert str(sent_message["Subject"]) == f"Yeni konu: {company.name}"
    body_text = sent_message.get_body(preferencelist=("plain",)).get_content()
    assert body_text.strip() == f"Yeni gövde, {profile.personal_info.name}."
    # Kayıt da güncel metni yansıtmalı (gönderilenle aynı olsun, arşiv/gelen kutusu eşleşmesi için).
    assert application.mail_subject == f"Yeni konu: {company.name}"
    assert application.mail_body == f"Yeni gövde, {profile.personal_info.name}."


def test_test_mode_send_also_regenerates_mail_text(session, application, profile, account):
    """Test modunda da güncel şablon kullanılmalı — test modu 'dürüst bir kuru çalıştırma'."""
    set_setting(session, KEY_MAIL_TEMPLATE_SUBJECT, "Test şablonu")
    set_setting(session, KEY_MAIL_TEMPLATE_BODY, "Test gövdesi")
    transport = _FakeTransport()

    send_application(session, application, profile, auto_mode=True, transport=transport, account=account)

    sent_message = transport.calls[0][1]
    assert "Test şablonu" in str(sent_message["Subject"])
    body_text = sent_message.get_body(preferencelist=("plain",)).get_content()
    assert body_text.strip() == "Test gövdesi"
    assert application.mail_subject == "Test şablonu"
    assert application.mail_body == "Test gövdesi"


def test_now_parameter_determines_sent_at(session, application, profile, account):
    """`now=` verilirse sent_at ondan gelmeli, gerçek `utcnow()`'dan değil."""
    set_setting(session, KEY_TEST_MODE, False)
    transport = _FakeTransport()
    fixed_now = datetime(2026, 1, 1, 12, 0, 0)
    send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account,
        now=fixed_now,
    )
    assert application.sent_at == fixed_now


def test_real_send_writes_sending_marker_before_transport_commits(tmp_path, profile, account, pdf_path):
    """`transport()` çağrıldığı anda kayıt DİSKTE 'sending' olmalı.

    Bellek içi (StaticPool) motor tek bağlantıyı paylaştığı için bunu kanıtlayamaz; burada
    gerçek bir dosya veritabanı ve transport'un içinden açılan İKİNCİ, AYRI bir oturum
    kullanılıyor — işaretin gerçekten commit edildiğini gösteren bu.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    configure_sqlite_engine(engine)
    init_db(engine=engine)

    with get_session(engine=engine) as session:
        company = build_company(
            domain="firma-file.com", email="kariyer@firma-file.com", email_is_role=True
        )
        session.add(company)
        session.flush()
        application = Application(
            company_id=company.id, status=STATUS_QUEUED, mail_subject=SUBJECT, mail_body=BODY,
            cv_pdf_path=str(pdf_path),
        )
        session.add(application)
        session.commit()
        application_id = application.id

        set_setting(session, KEY_TEST_MODE, False)
        session.commit()

        observed_statuses = []

        def transport(account, message, recipients):
            with get_session(engine=engine) as other_session:
                observed_statuses.append(other_session.get(Application, application_id).status)

        send_application(
            session, application, profile, auto_mode=True, transport=transport, account=account
        )

    assert observed_statuses == [STATUS_SENDING]


def test_test_mode_does_not_write_sending_marker(session, application, profile, account):
    """Test modunda 'sending' işareti hiç yazılmamalı (kayda test modunda hiç dokunulmuyor)."""
    transport = _FakeTransport()
    send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert application.status == STATUS_QUEUED


def test_real_send_clears_previous_error(session, application, profile, account):
    """Önceki başarısız denemeden kalan hata metni temizlenmeli."""
    set_setting(session, KEY_TEST_MODE, False)
    application.error = "önceki hata"
    session.add(application)
    session.flush()

    transport = _FakeTransport()
    send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert application.error is None


def test_message_id_saved_matches_header(session, application, profile, account):
    """Kaydedilen message_id, gönderilen mesajın Message-ID başlığıyla birebir aynı olmalı."""
    set_setting(session, KEY_TEST_MODE, False)
    transport = _FakeTransport()
    send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    message = transport.calls[0][1]
    assert application.message_id == message["Message-ID"]


def test_message_id_is_unique_per_send(account):
    """Her mail için üretilen Message-ID farklı olmalı."""
    first = build_message(
        identity=account.identity, subject="Konu", body="gövde", envelope_to="a@ornek.com",
        attachment_path=None, attachment_name=None,
    )
    second = build_message(
        identity=account.identity, subject="Konu", body="gövde", envelope_to="a@ornek.com",
        attachment_path=None, attachment_name=None,
    )
    assert first["Message-ID"] != second["Message-ID"]
    assert first["Message-ID"].endswith("gmail.com>")


# --- Türkçe kodlama --------------------------------------------------------------------------


def test_turkish_subject_is_encoded(identity):
    """Konu RFC 2047 ile kodlanmalı; ham Türkçe bayt hiçbir yerde geçmemeli."""
    message = build_message(
        identity=identity, subject=SUBJECT, body="gövde", envelope_to="alici@ornek.com",
        attachment_path=None, attachment_name=None,
    )
    raw = message.as_bytes()
    assert "Çiçek".encode("utf-8") not in raw
    assert b"=?utf-8?" in raw

    parsed = message_from_bytes(raw, policy=policy.default)
    assert str(parsed["Subject"]) == SUBJECT


def test_turkish_sender_name_is_encoded(identity):
    """From görünen adı kodlanır, adres kısmı ASCII ve bozulmadan kalır."""
    message = build_message(
        identity=identity, subject="Konu", body="gövde", envelope_to="alici@ornek.com",
        attachment_path=None, attachment_name=None,
    )
    raw = message.as_bytes()
    parsed = message_from_bytes(raw, policy=policy.default)
    address = parsed["From"].addresses[0]
    assert address.display_name == identity.name
    assert address.addr_spec == identity.email
    assert "Çiçek".encode("utf-8") not in raw


def test_body_is_utf8_and_round_trips(identity):
    """Gövde UTF-8 olmalı ve Türkçe harfler kayıpsız geri okunabilmeli."""
    body = "Sayın İnsan Kaynakları Ekibi, öğrenciyim."
    message = build_message(
        identity=identity, subject="Konu", body=body, envelope_to="alici@ornek.com",
        attachment_path=None, attachment_name=None,
    )
    assert message.get_content_type() == "text/plain"
    assert message.get_content_charset() == "utf-8"
    assert message.get_content().strip() == body


def test_pdf_attachment_is_added(identity, pdf_path):
    """Ek tam olarak diskteki PDF'in kendisi olmalı, doğru içerik türüyle."""
    message = build_message(
        identity=identity, subject="Konu", body="gövde", envelope_to="alici@ornek.com",
        attachment_path=pdf_path, attachment_name="CV.pdf",
    )
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    attachment = attachments[0]
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_filename() == "CV.pdf"
    assert attachment.get_content() == pdf_path.read_bytes()


def test_attachment_filename_rfc2231_when_turkish(identity, pdf_path):
    """Türkçe harfli ek adı RFC 2231 ile kodlanmalı ve geri okununca aynen çıkmalı."""
    message = build_message(
        identity=identity, subject="Konu", body="gövde", envelope_to="alici@ornek.com",
        attachment_path=pdf_path, attachment_name="Gülşah_Çiçek_CV.pdf",
    )
    raw = message.as_bytes()
    assert b"filename*=" in raw
    attachment = next(message.iter_attachments())
    assert attachment.get_filename() == "Gülşah_Çiçek_CV.pdf"


def test_attachment_filename_stays_plain_when_ascii(identity, pdf_path):
    """ASCII ek adı gereksiz yere RFC 2231'e kodlanmamalı (istemci uyumu)."""
    message = build_message(
        identity=identity, subject="Konu", body="gövde", envelope_to="alici@ornek.com",
        attachment_path=pdf_path, attachment_name="Ali_CV.pdf",
    )
    raw = message.as_bytes()
    assert b'filename="Ali_CV.pdf"' in raw
    assert b"filename*=" not in raw


# --- Hatalar ---------------------------------------------------------------------------------


def test_smtp_error_marks_failed(session, application, profile, account):
    """Kimlik doğrulama hatası: status 'failed', hata dolu, istisna dışarı sızmamalı."""
    set_setting(session, KEY_TEST_MODE, False)
    transport = _FakeTransport(error=smtplib.SMTPAuthenticationError(535, b"auth failed"))
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.sent is False
    assert result.error is not None
    assert application.status == STATUS_FAILED
    assert application.error is not None
    assert application.sent_at is None
    assert application.message_id is None


def test_smtp_error_sets_pause_reason_for_auth_failure(session, application, profile, account):
    """535 kimlik hatası `smtp_pause_reason`'ı doldurmalı (Faz 9.4 — worker bunu HEMEN durur)."""
    set_setting(session, KEY_TEST_MODE, False)
    transport = _FakeTransport(error=smtplib.SMTPAuthenticationError(535, b"5.7.8 auth failed"))
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.smtp_pause_reason is not None
    assert "535" in result.smtp_pause_reason


def test_timeout_does_not_set_pause_reason(session, application, profile, account):
    """Geçici bir ağ hatası (zaman aşımı) kota/hız/kimlik hatası SAYILMAMALI."""
    set_setting(session, KEY_TEST_MODE, False)
    transport = _FakeTransport(error=TimeoutError("zaman aşımı"))
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.smtp_pause_reason is None


def test_timeout_marks_failed(session, application, profile, account):
    """Zaman aşımı (OSError altındaki TimeoutError) de yakalanmalı, sadece SMTPException değil."""
    set_setting(session, KEY_TEST_MODE, False)
    transport = _FakeTransport(error=TimeoutError("zaman aşımı"))
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.error is not None
    assert application.status == STATUS_FAILED


def test_unexpected_exception_marks_failed(session, application, profile, account):
    """Transport beklenmeyen bir istisna fırlatsa da uygulama çökmemeli (son savunma)."""
    set_setting(session, KEY_TEST_MODE, False)
    transport = _FakeTransport(error=ValueError("beklenmeyen"))
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.error is not None
    assert application.status == STATUS_FAILED


def test_password_never_logged(session, application, profile, account, caplog):
    """Şifre hiçbir log kaydında ve error alanında geçmemeli (CLAUDE.md kural 6)."""
    set_setting(session, KEY_TEST_MODE, False)
    transport = _FakeTransport(error=smtplib.SMTPException(f"hata: {account.password}"))
    with caplog.at_level(logging.DEBUG):
        result = send_application(
            session, application, profile, auto_mode=True, transport=transport, account=account
        )
    assert account.password not in caplog.text
    assert account.password not in (application.error or "")
    assert account.password not in (result.error or "")


# --- classify_smtp_error (Faz 9.4 güvenlik freni) -----------------------------------------------


def test_classify_smtp_error_recognizes_auth_failure():
    error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    reason = classify_smtp_error(error)
    assert reason is not None
    assert "535" in reason


def test_classify_smtp_error_recognizes_daily_quota():
    error = smtplib.SMTPSenderRefused(
        550, b"5.4.5 Daily user sending quota exceeded", "ben@gmail.com"
    )
    reason = classify_smtp_error(error)
    assert reason is not None
    assert "kota" in reason


def test_classify_smtp_error_recognizes_rate_limit_421():
    error = smtplib.SMTPResponseException(421, b"4.7.0 Try again later, closing connection")
    reason = classify_smtp_error(error)
    assert reason is not None
    assert "hız" in reason


def test_classify_smtp_error_recognizes_rate_limit_454():
    error = smtplib.SMTPResponseException(454, b"4.7.0 Too many login attempts")
    reason = classify_smtp_error(error)
    assert reason is not None
    assert "hız" in reason


def test_classify_smtp_error_ignores_unrelated_550():
    """Düz 550 ama günlük kota DEĞİL (ör. alıcı adresi yok) -- tanınmamalı."""
    error = smtplib.SMTPRecipientsRefused({"alici@firma.com": (550, b"5.1.1 No such user")})
    reason = classify_smtp_error(error)
    assert reason is None


def test_classify_smtp_error_ignores_generic_4xx_without_enhanced_code():
    """421 ama '4.7.x' içermiyor (ör. geçici sistem sorunu) -- hız sınırı SAYILMAMALI."""
    error = smtplib.SMTPResponseException(421, b"4.3.0 Temporary system problem")
    reason = classify_smtp_error(error)
    assert reason is None


def test_classify_smtp_error_returns_none_for_plain_exception():
    assert classify_smtp_error(ValueError("beklenmeyen")) is None


def test_classify_smtp_error_returns_none_for_oserror():
    """Zaman aşımı/DNS hatası gibi `OSError`'ların SMTP kodu yok, GEÇİCİ sayılmalı."""
    assert classify_smtp_error(TimeoutError("zaman aşımı")) is None


def test_classify_smtp_error_masks_nothing_but_reads_recipients_refused_code():
    """`SMTPRecipientsRefused` kodu `.recipients` sözlüğünden okunmalı (düz `.smtp_code` yok)."""
    error = smtplib.SMTPRecipientsRefused({"alici@firma.com": (535, b"5.7.8 auth failed")})
    reason = classify_smtp_error(error)
    assert reason is not None
    assert "535" in reason


# --- check_login (Ayarlar sekmesi "Bağlantıyı test et" -- SADECE login, mail GÖNDERMEZ) --------


def test_check_login_success_does_not_call_send_message(monkeypatch, account):
    _FakeSMTP.instances = []
    monkeypatch.setattr(smtp_sender.smtplib, "SMTP", _FakeSMTP)

    error = check_login(account)

    assert error is None
    instance = _FakeSMTP.instances[0]
    assert instance.calls == ["starttls", ("login", account.identity.email, account.password)]


def test_check_login_auth_failure_returns_short_error(monkeypatch, account):
    class _FailingSMTP(_FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"auth failed")

    monkeypatch.setattr(smtp_sender.smtplib, "SMTP", _FailingSMTP)

    error = check_login(account)

    assert error is not None
    assert "535" in error or "auth failed" in error


def test_check_login_timeout_is_caught_via_oserror(monkeypatch, account):
    """`OSError` da yakalanmalı -- zaman aşımı/DNS hatası `SMTPException` değildir."""

    class _TimeoutSMTP(_FakeSMTP):
        def login(self, user, password):
            raise TimeoutError("zaman aşımı")

    monkeypatch.setattr(smtp_sender.smtplib, "SMTP", _TimeoutSMTP)

    error = check_login(account)

    assert error is not None


def test_check_login_masks_password_in_error(monkeypatch, account):
    class _LeakySMTP(_FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPException(f"hata: {password}")

    monkeypatch.setattr(smtp_sender.smtplib, "SMTP", _LeakySMTP)

    error = check_login(account)

    assert account.password not in error
    assert "***" in error


def test_account_repr_hides_password(account):
    """Dataclass repr'ı şifreyi basmamalı (`field(repr=False)`)."""
    assert account.password not in repr(account)


# --- Guard'lar ---------------------------------------------------------------------------------


def test_refuses_second_send(session, application, profile, account):
    """Zaten 'sent' olan başvuruya ikinci kez gönderim denenmemeli (kural 4)."""
    application.status = STATUS_SENT
    session.add(application)
    session.flush()

    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.refused_reason is not None
    assert transport.calls == []


def test_refuses_when_sent_at_set_but_status_stale(session, application, profile, account):
    """status eski kalmış olsa da sent_at doluysa yine reddedilmeli."""
    application.sent_at = utcnow()
    session.add(application)
    session.flush()

    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.refused_reason is not None
    assert transport.calls == []


def test_refuses_rejected_application(session, application, profile, account):
    """Reddedilmiş bir başvuruya gönderim denenmemeli."""
    application.status = STATUS_REJECTED
    session.add(application)
    session.flush()

    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.refused_reason is not None
    assert transport.calls == []


def test_refuses_sending_application(session, application, profile, account):
    """Gönderimi yarıda kalmış (durumu belirsiz) bir başvuru tekrar denenmemeli."""
    application.status = STATUS_SENDING
    session.add(application)
    session.flush()

    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.refused_reason is not None
    assert "belirsiz" in result.refused_reason
    assert transport.calls == []


def test_refuses_non_role_address_in_auto_mode(session, profile, account, pdf_path):
    """Kişi adresine otomatik modda gönderim yapılmamalı (kural 10)."""
    application, _ = _make_application(
        session, domain="kisi.com", email="ahmet.yilmaz@kisi.com", pdf_path=pdf_path,
        email_is_role=False,
    )
    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.refused_reason is not None
    assert "kural 10" in result.refused_reason
    assert transport.calls == []


def test_allows_non_role_address_in_manual_mode(session, profile, account, pdf_path):
    """Aynı kişi adresi manuel modda (kullanıcı onayladıysa) gönderilebilmeli."""
    application, _ = _make_application(
        session, domain="kisi2.com", email="ahmet.yilmaz@kisi2.com", pdf_path=pdf_path,
        email_is_role=False,
    )
    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=False, transport=transport, account=account
    )
    assert result.sent is True


def test_auto_mode_has_no_default(profile, account):
    """`auto_mode` zorunlu anahtar kelime argümanı: verilmezse TypeError fırlatmalı."""
    with pytest.raises(TypeError):
        send_application(None, None, profile, transport=_FakeTransport(), account=account)


def test_recomputes_role_when_unknown_allows_role_address(session, profile, account, pdf_path):
    """email_is_role hiç hesaplanmamışsa (None), adres yeniden değerlendirilip izin verilmeli."""
    application, _ = _make_application(
        session, domain="bilinmeyen.com", email="kariyer@bilinmeyen.com", pdf_path=pdf_path,
        email_is_role=None,
    )
    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.sent is True


def test_recomputes_role_when_unknown_blocks_person_address(session, profile, account, pdf_path):
    """email_is_role None ama adres kişi adı gibiyse yine engellenmeli."""
    application, _ = _make_application(
        session, domain="bilinmeyen2.com", email="ahmet.yilmaz@bilinmeyen2.com", pdf_path=pdf_path,
        email_is_role=None,
    )
    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.refused_reason is not None


def test_refuses_without_email(session, profile, account, pdf_path):
    """Şirketin e-posta adresi yoksa gönderim denenmemeli, kayıt değişmemeli."""
    application, _ = _make_application(
        session, domain="bosadres.com", email=None, pdf_path=pdf_path,
    )
    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.refused_reason is not None
    assert transport.calls == []
    assert application.status == STATUS_QUEUED


def test_refuses_when_mx_invalid(session, profile, account, pdf_path):
    """MX kaydı geçersiz alan adına gönderim yapılmamalı."""
    application, _ = _make_application(
        session, domain="olumsuz.com", email="kariyer@olumsuz.com", pdf_path=pdf_path,
        email_is_role=True, mx_valid=False,
    )
    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.refused_reason is not None
    assert transport.calls == []


def test_mx_unknown_is_allowed(session, profile, account, pdf_path):
    """MX henüz kontrol edilmemişse (None) bu tek başına engel olmamalı."""
    application, _ = _make_application(
        session, domain="bilinmeyenmx.com", email="kariyer@bilinmeyenmx.com", pdf_path=pdf_path,
        email_is_role=True, mx_valid=None,
    )
    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.sent is True


def test_refuses_when_pdf_missing(session, company, profile, account, tmp_path):
    """CV PDF'i diskte yoksa gönderim denenmemeli."""
    missing = tmp_path / "eksik.pdf"
    application = Application(
        company_id=company.id, status=STATUS_QUEUED, mail_subject=SUBJECT, mail_body=BODY,
        cv_pdf_path=str(missing),
    )
    session.add(application)
    session.flush()

    transport = _FakeTransport()
    result = send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    assert result.refused_reason is not None
    assert transport.calls == []


def test_refuses_without_password(session, application, profile, monkeypatch):
    """Şifre kasada kayıtlı değilse gönderim denenmemeli."""
    monkeypatch.setattr(smtp_sender, "get_mail_password", lambda: None)
    transport = _FakeTransport()
    result = send_application(session, application, profile, auto_mode=True, transport=transport)
    assert result.refused_reason is not None
    assert transport.calls == []


# --- Kimlik ------------------------------------------------------------------------------------


def test_identity_falls_back_to_profile(session, profile):
    """Ayarlar boşken gönderen kimliği profildeki kişisel bilgilerden gelmeli."""
    identity = resolve_identity(session, profile)
    assert identity.email == profile.personal_info.email
    assert identity.name == profile.personal_info.name


def test_identity_setting_overrides_profile(session, profile):
    """Ayarlar doluysa profil yerine onlar kullanılmalı."""
    set_setting(session, KEY_SENDER_EMAIL, "ozel@gmail.com")
    set_setting(session, KEY_SENDER_NAME, "Özel Ad")
    identity = resolve_identity(session, profile)
    assert identity.email == "ozel@gmail.com"
    assert identity.name == "Özel Ad"


# --- Transport (gerçek smtplib arayüzüyle) ------------------------------------------------------


def test_smtp_transport_uses_starttls_login_send_in_order(monkeypatch, account):
    """STARTTLS → login → send_message sırası ve alıcının açıkça verilmesi doğrulanmalı."""
    _FakeSMTP.instances = []
    monkeypatch.setattr(smtp_sender.smtplib, "SMTP", _FakeSMTP)

    message = build_message(
        identity=account.identity, subject="Konu", body="gövde", envelope_to="alici@ornek.com",
        attachment_path=None, attachment_name=None,
    )
    smtp_sender.smtp_transport(account, message, ["alici@ornek.com"])

    instance = _FakeSMTP.instances[0]
    assert instance.host == smtp_sender.SMTP_HOST
    assert instance.port == smtp_sender.SMTP_PORT
    assert instance.timeout == smtp_sender.SMTP_TIMEOUT
    assert instance.calls[0] == "starttls"
    assert instance.calls[1] == ("login", account.identity.email, account.password)
    assert instance.calls[2] == ("send_message", account.identity.email, ["alici@ornek.com"])


def test_smtp_transport_uses_ssl_for_ssl_provider(monkeypatch, identity):
    """SSL güvenlikli bir sağlayıcı (ör. Yandex, 465) `SMTP_SSL` kullanmalı, `starttls()` DEĞİL
    (Adım 10.7c) -- doğru host/port'la çağrıldığı da doğrulanır (Adım 10.7f)."""
    _FakeSMTPSSL.instances = []
    monkeypatch.setattr(smtp_sender.smtplib, "SMTP_SSL", _FakeSMTPSSL)

    yandex_connection = MailConnection(
        provider="yandex",
        smtp_host="smtp.yandex.com",
        smtp_port=465,
        smtp_security=SECURITY_SSL,
        imap_host="imap.yandex.com",
        imap_port=993,
    )
    account = MailAccount(identity=identity, password="gizli", connection=yandex_connection)
    message = build_message(
        identity=account.identity, subject="Konu", body="gövde", envelope_to="alici@ornek.com",
        attachment_path=None, attachment_name=None,
    )
    smtp_sender.smtp_transport(account, message, ["alici@ornek.com"])

    instance = _FakeSMTPSSL.instances[0]
    assert instance.host == "smtp.yandex.com"
    assert instance.port == 465
    assert instance.calls[0] == ("login", account.identity.email, account.password)
    assert instance.calls[1] == ("send_message", account.identity.email, ["alici@ornek.com"])


# --- .eml önizlemesi -----------------------------------------------------------------------------


def test_eml_uses_crlf_and_round_trips(session, application, profile, tmp_path):
    """.eml dosyası \\r\\n satır sonu kullanmalı (\\r\\r\\n olmamalı) ve geri okunabilmeli."""
    message = build_message_for_application(session, application, profile)
    path = save_eml(message, tmp_path / "mail.eml")
    raw = path.read_bytes()

    assert b"\r\n" in raw
    assert b"\r\r\n" not in raw

    parsed = message_from_bytes(raw, policy=policy.default)
    assert str(parsed["Subject"])
    assert list(parsed.iter_attachments())


def test_eml_does_not_touch_database_or_keyring(session, application, profile, tmp_path, monkeypatch):
    """.eml üretimi kasaya (şifreye) hiç dokunmamalı, kaydı değiştirmemeli."""
    calls = []
    monkeypatch.setattr(smtp_sender, "get_mail_password", lambda: calls.append(True))

    build_message_for_application(session, application, profile)

    assert application.status == STATUS_QUEUED
    assert calls == []


def test_eml_preview_uses_current_template_for_unsent_application(session, application, company, profile):
    """Henüz gönderilmemiş başvurunun önizlemesi GÜNCEL şablonu göstermeli, kayda YAZILMAMALI
    (`send_application`'ın göndereceğiyle aynı içerik, ama kasaya/veritabanına dokunmadan)."""
    set_setting(session, KEY_TEST_MODE, False)
    set_setting(session, KEY_MAIL_TEMPLATE_SUBJECT, "Önizleme konusu: {sirket_adi}")
    set_setting(session, KEY_MAIL_TEMPLATE_BODY, "Önizleme gövdesi, {ad_soyad}.")

    message = build_message_for_application(session, application, profile)

    assert str(message["Subject"]) == f"Önizleme konusu: {company.name}"
    body_text = message.get_body(preferencelist=("plain",)).get_content()
    assert body_text.strip() == f"Önizleme gövdesi, {profile.personal_info.name}."
    # Veritabanındaki kayıt DEĞİŞMEMELİ, yalnızca önizleme hesaplandı.
    assert application.mail_subject == SUBJECT
    assert application.mail_body == BODY


def test_eml_preview_keeps_stored_text_for_sent_application(session, application, company, profile):
    """Zaten gönderilmiş bir başvurunun önizlemesi gönderildiği andaki metni göstermeli, güncel
    şablonu DEĞİL — geçmiş bir gönderimin kaydı sonradan değişen şablona göre yeniden yazılmaz."""
    application.status = STATUS_SENT
    application.sent_at = utcnow()
    session.add(application)
    session.flush()
    set_setting(session, KEY_TEST_MODE, False)
    set_setting(session, KEY_MAIL_TEMPLATE_SUBJECT, "Sonradan değişen şablon")

    message = build_message_for_application(session, application, profile)

    assert str(message["Subject"]) == SUBJECT


def test_attachment_name_comes_from_profile_not_from_disk_file(session, profile, account, tmp_path):
    """"Kendi PDF'im" modunda diskteki ad (own_cv.pdf) maile gitmez: ad profilden üretilir."""
    own_pdf = tmp_path / "own_cv.pdf"
    own_pdf.write_bytes(b"%PDF-1.4 kendi cv")
    application, _ = _make_application(
        session, domain="ornek.com", email="info@ornek.com", pdf_path=own_pdf
    )
    transport = _FakeTransport()
    send_application(
        session, application, profile, auto_mode=True, transport=transport, account=account
    )
    (attachment,) = list(transport.calls[0][1].iter_attachments())
    assert attachment.get_filename() == cv_attachment_name(profile)
    assert attachment.get_filename() == "Test_Kisi_CV.pdf"


def _assert_verifying(context) -> None:
    import ssl

    assert context is not None
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_smtp_starttls_verifies_server_certificate(monkeypatch, account):
    """Regresyon: `starttls()` bağlamsız çağrılırsa smtplib sertifikayı doğrulamaz."""
    _FakeSMTP.instances = []
    monkeypatch.setattr(smtp_sender.smtplib, "SMTP", _FakeSMTP)
    message = build_message(
        identity=account.identity, subject="Konu", body="gövde", envelope_to="alici@ornek.com",
        attachment_path=None, attachment_name=None,
    )
    smtp_sender.smtp_transport(account, message, ["alici@ornek.com"])
    _assert_verifying(_FakeSMTP.instances[0].tls_context)


def test_smtp_ssl_verifies_server_certificate(monkeypatch, identity):
    _FakeSMTPSSL.instances = []
    monkeypatch.setattr(smtp_sender.smtplib, "SMTP_SSL", _FakeSMTPSSL)
    connection = MailConnection(
        provider="yandex", smtp_host="smtp.yandex.com", smtp_port=465, smtp_security=SECURITY_SSL,
        imap_host="imap.yandex.com", imap_port=993,
    )
    account = MailAccount(identity=identity, password="gizli", connection=connection)
    message = build_message(
        identity=account.identity, subject="Konu", body="gövde", envelope_to="alici@ornek.com",
        attachment_path=None, attachment_name=None,
    )
    smtp_sender.smtp_transport(account, message, ["alici@ornek.com"])
    _assert_verifying(_FakeSMTPSSL.instances[0].tls_context)
