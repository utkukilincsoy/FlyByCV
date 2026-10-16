"""src/sender/self_test.py ve src/ui/jobs.py::self_test_job testleri (PLAN.md Adım 10.2l; Qt YOK).

Ağa/SMTP'ye/Playwright'a çıkılmaz: gönderim ve PDF üretimi sahte. Asıl sözleşme: alıcı yalnızca
göndericinin kendi adresi, veritabanına HİÇBİR şey yazılmaz.
"""
import smtplib
from pathlib import Path

import pytest
from sqlmodel import func, select

from src.core import paths
from src.core.models import Application, Company, Setting
from src.core.settings import (
    CV_MODE_ATS,
    CV_MODE_OWN_PDF,
    KEY_CV_MODE,
    KEY_SENDING_PAUSED,
    KEY_TEST_MODE,
    set_setting,
)
from src.cv_builder.profile_editor import write_profile
from src.sender import self_test
from src.sender.smtp_sender import HEADER_INTENDED_TO
from src.ui.jobs import CancelToken, Progress, self_test_job
from tests.conftest import FakeTransport, build_mail_account

PDF_BYTES = b"%PDF-1.4 kendi cv"


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def own_cv(home) -> Path:
    path = paths.own_cv_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PDF_BYTES)
    return path


class FakeRender:
    """`render_pdf` yerine: çağrıları kaydeder, gerçek bir dosya yazar."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.out_path: Path | None = None

    def __call__(self, profile, tailored, out_path):
        self.calls.append((profile, tailored, out_path))
        self.out_path = Path(out_path)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.out_path.write_bytes(b"%PDF-1.4 ats cv")
        return self.out_path


def _row_counts(session) -> tuple[int, int, int]:
    return tuple(
        session.exec(select(func.count()).select_from(model)).one()
        for model in (Company, Application, Setting)
    )


def _send(session, sample_profile, *, transport=None, render=None, account=None):
    return self_test.send_self_test(
        session,
        sample_profile,
        company_name="Örnek Ağ Çözümleri A.Ş.",
        transport=transport or FakeTransport(),
        account=account or build_mail_account(),
        render=render or FakeRender(),
    )


def test_sends_only_to_own_address_with_template_and_own_pdf(session, sample_profile, own_cv):
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)
    transport = FakeTransport()

    result = _send(session, sample_profile, transport=transport)

    assert result.sent is True and result.error is None and result.refused_reason is None
    assert result.recipient == "ben@gmail.com"
    (account, message, recipients), = transport.calls
    assert recipients == ["ben@gmail.com"]  # zarf: yalnızca kendi adresi
    assert message["To"] == "ben@gmail.com"
    assert message["From"].addresses[0].addr_spec == "ben@gmail.com"
    assert message["Subject"].startswith(self_test.SELF_TEST_SUBJECT_PREFIX)
    assert "Örnek Ağ Çözümleri A.Ş." in message["Subject"]  # şablon örnek firma adıyla dolu
    assert message[HEADER_INTENDED_TO] == "ben@gmail.com"  # gelen kutusu okuyucusu "cevap" saymasın
    (attachment,) = list(message.iter_attachments())
    assert attachment.get_content() == PDF_BYTES  # kullanıcının PDF'i AYNEN
    assert attachment.get_filename() == "Test_Kisi_CV.pdf"
    assert result.attachment_name == "Test_Kisi_CV.pdf"


def test_message_body_is_the_current_template(session, sample_profile, own_cv):
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)
    set_setting(session, "mail_template_subject", "Selam {sirket_adi}")
    set_setting(session, "mail_template_body", "Merhaba {sirket_adi}, ben {ad_soyad}.")
    transport = FakeTransport()

    _send(session, sample_profile, transport=transport)

    message = transport.calls[0][1]
    assert message["Subject"] == "[KURULUM TESTİ] Selam Örnek Ağ Çözümleri A.Ş."
    assert message.get_body(("plain",)).get_content().strip() == (
        "Merhaba Örnek Ağ Çözümleri A.Ş., ben Test Kişi."
    )


def test_writes_nothing_to_the_database(session, sample_profile, own_cv):
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)
    session.commit()
    before = _row_counts(session)

    result = _send(session, sample_profile)

    assert result.sent is True
    assert _row_counts(session) == before
    assert not session.new and not session.dirty  # ne eklendi ne değişti (örnek şirket oturuma girmedi)
    assert session.exec(select(func.count()).select_from(Company)).one() == 0
    assert session.exec(select(func.count()).select_from(Application)).one() == 0


def test_ats_mode_renders_main_cv_into_a_temp_folder_that_is_removed(session, sample_profile):
    set_setting(session, KEY_CV_MODE, CV_MODE_ATS)
    transport, render = FakeTransport(), FakeRender()

    result = _send(session, sample_profile, transport=transport, render=render)

    assert result.sent is True
    (profile, tailored, out_path), = render.calls
    assert tailored.matched_tags == []  # ana CV, şirkete göre özelleştirme yok
    assert out_path.name == "Test_Kisi_CV.pdf"  # gerçek başvurudaki ek adıyla aynı
    assert not out_path.parent.exists()  # geçici klasör silindi
    assert not paths.output_dir().exists()  # output/ altına dosya bırakılmadı
    attachment = next(transport.calls[0][1].iter_attachments())
    assert attachment.get_content() == b"%PDF-1.4 ats cv"  # silinmeden ÖNCE okunmuş


def test_works_in_real_send_mode_too_and_does_not_touch_the_test_mode_setting(
    session, sample_profile, own_cv
):
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)
    set_setting(session, KEY_TEST_MODE, False)
    transport = FakeTransport()

    result = _send(session, sample_profile, transport=transport)

    assert result.sent is True
    assert transport.calls[0][2] == ["ben@gmail.com"]  # test modu kapalı olsa da alıcı kendisi


def test_emergency_stop_blocks_sending(session, sample_profile, own_cv):
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)
    set_setting(session, KEY_SENDING_PAUSED, True)
    transport = FakeTransport()

    result = _send(session, sample_profile, transport=transport)

    assert result.sent is False
    assert result.refused_reason == self_test.PAUSED_REASON
    assert transport.calls == []


def test_own_pdf_mode_without_a_pdf_is_refused(session, sample_profile):
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)
    transport = FakeTransport()

    result = _send(session, sample_profile, transport=transport)

    assert result.sent is False
    assert result.refused_reason == self_test.NO_OWN_CV_REASON
    assert transport.calls == []


def test_cv_render_failure_is_reported_not_raised(session, sample_profile):
    set_setting(session, KEY_CV_MODE, CV_MODE_ATS)
    transport = FakeTransport()

    def broken_render(profile, tailored, out_path):
        raise RuntimeError("chromium yok")

    result = _send(session, sample_profile, transport=transport, render=broken_render)

    assert result.sent is False
    assert "CV hazırlanamadı" in result.refused_reason and "chromium yok" in result.refused_reason
    assert transport.calls == []


def test_smtp_error_is_reported_with_the_password_masked(session, sample_profile, own_cv):
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)
    account = build_mail_account(password="gizli-sifre-1234")
    transport = FakeTransport(
        errors=[smtplib.SMTPAuthenticationError(535, b"hatali gizli-sifre-1234 sifre")]
    )

    result = _send(session, sample_profile, transport=transport, account=account)

    assert result.sent is False and result.refused_reason is None
    assert "SMTPAuthenticationError" in result.error
    assert "gizli-sifre-1234" not in result.error


def test_network_error_is_reported(session, sample_profile, own_cv):
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)

    result = _send(session, sample_profile, transport=FakeTransport(errors=[TimeoutError("zaman aşımı")]))

    assert result.sent is False
    assert "zaman aşımı" in result.error


def test_unexpected_transport_error_is_reported_without_details(session, sample_profile, own_cv):
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)

    result = _send(session, sample_profile, transport=FakeTransport(errors=[KeyError("x")]))

    assert result.sent is False
    assert result.error == "beklenmeyen hata (KeyError)"


def test_missing_password_is_refused(session, sample_profile, own_cv, monkeypatch):
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)
    monkeypatch.setattr(self_test, "load_account", lambda identity, connection=None: None)
    transport = FakeTransport()

    result = self_test.send_self_test(
        session, sample_profile, company_name="X", transport=transport, render=FakeRender()
    )

    assert result.sent is False
    assert result.refused_reason == self_test.NO_PASSWORD_REASON
    assert transport.calls == []


# -- self_test_job ---------------------------------------------------------------------------


def _run_job(engine, profile_path, **kwargs):
    progress: list[Progress] = []
    result = self_test_job(
        engine, profile_path, on_progress=progress.append, token=CancelToken(), **kwargs
    )
    return result, progress


def test_job_sends_and_leaves_the_database_untouched(engine, session, sample_profile, own_cv, tmp_path):
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)
    session.commit()
    profile_path = tmp_path / "master_profile.json"
    write_profile(profile_path, sample_profile)
    before = _row_counts(session)
    transport = FakeTransport()

    result, progress = _run_job(
        engine, profile_path, transport=transport, account=build_mail_account(), render=FakeRender()
    )

    assert result.sent is True
    assert result.recipient == "ben@gmail.com"
    assert result.attachment_name == "Test_Kisi_CV.pdf"
    assert len(transport.calls) == 1
    assert progress[-1].current == progress[-1].total
    session.expire_all()
    assert _row_counts(session) == before


def test_job_reports_a_missing_or_broken_profile(engine, tmp_path):
    result, _ = _run_job(engine, tmp_path / "yok.json", transport=FakeTransport())
    assert result.sent is False and "Profil okunamadı" in result.refused_reason

    broken = tmp_path / "bozuk.json"
    broken.write_text("{bozuk", encoding="utf-8")
    result, _ = _run_job(engine, broken, transport=FakeTransport())
    assert result.sent is False and "Profil okunamadı" in result.refused_reason


def test_job_passes_through_refusal_and_error(engine, session, sample_profile, own_cv, tmp_path):
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)
    session.commit()
    profile_path = tmp_path / "master_profile.json"
    write_profile(profile_path, sample_profile)

    failing, _ = _run_job(
        engine,
        profile_path,
        transport=FakeTransport(errors=[smtplib.SMTPServerDisconnected("koptu")]),
        account=build_mail_account(),
    )
    assert failing.sent is False and "koptu" in failing.error

    set_setting(session, KEY_SENDING_PAUSED, True)
    session.commit()
    refused, _ = _run_job(
        engine, profile_path, transport=FakeTransport(), account=build_mail_account()
    )
    assert refused.sent is False and refused.refused_reason == self_test.PAUSED_REASON
