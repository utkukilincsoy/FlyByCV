"""Kurulum sihirbazının "kendine test maili" gönderimi (PLAN.md Adım 10.2l).

Gerçek gönderici hesabından kullanıcının KENDİ adresine, gerçek bir başvurudakiyle aynı mail
şablonu ve CV ekiyle bir deneme maili yollar. `send_application()`'dan bilerek AYRI bir yoldur,
çünkü o bir `Company` + `Application` kaydı ister ve test modunda bile `test_sent_at` yazar; bu
deneme ise veritabanına HİÇBİR ŞEY YAZMAZ (şirket/başvuru/ayar satırı yok, `commit`/`flush`
yok -- oturum yalnızca ayarları OKUMAK için kullanılır). Örnek şirket yalnızca bellekte, hiç
oturuma eklenmeden mail metnini doldurmak için kurulur.

Güvenceler:
- Alıcı HER ZAMAN gönderenin kendi adresidir (`resolve_identity`); dışarıdan alıcı verilemez.
- Acil durdur (`sending_paused`) açıksa gönderilmez (CLAUDE.md kural 5: hiçbir şekilde aşılamaz).
- Konu `[KURULUM TESTİ]` önekiyle başlar ve mail `X-FlybyCV-Intended-To` başlığını taşır:
  gelen kutusu okuyucusu (`inbox/reply_matcher.is_own_test_mail`) onu asla "şirket cevabı" saymaz.
- Ek: "Kendi PDF'imi ekle" modunda kullanıcının PDF'i aynen; ATS modunda ana CV geçici bir
  klasöre üretilir ve iş bitince silinir (`output/` altına dosya bırakılmaz).
"""
import logging
import smtplib
import tempfile
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Optional

from sqlmodel import Session

from src.core.mail_providers import resolve_mail_connection
from src.core.models import Company
from src.core.settings import CV_MODE_OWN_PDF, KEY_CV_MODE, KEY_SENDING_PAUSED, get_setting
from src.cv_builder.application_builder import render_current_mail_text
from src.cv_builder.own_cv import own_cv_path_if_present
from src.cv_builder.profile_loader import MasterProfile
from src.cv_builder.renderer import cv_attachment_name, render_pdf
from src.cv_builder.tag_matcher import tailor
from src.sender.smtp_sender import (
    HEADER_INTENDED_TO,
    MailAccount,
    Transport,
    _short_error,
    build_message,
    load_account,
    resolve_identity,
    smtp_transport,
)

logger = logging.getLogger(__name__)

SELF_TEST_SUBJECT_PREFIX = "[KURULUM TESTİ] "
#: Yalnızca bellekte kullanılan örnek şirketin alan adı; `.invalid` (RFC 2606) asla gerçek olmaz.
SAMPLE_DOMAIN = "ornek-firma.invalid"

PAUSED_REASON = "Gönderim ACİL DURDUR ile duraklatılmış. Önce acil durdur'u kaldırın."
NO_PASSWORD_REASON = "Mail uygulama şifresi kayıtlı değil (Mail hesabı sayfasında kaydedin)."
NO_OWN_CV_REASON = (
    "CV modu \"Kendi PDF'imi ekle\" ama henüz bir PDF seçilmedi (CV sayfasında seçin)."
)

#: ATS modunda ana CV'yi PDF'e çeviren fonksiyonun imzası (`renderer.render_pdf`); testte sahtelenir.
PdfRenderer = Callable[[MasterProfile, object, Path], Path]


@dataclass
class SelfTestResult:
    """`send_self_test()`'in sonucu; hiçbir zaman istisna fırlatmaz."""

    sent: bool = False
    recipient: Optional[str] = None
    subject: Optional[str] = None
    attachment_name: Optional[str] = None
    #: Gönderim hiç denenmedi (acil durdur, şifre yok, CV yok, CV üretilemedi).
    refused_reason: Optional[str] = None
    #: Gönderim denendi ama arıza çıktı (şifre maskelenmiş, kısaltılmış).
    error: Optional[str] = None


def build_self_test_message(
    session: Session,
    profile: MasterProfile,
    account: MailAccount,
    cv_path: Path,
    company_name: str,
) -> EmailMessage:
    """Deneme mailini kurar (ağa/kasaya dokunmaz, veritabanına yazmaz).

    Konu/gövde, gerçek başvurudaki GÜNCEL şablondan (`render_current_mail_text`) örnek bir şirket
    adıyla üretilir; `To` ve zarf alıcısı gönderenin kendi adresidir.
    """
    sample_company = Company(name=company_name, domain=SAMPLE_DOMAIN, source="self_test")
    subject, body = render_current_mail_text(session, sample_company, profile)
    identity = account.identity
    message = build_message(
        identity=identity,
        subject=SELF_TEST_SUBJECT_PREFIX + subject,
        body=body,
        envelope_to=identity.email,
        attachment_path=cv_path,
        attachment_name=cv_attachment_name(profile),
    )
    # Gelen kutusu okuyucusu bu başlıkla maili "kendi test mailim" sayar (bkz. modül docstring'i).
    message[HEADER_INTENDED_TO] = identity.email
    return message


def _prepare_cv(
    session: Session, profile: MasterProfile, work_dir: Path, render: PdfRenderer
) -> Path:
    """Ekin yolu: kendi PDF'i modunda kullanıcının dosyası, aksi halde ana CV (geçici klasöre).

    Raises:
        FileNotFoundError: kendi PDF'i modunda dosya yok.
    """
    if get_setting(session, KEY_CV_MODE) == CV_MODE_OWN_PDF:
        own_cv = own_cv_path_if_present()
        if own_cv is None:
            raise FileNotFoundError(NO_OWN_CV_REASON)
        return own_cv
    main_cv = tailor(profile, [], enabled=False)
    return render(profile, main_cv, work_dir / cv_attachment_name(profile))


def send_self_test(
    session: Session,
    profile: MasterProfile,
    *,
    company_name: str,
    transport: Transport = smtp_transport,
    account: Optional[MailAccount] = None,
    render: PdfRenderer = render_pdf,
) -> SelfTestResult:
    """Kullanıcıya kendi adresine deneme başvurusu gönderir. Veritabanına YAZMAZ.

    `account` verilmezse şifre kasadan okunur (çağıran bunu arka plan iş parçacığında yapmalı).
    `transport`/`render` testlerde sahtelenir (ağa/Playwright'a çıkmadan).
    """
    result = SelfTestResult()

    if get_setting(session, KEY_SENDING_PAUSED):
        result.refused_reason = PAUSED_REASON
        return result

    if account is None:
        identity = resolve_identity(session, profile)
        account = load_account(identity, connection=resolve_mail_connection(session))
        if account is None:
            result.refused_reason = NO_PASSWORD_REASON
            return result

    with tempfile.TemporaryDirectory(prefix="flybycv-selftest-") as temp_dir:
        try:
            cv_path = _prepare_cv(session, profile, Path(temp_dir), render)
        except FileNotFoundError as error:
            result.refused_reason = str(error)
            return result
        except Exception as error:  # Playwright/şablon hatası: sihirbazı çökertme, söyle
            logger.exception("Test mailinin CV'si hazırlanamadı")
            result.refused_reason = f"CV hazırlanamadı ({type(error).__name__}): {error}"
            return result

        message = build_self_test_message(session, profile, account, cv_path, company_name)
        result.recipient = account.identity.email
        result.subject = str(message["Subject"])
        result.attachment_name = cv_attachment_name(profile)

        try:
            transport(account, message, [account.identity.email])
        except (smtplib.SMTPException, OSError) as error:
            result.error = _short_error(
                f"mail gönderilemedi ({type(error).__name__}): {error}", account.password
            )
            logger.warning(f"Kurulum test maili: {result.error}")
            return result
        except Exception as error:  # transport dışarıdan enjekte edilir; son savunma hattı
            logger.exception("Kurulum test maili beklenmeyen şekilde başarısız oldu")
            result.error = _short_error(f"beklenmeyen hata ({type(error).__name__})", account.password)
            return result

    result.sent = True
    logger.info("Kurulum test maili kullanıcının kendi adresine gönderildi")
    return result
