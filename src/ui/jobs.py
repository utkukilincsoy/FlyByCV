"""Uzun süren işlerin Qt'siz mantığı: içe aktarma, site tarama, başvuru hazırlama.

PySide6 İÇERMEZ; `src/ui/workers.py`'deki ince QThread kabuğu bu fonksiyonları çağırır.
Oturumu (Session) burada, iş fonksiyonunun kendisi açar (bkz. `src/core/db.py`) -- arka plan
iş parçacığı ile GUI iş parçacığı arasında hiçbir Session/ORM nesnesi paylaşılmaz. Bu yüzden
her iş fonksiyonu bir `engine` alır, `Company`/`Application` nesnelerini KENDİ oturumunda id
ile yeniden yükler.
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from src.autopilot.runner import AutopilotEventCallback, AutopilotResult, run_autopilot
from src.company_finder.csv_import import ImportReport, import_companies
from src.company_finder.enricher import enrich_company
from src.company_finder.google_places import check_api_key
from src.company_finder.mx_check import has_mx_record
from src.company_finder.site_crawler import CrawlResult, crawl_company
from src.company_finder.source_selector import GOOGLE_PLACES_API_KEY_NAME
from src.core import paths
from src.core.db import get_session
from src.core.mail_providers import (
    MailProviderPreset,
    load_mail_provider_presets,
    resolve_connection_for_provider,
    resolve_mail_connection,
)
from src.core.models import Application, Company
from src.core.secrets import get_api_key
from src.core.settings import KEY_SENDING_PAUSED, get_setting
from src.core.timeutil import now_istanbul
from src.core.update_check import UpdateInfo, check_for_update, is_valid_repo
from src.core import version as app_version
from src.cv_builder.application_builder import build_application
from src.cv_builder.profile_loader import MasterProfile, load_profile
from src.inbox.imap_reader import ImapConnectionError, check_connection
from src.inbox.service import InboxCheckResult, check_inbox
from src.llm.base import LLMProvider, filter_tags
from src.llm.factory import _build_llm_provider
from src.llm.keyword_provider import KeywordProvider
from src.llm.providers import PROVIDER_NONE, describe_llm_error
from src.cv_builder.renderer import render_pdf
from src.sender.mail_connection import PROVIDER_CUSTOM, SECURITY_STARTTLS
from src.sender.self_test import PdfRenderer, send_self_test
from src.sender.smtp_sender import (
    MailAccount,
    MailIdentity,
    Transport,
    check_login,
    load_account,
    resolve_identity,
    send_application,
    smtp_transport,
)
from src.sender.worker import EventCallback, QueueRunResult, process_queue
from src.ui.settings_data import LLM_TEST_ABOUT, LLM_TEST_COMPANY

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Progress:
    """Bir arka plan işinin ilerleme bildirimi."""

    current: int
    total: int
    message: str  # Türkçe, doğrudan ilerleme çubuğunda gösterilir


ProgressCallback = Callable[[Progress], None]


class Cancelled(Exception):
    """`CancelToken.raise_if_cancelled()` tarafından fırlatılır."""


class CancelToken:
    """İptal bayrağı, `threading.Event` üzerine kurulu.

    `cancel()` GUI iş parçacığından, `is_cancelled`/`wait()` arka plan iş parçacığından
    kullanılır. Bir `bool` yerine `Event` seçildi çünkü `wait(timeout)` iptal edilirse HEMEN
    döner -- `countdown_sleeper()`'ın dayandığı budur: 12 dakikalık bir `time.sleep()` "Durdur"u
    işlevsiz bırakırdı, iş parçacığı uyanana kadar hiçbir iptal görülmezdi.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        """En fazla `timeout` saniye bekler; iptal edilirse HEMEN döner. İptalse `True` döner."""
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise Cancelled()


def cancellable_crawler(
    crawler: Callable[..., CrawlResult], token: CancelToken
) -> Callable[[str], CrawlResult]:
    """`crawl_company`'yi, her sayfadan sonra iptal bayrağına bakan bir sürüme sarar.

    `on_page` içinden fırlatılan hata taramayı durdurur (bkz. `site_crawler._run_crawl`).
    AMA `crawl_company` HER exception'ı yutup `CrawlResult(error=...)` döndürüyor
    (`site_crawler.py:575`), bu yüzden `Cancelled` dışarı SIZMAZ -- çağıran, döndükten sonra
    `token.is_cancelled`'a bakıp gerekirse geri almalıdır (bkz. `enrich_job`).
    """

    def crawl(website: str) -> CrawlResult:
        def on_page(url: str, visit_priority: int, text_length: int) -> None:
            token.raise_if_cancelled()

        return crawler(website, on_page=on_page)

    return crawl


# ---------------------------------------------------------------------------
# E-posta/tanıtım metni tarama (enrich)
# ---------------------------------------------------------------------------


@dataclass
class EnrichJobResult:
    processed: int = 0
    email_added: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (alan adı, Türkçe sebep)
    cancelled: bool = False


def enrich_job(
    engine: Engine,
    company_ids: Sequence[int],
    *,
    on_progress: ProgressCallback,
    token: CancelToken,
    crawler: Callable[..., CrawlResult] = crawl_company,
    mx: Callable[..., Optional[bool]] = has_mx_record,
) -> EnrichJobResult:
    """Verilen şirketleri tek tek tarar (`enrich_company`); şirket başına commit eder.

    İptal edilirse yarım kalan taramanın sonucu ATILIR (`session.rollback()`) -- aksi
    halde `enrich_company`'nin (iptal edilen tarama için) ezdiği boş `found_emails` kalıcı
    olurdu.
    """
    result = EnrichJobResult()
    total = len(company_ids)
    crawl = cancellable_crawler(crawler, token)

    with get_session(engine) as session:
        for index, company_id in enumerate(company_ids, start=1):
            if token.is_cancelled:
                result.cancelled = True
                break

            company = session.get(Company, company_id)
            if company is None:
                continue

            on_progress(Progress(index, total, f"{company.name} taranıyor…"))

            try:
                enrich_result = enrich_company(session, company, crawler=crawl, mx=mx)
            except Exception as error:  # enrich_company normalde yutar; bu son savunma hattı
                logger.exception(f"{company.domain}: tarama beklenmeyen şekilde başarısız oldu")
                session.rollback()
                result.failed.append((company.domain, str(error)))
                continue

            if token.is_cancelled:
                session.rollback()  # yarım kalan tarama ATILIR (found_emails silinmesin)
                result.cancelled = True
                break

            if enrich_result.error:
                result.failed.append((company.domain, enrich_result.error))
            if enrich_result.email_added:
                result.email_added += 1

            session.commit()  # şirket başına commit -- scripts/enrich_companies.py:159 ile aynı
            result.processed += 1

    return result


# ---------------------------------------------------------------------------
# Başvuru hazırlama
# ---------------------------------------------------------------------------


@dataclass
class BuildJobResult:
    prepared: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)  # (alan adı, Türkçe sebep)
    cancelled: bool = False


def build_job(
    engine: Engine,
    company_ids: Sequence[int],
    profile: MasterProfile,
    *,
    on_progress: ProgressCallback,
    token: CancelToken,
    builder: Callable[..., object] = build_application,
    output_dir: Optional[Path] = None,
) -> BuildJobResult:
    """Verilen şirketler için başvuru hazırlar (`build_application`); şirket başına commit eder.

    `build_application`'ın ilerleme kancası yok; iptal ŞİRKETLER ARASINDA kontrol edilir --
    işlenmekte olan şirket bitene kadar geçerli olmaz, ilerleme mesajı bunu belirtir.
    `profile` (`MasterProfile`) değişmez bir pydantic modeli ve burada salt okunur kullanılır;
    bu yüzden iş parçacığına geçirilmesi güvenlidir.

    `output_dir` verilmezse `src.core.paths.output_dir()` kullanılır (çağrı anında okunur).
    """
    resolved_output_dir = output_dir if output_dir is not None else paths.output_dir()
    result = BuildJobResult()
    total = len(company_ids)

    with get_session(engine) as session:
        for index, company_id in enumerate(company_ids, start=1):
            if token.is_cancelled:
                result.cancelled = True
                break

            company = session.get(Company, company_id)
            if company is None:
                continue

            on_progress(
                Progress(
                    index,
                    total,
                    f"{company.name} için başvuru hazırlanıyor "
                    "(iptal, bu şirket bitince geçerli olacak)…",
                )
            )

            try:
                builder(session, company, profile, output_dir=resolved_output_dir)
                session.commit()
            except Exception as error:
                logger.error(f"{company.domain}: başvuru hazırlanamadı ({error!r})")
                session.rollback()
                result.failed.append((company.domain, str(error)))
                continue

            result.prepared += 1

    return result


# ---------------------------------------------------------------------------
# İçe aktarma
# ---------------------------------------------------------------------------


@dataclass
class ImportJobResult:
    report: Optional[ImportReport] = None
    error: Optional[str] = None  # dosya düzeyi hata, Türkçe
    committed: bool = False


def import_job(
    engine: Engine,
    path: Path,
    *,
    dry_run: bool,
    on_progress: ProgressCallback,
    token: CancelToken,
    importer: Callable[..., ImportReport] = import_companies,
) -> ImportJobResult:
    """Bir CSV/Excel dosyasını içe aktarır (`import_companies`).

    Dosya düzeyi hatalar (desteklenmeyen uzantı, okunamayan kodlama) `result.error`'a
    yazılır; satır düzeyi sorunlar raporun kendisinde kalır (`import_companies` zaten böyle
    davranıyor). İçe aktarma hızlı olduğu için dosya ortasında iptal EDİLEMEZ; `token` imza
    birliği için alınır, commit'ten önce bir kez kontrol edilir.
    """
    result = ImportJobResult()
    label = "deneme" if dry_run else "gerçek"
    on_progress(Progress(0, 1, f"{path.name} okunuyor ({label})…"))

    with get_session(engine) as session:
        try:
            report = importer(session, path, dry_run=dry_run)
        except (ValueError, OSError) as error:
            result.error = str(error)
            on_progress(Progress(1, 1, "İçe aktarma başarısız oldu."))
            return result

        result.report = report

        if dry_run or token.is_cancelled:
            on_progress(Progress(1, 1, "Deneme tamamlandı." if dry_run else "İptal edildi."))
            return result

        try:
            session.commit()
            result.committed = True
        except IntegrityError as error:
            session.rollback()
            result.error = (
                "Veritabanına yazılamadı: bir alan adı tekrarı kaydetme anında oluştu, "
                "hiçbir satır kaydedilmedi. Dosyayı kontrol edip tekrar deneyin."
            )
            logger.error(f"İçe aktarma commit hatası: {error}")

    on_progress(Progress(1, 1, "İçe aktarma tamamlandı." if result.committed else "Bitti."))
    return result


# ---------------------------------------------------------------------------
# Tekli gönderim (Onay Kuyruğu sekmesi -- "Tek gönder")
# ---------------------------------------------------------------------------


@dataclass
class SendJobResult:
    domain: str = ""
    sent: bool = False
    test_mode: bool = False
    envelope_to: Optional[str] = None
    intended_to: Optional[str] = None
    refused_reason: Optional[str] = None
    error: Optional[str] = None
    no_account: bool = False  # kasada mail uygulama şifresi kayıtlı değil
    blocked_reason: Optional[str] = None  # işe girerken acil durdur açılmış


def send_one_job(
    engine: Engine,
    application_id: int,
    profile: MasterProfile,
    *,
    auto_mode: bool,
    on_progress: ProgressCallback,
    token: CancelToken,
    transport: Transport = smtp_transport,
    account: Optional[MailAccount] = None,
) -> SendJobResult:
    """`send_application()`'ı arka planda çalıştırır (`queue_tab.py`'nin "Tek gönder" butonu).

    Onay diyalogları (acil durdur/limit/rol adresi/gerçek alıcı) bu işe girmeden ÖNCE, GUI iş
    parçacığında `queue_data.plan_send()` ile sorulur; `auto_mode` o kararın sonucudur. Burada
    `sending_paused` YENİDEN okunur: onay diyalogları dakikalarca açık kalabilir, bu arada acil
    durdur başka bir pencereden açılmış olabilir (`src/sender/worker.py`'ın her turda yaptığının
    tekil hali). `account` verilmezse kasa (`keyring`) işçi iş parçacığında okunur -- arayüz
    dondurulmasın diye.
    """
    result = SendJobResult()

    with get_session(engine) as session:
        application = session.get(Application, application_id)
        if application is None:
            result.error = "Başvuru bulunamadı (silinmiş olabilir)."
            return result

        result.domain = application.company.domain
        on_progress(Progress(0, 1, f"{result.domain}: kontrol ediliyor…"))

        if get_setting(session, KEY_SENDING_PAUSED):
            result.blocked_reason = (
                "Gönderim, onay penceresi açıkken ACİL DURDUR ile duraklatıldı. Hiçbir mail "
                "gönderilmedi."
            )
            return result

        if account is None:
            identity = resolve_identity(session, profile)
            account = load_account(identity, connection=resolve_mail_connection(session))
            if account is None:
                result.no_account = True
                return result

        on_progress(Progress(0, 1, f"{result.domain}: mail gönderiliyor…"))
        send_result = send_application(
            session, application, profile, auto_mode=auto_mode, transport=transport, account=account
        )
        session.commit()

        result.sent = send_result.sent
        result.test_mode = send_result.test_mode
        result.envelope_to = send_result.envelope_to
        result.intended_to = send_result.intended_to
        result.refused_reason = send_result.refused_reason
        result.error = send_result.error

    on_progress(Progress(1, 1, "Gönderim tamamlandı." if result.sent else "Bitti."))
    return result


# ---------------------------------------------------------------------------
# Toplu gönderim (Onay Kuyruğu sekmesi -- "Gönderimi başlat")
# ---------------------------------------------------------------------------

#: Geri sayımın kaç saniyede bir güncelleneceği. `countdown_sleeper`'ın varsayılanı; testler
#: daha küçük bir değer geçerek gerçek zaman beklemeden çok sayıda tik üretebilir.
COUNTDOWN_TICK_SECONDS = 1.0

CountdownCallback = Callable[[float], None]


def countdown_sleeper(
    token: CancelToken, on_countdown: CountdownCallback, *, tick_seconds: float = COUNTDOWN_TICK_SECONDS
) -> Callable[[float], None]:
    """`process_queue(sleeper=...)`'a geçilecek, iptal edilebilir ve geri sayım yayan bir bekleme.

    `time.sleep(seconds)` yerine `token.wait(tick_seconds)` kullanır: iptal edilirse bekleme
    HEMEN biter (uzun beklemenin bitmesi beklenmez). Kayma olmasın diye `time.monotonic()`
    kullanılır -- her tik arasında `on_countdown` çağrılarının kendi süresi kalan zamandan
    düşülmez.
    """

    def sleep(seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            on_countdown(remaining)
            if token.wait(min(tick_seconds, remaining)):
                break  # iptal edildi: uzun beklemeyi bitirmeyi BEKLEMEZ
        on_countdown(0.0)  # geri sayım etiketini temizlemek için

    return sleep


@dataclass
class QueueRunJobResult:
    run: Optional[QueueRunResult] = None
    no_account: bool = False  # kasada mail uygulama şifresi kayıtlı değil
    blocked_reason: Optional[str] = None  # işe başlarken acil durdur açıkmış


def queue_run_job(
    engine: Engine,
    profile: MasterProfile,
    *,
    on_event: EventCallback,
    on_countdown: CountdownCallback,
    token: CancelToken,
    mode: Optional[str] = None,
    max_count: Optional[int] = None,
    transport: Transport = smtp_transport,
    account: Optional[MailAccount] = None,
    clock: Callable[[], datetime] = now_istanbul,
    tick_seconds: float = COUNTDOWN_TICK_SECONDS,
) -> QueueRunJobResult:
    """`process_queue()`'yu arka planda çalıştırır (`queue_tab.py`'nin "Gönderimi başlat" butonu).

    `send_one_job`'un tur karşılığı: kendi oturumunu açar, `sending_paused` açıksa hiç
    başlamaz (`blocked_reason`) -- `scripts/run_sender.py`'nin başlangıç kontrolüyle aynı kural.
    Hesabı kendisi çözer (keyring erişimi işçi iş parçacığında, arayüz dondurulmasın); `None`
    dönerse `no_account=True` -- `process_queue`'nun CLI komutu içeren kendi gerekçesi arayüze
    hiç sızmaz. `should_stop`, `token.is_cancelled`'a bağlanır; `sleeper` ise `countdown_sleeper`
    ile iptal edilebilir ve geri sayım yayan bir sürüme sarılır.
    """
    result = QueueRunJobResult()

    with get_session(engine) as session:
        if get_setting(session, KEY_SENDING_PAUSED):
            result.blocked_reason = (
                "Gönderim ACİL DURDUR ile duraklatılmış. Önce acil durdur'u kaldırın."
            )
            return result

        if account is None:
            identity = resolve_identity(session, profile)
            account = load_account(identity, connection=resolve_mail_connection(session))
            if account is None:
                result.no_account = True
                return result

        result.run = process_queue(
            session,
            profile,
            mode=mode,
            max_count=max_count,
            sleeper=countdown_sleeper(token, on_countdown, tick_seconds=tick_seconds),
            clock=clock,
            transport=transport,
            account=account,
            on_event=on_event,
            should_stop=lambda: token.is_cancelled,
        )

    return result


# ---------------------------------------------------------------------------
# Otopilot sekmesi: "Başlat" (keşif → tara → hazırla → gönder döngüsü)
# ---------------------------------------------------------------------------


@dataclass
class AutopilotRunJobResult:
    run: Optional[AutopilotResult] = None


def autopilot_run_job(
    engine: Engine,
    profile: MasterProfile,
    *,
    on_event: AutopilotEventCallback,
    on_countdown: CountdownCallback,
    token: CancelToken,
    output_dir: Optional[Path] = None,
    transport: Transport = smtp_transport,
    account: Optional[MailAccount] = None,
    clock: Callable[[], datetime] = now_istanbul,
    tick_seconds: float = COUNTDOWN_TICK_SECONDS,
) -> AutopilotRunJobResult:
    """`run_autopilot()`'u arka planda çalıştırır (Otopilot sekmesinin "Başlat" butonu).

    `queue_run_job`'un aksine `sending_paused`/hesap kontrolünü burada TEKRAR ETMEZ --
    `run_autopilot()`'un kendi ön kontrolü (`_run_preflight_check`, PLAN Adım 9.5) bunu zaten
    yapıyor ve sonucu `AutopilotResult.stop_reason`'a Türkçe olarak yazıyor; sekme bunu
    doğrudan, ayrıştırmadan gösterir. `account` `None` verilirse `run_autopilot()` Gmail
    şifresini kasadan kendisi okur (işçi iş parçacığında, arayüz dondurulmasın diye --
    `queue_run_job` ile aynı gerekçe). `should_stop`, `token.is_cancelled`'a bağlanır; `sleeper`
    `countdown_sleeper` ile iptal edilebilir ve geri sayım yayan bir sürüme sarılır (hem
    gönderimler arası "verimli bekleme"de hem saatlik limit beklemesinde kullanılır).
    """
    result = AutopilotRunJobResult()

    with get_session(engine) as session:
        result.run = run_autopilot(
            session,
            profile,
            output_dir=output_dir,
            sleeper=countdown_sleeper(token, on_countdown, tick_seconds=tick_seconds),
            clock=clock,
            transport=transport,
            account=account,
            should_stop=lambda: token.is_cancelled,
            on_event=on_event,
        )

    return result


# ---------------------------------------------------------------------------
# Ayarlar sekmesi: LLM bağlantı testi ("Bağlantıyı test et")
# ---------------------------------------------------------------------------


@dataclass
class LlmTestJobResult:
    provider: str = ""
    tags: list[str] = field(default_factory=list)
    no_key: bool = False  # sağlayıcı "none" değil ama kasada API anahtarı yok
    error: Optional[str] = None  # `describe_llm_error()` çıktısı; anahtarı ASLA içermez


def llm_test_job(
    provider: str,
    model: str,
    *,
    on_progress: ProgressCallback,
    token: CancelToken,
    build: Callable[[str, str, str], LLMProvider] = _build_llm_provider,
) -> LlmTestJobResult:
    """Verilen sağlayıcı+modelle küçük bir `classify_company` çağrısı dener.

    `provider`/`model` Ayarlar sekmesinden EKRANDAKİ (henüz Kaydet'e basılmamış) seçimle gelir
    -- "Bağlantıyı test et" şu an görüneni dener, veritabanındaki son kaydedilen değeri değil.
    API anahtarı her zaman kasadan okunur: "Anahtar kaydet/sil" zaten anında yazıyor, bu yüzden
    anahtar için ayrı bir "kaydedilmemiş hâl" yok.

    `scripts/try_llm.py` ile AYNI ilke: sonuç `SafeProvider` ile SARMALANMAZ. Sarmalansaydı
    401, yanlış model adı gibi gerçek hatalar sessizce yutulup ekranda anahtar kelime sonucu
    görünürdü ve test "başarılı" sanılırdı -- bir bağlantı testinin tek işi bunu ENGELLEMEK.
    `token` yalnızca imza birliği için alınır: tek bir ağ çağrısı ortasında iptal edilemez
    (`import_job`'daki aynı gerekçe).
    """
    result = LlmTestJobResult(provider=provider)

    if provider == PROVIDER_NONE:
        on_progress(Progress(0, 1, "Anahtar kelime sağlayıcısı deneniyor…"))
        tags = KeywordProvider().classify_company(LLM_TEST_COMPANY, LLM_TEST_ABOUT)
        result.tags = filter_tags(tags)
        on_progress(Progress(1, 1, "Bitti."))
        return result

    api_key = get_api_key(provider)
    if not api_key:
        result.no_key = True
        return result

    on_progress(Progress(0, 1, f"{provider}: bağlantı deneniyor…"))
    try:
        llm_provider: LLMProvider = build(provider, model, api_key)
        tags = llm_provider.classify_company(LLM_TEST_COMPANY, LLM_TEST_ABOUT)
    except Exception as error:
        result.error = describe_llm_error(error)
        return result

    result.tags = filter_tags(tags)
    on_progress(Progress(1, 1, "Bitti."))
    return result


# ---------------------------------------------------------------------------
# Ayarlar sekmesi: Google Places anahtar testi ("Anahtarı test et")
# ---------------------------------------------------------------------------


@dataclass
class PlacesKeyTestJobResult:
    no_key: bool = False  # kasada Google Places anahtarı yok
    error: Optional[str] = None  # Türkçe; anahtarı ASLA içermez


def places_key_test_job(
    *,
    on_progress: ProgressCallback,
    token: CancelToken,
    check: Callable[[str], Optional[str]] = check_api_key,
) -> PlacesKeyTestJobResult:
    """Kasadaki Google Places anahtarını tek, ücretsiz bir istekle dener (yalnız `places.id`
    alan maskesi, bkz. `google_places.check_api_key`). Mail GÖNDERMEZ, veritabanına YAZMAZ,
    aylık istek sayacına SAYILMAZ. `token` yalnızca imza birliği için alınır (tek istek
    iptal edilemez, `llm_test_job` ile aynı gerekçe)."""
    result = PlacesKeyTestJobResult()
    api_key = get_api_key(GOOGLE_PLACES_API_KEY_NAME)
    if not api_key:
        result.no_key = True
        return result

    on_progress(Progress(0, 1, "Google Places: anahtar deneniyor (ücretsiz, yalnız kimlik)…"))
    result.error = check(api_key)
    on_progress(Progress(1, 1, "Bitti."))
    return result


# ---------------------------------------------------------------------------
# Yeni sürüm kontrolü (açılışta otomatik + Ayarlar'da "Şimdi kontrol et", Adım 10.9d)
# ---------------------------------------------------------------------------


@dataclass
class UpdateCheckJobResult:
    info: Optional[UpdateInfo] = None  # yeni sürüm varsa; yoksa (ya da bilinemiyorsa) None
    configured: bool = True  # False: yayın deposu henüz tanımlı değil, ağa hiç çıkılmadı


def update_check_job(
    engine: Engine,
    *,
    force: bool,
    on_progress: ProgressCallback,
    token: CancelToken,
    check: Callable[..., Optional[UpdateInfo]] = check_for_update,
    repo: Optional[str] = None,
) -> UpdateCheckJobResult:
    """`check_for_update`'i kendi oturumuyla çalıştırır (arka plan iş parçacığı GUI'nin oturumunu
    paylaşmaz). `force=False` açılıştaki sessiz otomatik kontroldür (kapalı ayar/günlük sınır/atlanan
    sürüm geçerli); `force=True` "Şimdi kontrol et"tir. Hata yutma kuralı `check_for_update`'in
    kendisinde; bu iş hiçbir zaman kullanıcıya hata göstermez. `token` yalnızca imza birliği için
    alınır (tek istek iptal edilemez, `llm_test_job` ile aynı gerekçe)."""
    repo = repo if repo is not None else app_version.GITHUB_REPO  # çağrı anında oku (testler değiştirebilir)
    if not is_valid_repo(repo):
        return UpdateCheckJobResult(configured=False)
    on_progress(Progress(0, 1, "Yeni sürüm aranıyor…"))
    with get_session(engine) as session:
        info = check(session, force=force, repo=repo)
    on_progress(Progress(1, 1, "Bitti."))
    return UpdateCheckJobResult(info=info)


# ---------------------------------------------------------------------------
# Ayarlar sekmesi: mail bağlantı testi -- SMTP login + IMAP salt okuma, mail GÖNDERMEZ/mail
# İŞARETLEMEZ. İkisi aynı hesabı (aynı uygulama şifresi) kullanır ama BAĞIMSIZ denenir --
# biri başarısız olsa da diğeri denenir, sonuçlar ayrı ayrı raporlanır.
# ---------------------------------------------------------------------------


@dataclass
class SmtpLoginTestJobResult:
    email: str = ""
    ok: bool = False  # SMTP (gönderim) sonucu
    imap_ok: bool = False  # IMAP (gelen kutusu, salt okuma) sonucu
    no_account: bool = False  # kasada mail uygulama şifresi kayıtlı değil
    error: Optional[str] = None  # profil hatası ya da SMTP `check_login()`'in kısa hata metni
    imap_error: Optional[str] = None  # IMAP `check_connection()`'ın kısa/maskeli hata metni


def smtp_login_test_job(
    profile_path: Path,
    *,
    sender_email: Optional[str],
    sender_name: Optional[str],
    on_progress: ProgressCallback,
    token: CancelToken,
    provider: str = "gmail",
    custom_smtp_host: Optional[str] = None,
    custom_smtp_port: Optional[int] = None,
    custom_smtp_security: str = SECURITY_STARTTLS,
    custom_imap_host: Optional[str] = None,
    custom_imap_port: Optional[int] = None,
    presets: Optional[dict[str, MailProviderPreset]] = None,
    connect: Callable[[MailAccount], Optional[str]] = check_login,
    connect_imap: Callable[[MailAccount], Optional[str]] = check_connection,
) -> SmtpLoginTestJobResult:
    """SMTP `check_login()` VE IMAP `check_connection()`'ı arka planda dener; ikisi de sadece
    bağlanıp giriş yapar -- SMTP mail GÖNDERMEZ, IMAP hiçbir mail çekmez/işaretlemez.

    `sender_email`/`sender_name`/`provider`/`custom_*` Ayarlar sekmesinden EKRANDAKİ (henüz
    Kaydet'e basılmamış) değerlerle gelir -- veritabanından DEĞİL (Adım 10.7e); `sender_email`
    boşsa `resolve_identity()`'nin sözleşmesiyle aynı şekilde profile düşülür. `presets`
    normalde verilmez (`load_mail_provider_presets()` işçi iş parçacığında okunur, ağa
    çıkmaz ama disk I/O'dur); testlerde dışarıdan geçirilebilir. Kasa erişimi (`load_account`)
    burada, işçi iş parçacığında yapılır -- arayüz dondurulmasın (`send_one_job`/`queue_run_job`
    ile aynı gerekçe). Kimlik bilgisi (mail adresi + uygulama şifresi) ikisi için AYNI (bkz.
    `src/inbox/imap_reader.py` modül docstring'i), bu yüzden `load_account` bir kez çağrılır;
    hesap yoksa (`no_account`) ikisi de hiç denenmez. `token` yalnızca imza birliği için alınır
    (`llm_test_job` ile aynı gerekçe): tek bir bağlantı denemesi iptal edilemez.
    """
    result = SmtpLoginTestJobResult()

    if sender_email:
        identity = MailIdentity(email=sender_email, name=sender_name or sender_email)
    else:
        try:
            profile = load_profile(profile_path)
        except (FileNotFoundError, ValueError) as error:
            result.error = f"Profil okunamadı: {error}"
            return result
        identity = MailIdentity(
            email=profile.personal_info.email, name=sender_name or profile.personal_info.name
        )

    result.email = identity.email

    connection = resolve_connection_for_provider(
        provider,
        presets if presets is not None else load_mail_provider_presets(),
        custom_smtp_host=custom_smtp_host,
        custom_smtp_port=custom_smtp_port,
        custom_smtp_security=custom_smtp_security,
        custom_imap_host=custom_imap_host,
        custom_imap_port=custom_imap_port,
    )
    account = load_account(identity, connection=connection)
    if account is None:
        result.no_account = True
        return result

    on_progress(Progress(0, 2, f"{identity.email}: SMTP bağlantısı deneniyor…"))
    smtp_error = connect(account)
    if smtp_error is None:
        result.ok = True
    else:
        result.error = smtp_error

    on_progress(Progress(1, 2, f"{identity.email}: IMAP bağlantısı deneniyor (salt okuma)…"))
    imap_error = connect_imap(account)
    if imap_error is None:
        result.imap_ok = True
    else:
        result.imap_error = imap_error

    on_progress(Progress(2, 2, "Bağlantı testleri tamamlandı."))
    return result


# ---------------------------------------------------------------------------
# Kurulum sihirbazı: "kendine test maili" -- gerçek hesaptan kullanıcının KENDİ adresine,
# şablon + CV ekiyle. Veritabanına YAZMAZ (bkz. `src/sender/self_test.py`).
# ---------------------------------------------------------------------------


@dataclass
class SelfTestJobResult:
    sent: bool = False
    recipient: Optional[str] = None
    attachment_name: Optional[str] = None
    refused_reason: Optional[str] = None  # gönderim denenmedi (acil durdur, şifre/CV yok, profil)
    error: Optional[str] = None  # gönderim denendi ama arıza çıktı; şifre maskeli


def self_test_job(
    engine: Engine,
    profile_path: Path,
    *,
    on_progress: ProgressCallback,
    token: CancelToken,
    transport: Transport = smtp_transport,
    account: Optional[MailAccount] = None,
    render: PdfRenderer = render_pdf,
) -> SelfTestJobResult:
    """`send_self_test()`'i arka planda çalıştırır (sihirbazın "Test mailini gönder" düğmesi).

    Oturum yalnızca ayarları okumak içindir: `commit` çağrılmaz, iş sonunda `rollback` edilir, yani
    hiçbir koşulda veritabanına bir şey kalmaz. Profil her seferinde diskten okunur (kullanıcı
    sihirbazda onu az önce değiştirmiş olabilir). `token` yalnızca imza birliği için alınır: tek bir
    gönderim ortasında iptal edilemez (`smtp_login_test_job` ile aynı gerekçe).
    """
    result = SelfTestJobResult()
    try:
        profile = load_profile(profile_path)
    except (FileNotFoundError, ValueError) as error:
        result.refused_reason = f"Profil okunamadı: {error}"
        return result

    on_progress(Progress(0, 1, "Test maili hazırlanıyor ve gönderiliyor…"))
    with get_session(engine) as session:
        try:
            sent = send_self_test(
                session,
                profile,
                company_name=LLM_TEST_COMPANY,
                transport=transport,
                account=account,
                render=render,
            )
        finally:
            session.rollback()

    result.sent = sent.sent
    result.recipient = sent.recipient
    result.attachment_name = sent.attachment_name
    result.refused_reason = sent.refused_reason
    result.error = sent.error
    on_progress(Progress(1, 1, "Bitti."))
    return result


# ---------------------------------------------------------------------------
# Cevaplar sekmesi: "Şimdi kontrol et" -- IMAP'a çıkar (salt okuma), arka planda çalışır
# ---------------------------------------------------------------------------


@dataclass
class CheckInboxJobResult:
    result: Optional[InboxCheckResult] = None
    #: Kurulum eksikliği türü bir durum (Gmail şifresi yok, hiç gönderim yok) -- `check_inbox()`
    #: sözleşmesiyle aynı (bkz. modül docstring'i, `src/inbox/service.py`), istisna DEĞİL.
    error: Optional[str] = None
    #: `error` doluysa ve bu `True` ise sebep "henüz gönderilmiş başvuru yok" -- bu bir HATA
    #: değil, bilgilendirme (bkz. `src/inbox/service.InboxCheckResult.no_applications_sent`).
    no_applications_sent: bool = False
    #: Gerçek bir IMAP bağlantı/protokol hatası (`ImapConnectionError`) -- yukarıdakinden farklı,
    #: bu durumda `result`/`error` hiç doldurulmaz.
    connection_error: Optional[str] = None


def check_inbox_job(
    engine: Engine,
    profile: MasterProfile,
    *,
    on_progress: ProgressCallback,
    token: CancelToken,
    checker: Callable[..., InboxCheckResult] = check_inbox,
) -> CheckInboxJobResult:
    """`check_inbox()`'u arka planda çalıştırır (Cevaplar sekmesi -- "Şimdi kontrol et").

    IMAP'a çıktığı için (CLAUDE.md kural 8) arka planda çalışır; salt okuma (bkz.
    `src/inbox/imap_reader.py` modül docstring'i), hiçbir mail işaretlenmez/silinmez.
    `ImapConnectionError` burada YAKALANIR ki `JobWorker`'ın genel `except Exception` ->
    `failed` sinyaline düşüp "Beklenmeyen hata" göstermesin; kullanıcı zaten kısaltılmış/
    maskelenmiş Türkçe bir hata metni görür (`connection_error`). `token` yalnızca imza
    birliği için alınır: tek bir tarama iptal edilemez (`llm_test_job`/`import_job` ile aynı
    gerekçe).
    """
    result = CheckInboxJobResult()
    on_progress(Progress(0, 1, "Gelen kutusu taranıyor…"))

    with get_session(engine) as session:
        try:
            check_result = checker(session, profile)
        except ImapConnectionError as error:
            result.connection_error = str(error)
            on_progress(Progress(1, 1, "Bağlantı hatası."))
            return result

    result.result = check_result
    result.error = check_result.error
    result.no_applications_sent = check_result.no_applications_sent
    on_progress(Progress(1, 1, "Bitti."))
    return result
