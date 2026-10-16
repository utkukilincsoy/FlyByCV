"""SMTP ile başvuru mailini gönderir (Gmail/Yandex/özel sunucu — Adım 10.7, `account.connection`).

**Test modu** (`core/settings.KEY_TEST_MODE`, varsayılan AÇIK) hiçbir maili gerçek şirkete
göndermez: mail kullanıcının kendi adresine gider, konuya gerçek hedefi gösteren bir önek
eklenir ve `Application` kaydının GÖNDERİM alanlarına HİÇ DOKUNULMAZ (`status` "queued" kalır,
`sent_at`/`message_id`/`error` boş kalır). Gerekçe: test maili kullanıcının kendi adresine
gittiği için Faz 7'deki IMAP okuyucu onu yanlışlıkla "şirketten gelen cevap" sanabilirdi; gerçek
gönderim o zaman tertemiz bir ilk gönderim olarak kalır. **Tek istisna** (Faz 9 — Otopilot):
başarılı bir test gönderiminden sonra `test_sent_at` doldurulur — bu, gönderim alanı DEĞİL,
yalnızca `check_send_allowed()`'ın aynı başvuruyu tekrar tekrar test maili olarak göndermesini
engelleyen kalıcı bir işarettir.

**Son savunma:** `check_send_allowed()` burada da CLAUDE.md kural 4'ü (aynı şirkete iki kez
gönderim yok) ve kural 10'u (rol adresi olmayan adrese otomatik gönderim yok) kontrol eder —
test modu açık olsa bile. Test modu "dürüst bir kuru çalıştırma" olmalı: gerçek gönderimde
engellenecek bir şey test modunda da engellenir.

**Engelli alan adı ve KVKK onayı (Adım 10.8b):** `send_application()` (worker, tek gönderim,
`send_one.py` hepsinin tek geçidi) engelli alan adına (`core/blocklist.py`) test modunda da
GÖNDERMEZ; KVKK/6563 onayı (`core/legal.py`) yoksa GERÇEK gönderim yapmaz (test modu kendi
adresine gittiği için onay istemez). İkisi de `send_one.py`'nin "limiti aş" onayıyla AŞILAMAZ.

**Ctrl+C / çökme koruması:** `KeyboardInterrupt` bir `BaseException`'dır ve `except Exception`
bloğu onu yakalamaz — `transport()` çalışırken basılırsa kayda ne `sent` ne `failed` yazılırdı,
mail gitmiş olabilir ama kayıt "queued" kalıp kural 4'ü ihlal eden bir ikinci gönderime açık
olurdu. Bunun için `send_application()` gerçek modda, `transport()`'tan HEMEN ÖNCE
`STATUS_SENDING` yazıp COMMIT eder (bu modülün TEK commit'i — bkz. fonksiyonun docstring'i).
Yarıda kalan bir kayıt `sending`de kalır, asla yeniden seçilmez/gönderilmez;
`src/sender/queue.mark_sent`/`mark_queued` ile elle kurtarılır.
"""
import logging
import re
import smtplib
import ssl
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Callable, Iterator, Optional

from sqlmodel import Session

from src.company_finder.email_extractor import is_role_address
from src.core.blocklist import is_company_blocked
from src.core.legal import CONSENT_REQUIRED_REASON, has_consent
from src.core.models import (
    Application,
    Company,
    STATUS_FAILED,
    STATUS_REJECTED,
    STATUS_SENDING,
    STATUS_SENT,
    utcnow,
)
from src.core.mail_providers import resolve_mail_connection
from src.core.secrets import get_mail_password
from src.core.settings import KEY_SENDER_EMAIL, KEY_SENDER_NAME, KEY_TEST_MODE, get_setting
from src.cv_builder.application_builder import render_current_mail_text
from src.cv_builder.profile_loader import MasterProfile
from src.cv_builder.renderer import cv_attachment_name
from src.sender.mail_connection import GMAIL_CONNECTION, SECURITY_SSL, MailConnection

logger = logging.getLogger(__name__)

#: Gmail'in varsayılan bağlantı bilgisiyle AYNI (`MailAccount.connection`in varsayılanı da bu) —
#: yalnızca geriye dönük uyumluluk için burada tutuluyor (bkz. `tests/test_smtp_sender.py`).
SMTP_HOST = GMAIL_CONNECTION.smtp_host
SMTP_PORT = GMAIL_CONNECTION.smtp_port
SMTP_TIMEOUT = 30  # saniye; bağlantı ve her soket işlemi için ayrı ayrı geçerli
TEST_SUBJECT_PREFIX = "[TEST → {recipient}] "
#: Test modunda gerçek hedefi taşıyan başlık; `To` her zaman kendi adresimizdir.
HEADER_INTENDED_TO = "X-FlybyCV-Intended-To"
#: Ad değişikliğinden (CV Autopilot -> FlybyCV) önce gönderilen test maillerinin taşıdığı başlık;
#: gelen kutusu okuyucusu ikisini de tanır (eski test kopyaları "cevap" sayılmasın).
LEGACY_HEADER_INTENDED_TO = "X-CV-Autopilot-Intended-To"
MAX_ERROR_LENGTH = 300

#: Gmail'in genişletilmiş durum kodları (RFC 3463, "x.y.z"); mesajın içinde arıyoruz çünkü
#: `smtplib` bunu ayrıştırmıyor, tüm sunucu yanıtını tek bir metin olarak veriyor.
_DAILY_QUOTA_PATTERN = re.compile(r"5\.4\.5")
_RATE_LIMIT_PATTERN = re.compile(r"4\.7\.\d+")

#: `check_send_allowed()`'ın test modunda tekrar gönderimi reddettiğinde döndürdüğü sabit metin.
#: `worker.py` büyük bir test-modu backlog'unda bunu tek tek loglamak yerine turun sonunda TEK
#: bir özet satırına toplamak için Türkçe metni ayrıştırmak yerine bu sabitle karşılaştırır.
ALREADY_TEST_SENT_REASON = (
    "test modunda bu başvuruya daha önce test maili gönderildi, tekrar gönderilmeyecek"
)


@dataclass(frozen=True)
class MailIdentity:
    """Gönderenin görünen kimliği (ad + adres). Şifre İÇERMEZ; `.eml` önizlemesinde de
    kasaya dokunmadan güvenle kullanılabilir."""

    email: str
    name: str


@dataclass(frozen=True)
class MailAccount:
    """Kimlik + şifre + bağlantı bilgisi. `repr=False` şart: aksi halde `logger.debug(account)`
    gibi bir çağrı şifreyi olduğu gibi loglardı (CLAUDE.md kural 6).

    `connection`in varsayılanı Gmail'dir (Adım 10.7b) — sağlayıcı hiç seçilmemişse (var olan
    kullanıcı) ya da `MailAccount(identity=..., password=...)` doğrudan çağrılan bir testse
    davranış AYNEN eskisi gibi kalır."""

    identity: MailIdentity
    password: str = field(repr=False)
    connection: MailConnection = field(default_factory=lambda: GMAIL_CONNECTION)


@dataclass
class SendResult:
    """`send_application()`'ın döndürdüğü sonuç. Fonksiyon hiçbir zaman istisna fırlatmaz,
    her durumda bunu döner."""

    domain: str
    sent: bool = False
    test_mode: bool = False
    envelope_to: Optional[str] = None
    intended_to: Optional[str] = None
    message_id: Optional[str] = None
    status: Optional[str] = None
    #: Gönderim hiç denenmedi (guard reddetti) — kayda dokunulmadı.
    refused_reason: Optional[str] = None
    #: Gönderim denendi ama arıza çıktı.
    error: Optional[str] = None
    #: `classify_smtp_error()` bu arızayı kota/hız/kimlik hatası olarak tanıdıysa Türkçe sebep
    #: (Faz 9.4 — güvenlik freni); `worker.py` bunu görürse art arda 3 hata beklemeden HEMEN
    #: durur. Tanınmayan bir arızada (ör. geçici ağ hatası) `None` kalır, eski davranış sürer.
    smtp_pause_reason: Optional[str] = None


#: (hesap, mesaj, alıcı listesi) alan, ağa gerçekten çıkan fonksiyonun imzası. Testlerde ve
#: `enrich_company(session, company, crawler=..., mx=...)` deseninde olduğu gibi dışarıdan
#: sahte bir fonksiyonla değiştirilir.
Transport = Callable[[MailAccount, EmailMessage, list[str]], None]


@contextmanager
def _open_smtp(connection: MailConnection) -> Iterator[smtplib.SMTP]:
    """Sağlayıcının güvenlik türüne göre bağlanır (Adım 10.7c): SSL ise baştan `SMTP_SSL`,
    STARTTLS ise düz bağlantı açılıp `starttls()` ile yükseltilir. Login/gönderim çağırana ait.
    """
    if connection.smtp_security == SECURITY_SSL:
        with smtplib.SMTP_SSL(
            connection.smtp_host,
            connection.smtp_port,
            timeout=SMTP_TIMEOUT,
            context=ssl.create_default_context(),
        ) as server:
            yield server
    else:
        with smtplib.SMTP(connection.smtp_host, connection.smtp_port, timeout=SMTP_TIMEOUT) as server:
            server.starttls(context=ssl.create_default_context())
            yield server


def smtp_transport(account: MailAccount, message: EmailMessage, recipients: list[str]) -> None:
    """Gerçek SMTP gönderimi. Alıcı AÇIKÇA verilir, `To` başlığından türetilmez.

    Bu ayrım test modunun gerçek güvencesidir: `send_message`'a `to_addrs` verilmezse alıcıyı
    `To`/`Cc`/`Bcc` başlıklarından türetir; test modunda başlık ile zarfın aynı (kendi adresimiz)
    olduğunu garanti etmek için ikisi ayrı ayrı ele alınır.
    """
    with _open_smtp(account.connection) as server:
        server.login(account.identity.email, account.password)
        server.send_message(message, from_addr=account.identity.email, to_addrs=recipients)


def resolve_identity(session: Session, profile: MasterProfile) -> MailIdentity:
    """Gönderenin adı/adresini ayarlardan okur; ayar boşsa profildeki kişisel bilgilere düşer."""
    email = get_setting(session, KEY_SENDER_EMAIL) or profile.personal_info.email
    name = get_setting(session, KEY_SENDER_NAME) or profile.personal_info.name
    return MailIdentity(email=email, name=name)


def load_account(
    identity: MailIdentity, connection: Optional[MailConnection] = None
) -> Optional[MailAccount]:
    """Şifreyi kasadan okur; kayıtlı değilse (veya kasa açılamazsa) None döner.

    `connection` verilmezse Gmail'e düşülür (`MailAccount.connection`in kendi varsayılanıyla
    aynı) — gerçek sağlayıcı seçimi (`src/core/mail_providers.resolve_mail_connection`)
    çağıranın işi, burada YOK: bu modül `core.settings`'in `mail_provider` ayarını okumaz,
    yalnızca kasayı okur.
    """
    password = get_mail_password()
    if not password:
        logger.error(
            "Mail uygulama şifresi kayıtlı değil. Kaydetmek için: "
            "python scripts/set_mail_password.py"
        )
        return None
    return MailAccount(identity=identity, password=password, connection=connection or GMAIL_CONNECTION)


def check_login(account: MailAccount) -> Optional[str]:
    """Yalnızca bağlanıp login olur, MAİL GÖNDERMEZ. Başarılıysa `None`, değilse kısa hata metni.

    `smtp_transport`'un bağlantı + login kısmıyla aynı (`account.connection`e göre STARTTLS ya
    da SSL, Adım 10.7c), `send_message` çağrısı OLMADAN — Ayarlar sekmesindeki "Bağlantıyı test
    et" butonu (Adım 5.3c) kimlik doğrulamayı dener ama hiçbir mail göndermemeli.
    `(smtplib.SMTPException, OSError)`: `send_application`'ın yakaladığı aynı çift -- zaman
    aşımı/DNS hatası/SSL hatası `SMTPException` değil `OSError`'dur.
    """
    try:
        with _open_smtp(account.connection) as server:
            server.login(account.identity.email, account.password)
    except (smtplib.SMTPException, OSError) as error:
        return _short_error(str(error), account.password)
    return None


#: `check_send_allowed()`'ın engelli alan adı için döndürdüğü sebep (Adım 10.8b).
BLOCKED_REASON = "alan adı engelli listesinde (şirket bir daha yazılmamasını istedi ya da elle engellendi)"


def check_send_allowed(
    application: Application,
    company: Company,
    *,
    auto_mode: bool,
    test_mode: bool = False,
    blocked: bool = False,
) -> Optional[str]:
    """Gönderimi engelleyen ilk sebebi Türkçe döner; engel yoksa None.

    Kayda dokunmaz, log basmaz — çağıran hem loglar hem (kayda dokunmadan) sonucu bildirir.
    Sıra önemli: en kesin ve en ucuz kontrol önce.

    `test_mode=True` VE `application.test_sent_at` doluysa da reddeder (Faz 9 — Otopilot):
    test modunda `status`/`sent_at` hiç değişmediği için (bkz. `send_application` docstring'i),
    bu kontrol olmadan aynı başvuru her çalıştırmada tekrar tekrar test maili olarak
    gönderilebilirdi. Gerçek modu (`test_mode=False`, varsayılan) hiç ETKİLEMEZ.

    `blocked=True` (çağıran `core/blocklist.is_company_blocked` ile hesaplar, bu fonksiyon
    veritabanına dokunmaz) HER modda reddeder -- engelli bir şirkete test maili bile gitmez.
    """
    if blocked:
        return BLOCKED_REASON
    if application.status == STATUS_SENT or application.sent_at is not None:
        return "bu şirkete daha önce gönderim yapılmış (CLAUDE.md kural 4)"
    if test_mode and application.test_sent_at is not None:
        return ALREADY_TEST_SENT_REASON
    if application.status == STATUS_SENDING:
        return (
            "bu başvurunun gönderimi yarıda kalmış, durumu belirsiz "
            "(python scripts/queue_cli.py list)"
        )
    if application.status == STATUS_REJECTED:
        return "bu başvuru reddedilmiş"
    if not application.mail_subject or not application.mail_body:
        return "mail metni hazır değil"
    if not company.email:
        return "şirketin e-posta adresi yok"
    if company.mx_valid is False:
        return "alan adının MX kaydı yok, mail geri döner"
    if auto_mode:
        # email_is_role kaydı hiç hesaplanmamış olabilir (eski kayıt) — bayat veriye
        # güvenmemek için burada yeniden hesaplanır, ama kayda YAZILMAZ (o iş enricher'ın).
        is_role = company.email_is_role
        if is_role is None:
            is_role = is_role_address(company.email)
        if not is_role:
            return (
                "adres kişi adı gibi görünüyor, otomatik gönderim yasak (CLAUDE.md kural 10); "
                "yalnızca manuel onayla gönderilebilir"
            )
    if not application.cv_pdf_path or not Path(application.cv_pdf_path).exists():
        return "CV PDF'i bulunamadı"
    return None


def _test_subject(subject: str, intended_to: str) -> str:
    return TEST_SUBJECT_PREFIX.format(recipient=intended_to) + subject


def _envelope(company: Company, identity: MailIdentity, test_mode: bool) -> tuple[str, Optional[str]]:
    """(zarf alıcısı, gerçek hedef) döner. Gerçek modda ikincisi None (test öneki gerekmez)."""
    if test_mode:
        return identity.email, company.email
    return company.email, None


def build_message(
    *,
    identity: MailIdentity,
    subject: str,
    body: str,
    envelope_to: str,
    attachment_path: Optional[Path],
    attachment_name: Optional[str],
    intended_to: Optional[str] = None,
) -> EmailMessage:
    """Gönderilmeye hazır maili kurar. Ağa/kasaya/veritabanına dokunmaz — saf bir fonksiyondur.

    `intended_to` verilirse (test modu) konuya önek eklenir ve gerçek hedef ayrı bir başlıkta
    saklanır; `To` başlığı ve zarf alıcısı HER ZAMAN `envelope_to` ile aynı kalır.
    """
    message = EmailMessage()
    message["Subject"] = _test_subject(subject, intended_to) if intended_to else subject
    message["From"] = Address(display_name=identity.name, addr_spec=identity.email)
    message["To"] = Address(addr_spec=envelope_to)
    message["Date"] = formatdate(localtime=True)
    # domain= verilmezse make_msgid yerel makine adını kullanır (Windows'ta genelde geçersiz
    # bir FQDN üretir); gönderenin alan adı çok daha doğru ve spam puanını düşürür.
    message["Message-ID"] = make_msgid(domain=identity.email.rpartition("@")[2] or None)
    if intended_to:
        message[HEADER_INTENDED_TO] = intended_to

    # cte="quoted-printable": varsayılan base64, Türkçe gövdeyi ham .eml dosyasında okunamaz
    # yapardı; QP'de "Sayın ..." satırları gözle okunabilir kalır.
    message.set_content(body, subtype="plain", charset="utf-8", cte="quoted-printable")

    if attachment_path is not None:
        # set_content'ten SONRA: add_attachment mesajı multipart/mixed'e dönüştürür, ters
        # sırada yapı bozulur. Türkçe harfli bir ad verilirse RFC 2231 ile kodlanır; ASCII ise
        # (bizim ek adımız zaten ASCII) düz filename= kalır — istemci uyumu için daha iyi.
        message.add_attachment(
            attachment_path.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=attachment_name or attachment_path.name,
        )
    return message


def build_message_for_application(
    session: Session,
    application: Application,
    profile: MasterProfile,
    *,
    test_mode: Optional[bool] = None,
) -> EmailMessage:
    """`.eml` önizlemesi için: kasaya/ağa/veritabanına hiç dokunmadan, o anki ayarlara göre
    maili kurar.

    `test_mode` verilmezse ayardan okunur. Şifre gerekmez (gönderim yapılmıyor). Konu/gövde,
    henüz gönderilmemişse (`sent_at` boşsa) `render_current_mail_text` ile GÜNCEL şablondan
    hesaplanır (`send_application`'ın göndereceğiyle aynı önizleme); zaten gönderilmiş bir
    başvuruda kayıtlı (gönderildiği andaki) metin gösterilir — `Application` kaydına YAZILMAZ.
    """
    company = application.company
    identity = resolve_identity(session, profile)
    if test_mode is None:
        test_mode = get_setting(session, KEY_TEST_MODE)
    if application.sent_at is None:
        subject, body = render_current_mail_text(session, company, profile)
    else:
        subject, body = application.mail_subject, application.mail_body
    envelope_to, intended_to = _envelope(company, identity, test_mode)
    attachment_path = Path(application.cv_pdf_path) if application.cv_pdf_path else None
    return build_message(
        identity=identity,
        subject=subject,
        body=body,
        envelope_to=envelope_to,
        attachment_path=attachment_path,
        attachment_name=cv_attachment_name(profile) if attachment_path else None,
        intended_to=intended_to,
    )


def save_eml(message: EmailMessage, path: Path) -> Path:
    """Maili `.eml` olarak diske yazar (Outlook/Thunderbird'de önizlemek için).

    İkili modda yazılır: metin modu (`open(..., "w")`) Windows'ta `\\n`'i `\\r\\n`'e çevirir,
    içerik zaten `\\r\\n` kullandığı için sonuç `\\r\\r\\n` olur ve bazı istemciler başlıkları
    okuyamaz. `policy.SMTP`, `policy.default`'un satır sonu `\\r\\n` olan hali.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(message.as_bytes(policy=policy.SMTP))
    return path


def _short_error(text: str, password: Optional[str] = None) -> str:
    """Hata metnini kısaltır ve olur da içine sızmışsa şifreyi maskeler (kural 6, kalıcı yazım)."""
    if password:
        text = text.replace(password, "***")
    return text[:MAX_ERROR_LENGTH]


def _smtp_code_and_message(error: Exception) -> tuple[Optional[int], str]:
    """SMTP yanıt kodunu ve mesajını çıkarır; kod yoksa (ör. `OSError`) `(None, str(error))`.

    `SMTPRecipientsRefused` özel bir durum: kodu doğrudan taşımaz, `.recipients` bir
    `{alıcı: (kod, mesaj)}` sözlüğüdür — ilk (ve tek, biz her zaman tek alıcıya gönderiyoruz)
    girdisi kullanılır. Diğer `SMTPResponseException` alt sınıfları (`SMTPAuthenticationError`,
    `SMTPSenderRefused`, `SMTPDataError`, ...) hepsi `.smtp_code`/`.smtp_error` taşır.
    """
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        first = next(iter(error.recipients.values()), None)
        if first is None:
            return None, str(error)
        code, message = first
    else:
        code = getattr(error, "smtp_code", None)
        message = getattr(error, "smtp_error", None)
        if code is None:
            return None, str(error)
    if isinstance(message, bytes):
        message = message.decode(errors="replace")
    return code, str(message)


def classify_smtp_error(error: Exception, provider: str = "gmail") -> Optional[str]:
    """Kota/hız/kimlik hatalarını tanır; öyleyse Türkçe bir sebep döner, aksi halde `None`.

    Bu üçü GEÇİCİ bir arıza DEĞİL — yeniden denemekle düzelmez, ısrar hesabı daha kötü duruma
    sokabilir (ör. tekrar tekrar yanlış şifreyle login denemek sağlayıcıyı daha da kızdırır). Bu
    yüzden `worker.py` bunları art arda `MAX_CONSECUTIVE_ERRORS` hata beklemeden İLK seferde
    durdurur; tanınmayan her şey (zaman aşımı, DNS, geçici 4xx) eski art-arda-3 kuralında kalır.

    * **535** — kimlik doğrulama hatası (uygulama şifresi yanlış/iptal edilmiş). RFC 4954'ün
      standart SMTP AUTH kodu, HER sağlayıcıda aynı anlama gelir — `provider`den bağımsız.
    * **550 5.4.5** — günlük gönderim kotası doldu; **421 / 454 4.7.x** — hız sınırı. Bu ikisi
      Gmail'in genişletilmiş durum kodları (Adım 10.7d) — başka bir sağlayıcının kendi kota/hız
      kodları doğrulanmadan buraya eklenmedi (yanlış bir sağlayıcıyı yanlış tanımaktansa
      tanınmayan bırakmak daha güvenli); onlarda bu iki durum eski art-arda-3 kuralına düşer.
    """
    code, message = _smtp_code_and_message(error)
    if code is None:
        return None
    if code == 535:
        return (
            f"SMTP kimlik doğrulama hatası (535) — uygulama şifresi geçersiz veya iptal "
            f"edilmiş olabilir: {message}"
        )
    if provider != "gmail":
        return None
    if code == 550 and _DAILY_QUOTA_PATTERN.search(message):
        return f"Gmail günlük gönderim kotası doldu (550 5.4.5): {message}"
    if code in (421, 454) and _RATE_LIMIT_PATTERN.search(message):
        return f"Gmail hız sınırına takıldı ({code} 4.7.x) — çok hızlı/çok gönderim: {message}"
    return None


def send_application(
    session: Session,
    application: Application,
    profile: MasterProfile,
    *,
    auto_mode: bool,
    transport: Transport = smtp_transport,
    account: Optional[MailAccount] = None,
    now: Optional[datetime] = None,
) -> SendResult:
    """Bir başvuruyu gönderir. Hiçbir istisna dışarı sızmaz, her zaman bir `SendResult` döner.

    `auto_mode`'un varsayılanı YOK: kural 10 kontrolü buna bağlı, her çağıran hangi modda
    olduğunu açıkça belirtmek zorunda. `account` genelde verilmez (kasadan okunur); testlerde
    dışarıdan geçirilebilir. `now` verilirse `sent_at` bundan gelir (varsayılan `utcnow()`) —
    sahte saatli worker testlerinin gerçek modda DB sayımıyla tutarlı olmasının tek temiz yolu.

    `commit` normalde çağırana aittir (yalnızca `flush` edilir) — TEK istisna: gerçek modda,
    `transport()` çağrılmadan hemen önce `STATUS_SENDING` yazılıp COMMIT edilir (bkz. modül
    docstring'i, Ctrl+C koruması). Commit edilmeyen bir write-ahead işareti hiçbir işe
    yaramazdı; test modunda bu işaret hiç yazılmaz (kayda test modunda hiç dokunulmuyor).
    """
    company = application.company
    result = SendResult(domain=company.domain, status=application.status)

    test_mode = get_setting(session, KEY_TEST_MODE)
    reason = check_send_allowed(
        application,
        company,
        auto_mode=auto_mode,
        test_mode=test_mode,
        blocked=is_company_blocked(session, company),
    )
    if reason is None and not test_mode and not has_consent(session):
        reason = CONSENT_REQUIRED_REASON
    if reason is not None:
        logger.info(f"{company.domain}: gönderim yapılmadı — {reason}")
        result.refused_reason = reason
        return result

    if account is None:
        identity = resolve_identity(session, profile)
        account = load_account(identity, connection=resolve_mail_connection(session))
        if account is None:
            result.refused_reason = "Mail uygulama şifresi kayıtlı değil"
            return result

    # Henüz gönderilmemiş her başvuru, gönderim anındaki GÜNCEL mail şablonundan üretilir —
    # `check_send_allowed` zaten `sent_at`/`status == sent` olanları buraya kadar
    # ilerletmiyor, o yüzden burada her zaman yeniden hesaplanıp kayda yazılır.
    subject, body = render_current_mail_text(session, company, profile)
    application.mail_subject = subject
    application.mail_body = body
    session.add(application)
    session.flush()

    envelope_to, intended_to = _envelope(company, account.identity, test_mode)
    attachment_path = Path(application.cv_pdf_path)
    message = build_message(
        identity=account.identity,
        subject=subject,
        body=body,
        envelope_to=envelope_to,
        attachment_path=attachment_path,
        attachment_name=cv_attachment_name(profile),
        intended_to=intended_to,
    )
    result.test_mode = test_mode
    result.envelope_to = envelope_to
    result.intended_to = company.email

    if not test_mode:
        # Write-ahead işareti: bkz. modül docstring'i ("Ctrl+C / çökme koruması"). transport()
        # çağrılmadan ÖNCE COMMIT edilir — commit edilmeyen bir işaret Ctrl+C tarafından geri
        # alınır ve hiçbir işe yaramaz. Bu modülün TEK commit'idir.
        application.status = STATUS_SENDING
        session.add(application)
        session.commit()

    try:
        transport(account, message, [envelope_to])
    except (smtplib.SMTPException, OSError) as error:
        # SMTPException zaten OSError'un altında değil ama zaman aşımı (socket.timeout),
        # DNS hatası (socket.gaierror) ve TLS hatası (ssl.SSLError) hepsi OSError'dur —
        # yalnızca SMTPException yakalamak bunları kaçırırdı.
        # Maskeleme LOGLAMADAN ÖNCE yapılır: şifre olur da hata metnine sızmışsa (kural 6),
        # kayda yazılmadan önce olduğu gibi loga da girmemeli.
        reason = _short_error(f"mail gönderilemedi ({type(error).__name__}): {error}", account.password)
        logger.warning(f"{company.domain}: {reason}")
        result.smtp_pause_reason = classify_smtp_error(error, provider=account.connection.provider)
        return _record_failure(session, application, result, reason, test_mode)
    except Exception as error:  # transport dışarıdan enjekte edilir; son savunma hattı
        logger.exception(f"{company.domain}: gönderim beklenmeyen şekilde başarısız oldu")
        reason = _short_error(f"beklenmeyen hata ({type(error).__name__})", account.password)
        return _record_failure(session, application, result, reason, test_mode)

    message_id = message["Message-ID"]
    result.sent = True
    result.message_id = message_id

    if test_mode:
        # `status`/`sent_at`/`error` HÂLÂ değiştirilmiyor (Faz 6.1 kararı) — yalnızca
        # `test_sent_at` yazılır, bu da yalnızca `check_send_allowed`'ın yukarıdaki test-modu
        # tekrar kontrolünde okunur (Faz 9).
        application.test_sent_at = now if now is not None else utcnow()
        session.add(application)
        session.flush()
        logger.info(
            f"{company.domain}: TEST MODU — mail {account.identity.email} adresine gönderildi "
            f"(gerçek hedef: {company.email}), kayıt değiştirilmedi"
        )
        result.status = application.status
        return result

    application.status = STATUS_SENT
    application.sent_at = now if now is not None else utcnow()
    application.message_id = message_id
    application.error = None
    session.add(application)
    session.flush()
    logger.info(f"{company.domain}: mail gönderildi")  # adres yazılmaz: kişi adresi olabilir (kişisel veri)
    result.status = application.status
    return result


def _record_failure(
    session: Session,
    application: Application,
    result: SendResult,
    reason: str,
    test_mode: bool,
) -> SendResult:
    """Arıza sonucunu doldurur; gerçek modda kayda `failed` yazar, test modunda dokunmaz.

    `reason` çağıran tarafından zaten maskelenmiş/kısaltılmış olmalı (bkz. `_short_error`).
    Test modunda "failed" yazılmaz: hiçbir şirkete gönderim denenmedi, kaydı bozmanın anlamı
    yok — hata `SendResult.error`'da ve logda zaten görünür.
    """
    if not test_mode:
        application.status = STATUS_FAILED
        application.error = reason
        session.add(application)
        session.flush()
    result.status = application.status
    result.error = reason
    return result
