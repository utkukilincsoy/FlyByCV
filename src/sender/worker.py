"""Gönderim kuyruğunu işler: limit/mesai kontrolü → seçim → gönderim → bekleme, tekrar.

`process_queue()` tek giriş noktasıdır ve `Ctrl+C` dahil hiçbir istisna fırlatmaz — her zaman
bir `QueueRunResult` döner. Manuel/otomatik mod farkı `src/sender/queue.py`'de (hangi başvuru
seçilir), limit/mesai/acil durdur `src/sender/rate_limiter.py`'de (ne zaman gönderilebilir),
gerçek gönderim `src/sender/smtp_sender.py`'de (nasıl gönderilir) — bu modül üçünü birleştirir.

**Test modunda limitler de uygulanır** (CLAUDE.md kural 5): `can_send_now(..., test_mode=True)`
günlük/saatlik sayımı `Application.sent_at` yerine `Application.test_sent_at`'ten yapar (bkz.
`rate_limiter.py`) — bu sütun her gönderimden hemen sonra COMMIT edildiği için (aşağıdaki (B)),
sayım YALNIZCA bu çalıştırma içinde değil, aynı gün içindeki ÖNCEKİ `process_queue()` /
Otopilot turlarını da kapsar (bir önceki hatada olduğu gibi her çağrıda sıfırlanmaz). Test
modunda bekleme de kısaltılır (`TEST_MODE_DELAY_SECONDS`).
"""
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from sqlmodel import Session

from src.core.settings import (
    KEY_SEND_MODE,
    KEY_TEST_MODE,
    MODE_AUTO,
    MODE_MANUAL,
    SEND_MODES,
    get_setting,
)
from src.core.mail_providers import resolve_mail_connection
from src.core.text import format_duration_tr
from src.core.timeutil import now_istanbul, to_naive_utc
from src.cv_builder.profile_loader import MasterProfile
from src.sender.queue import guard_auto_mode, next_application
from src.sender.rate_limiter import can_send_now, load_limits, random_delay_seconds
from src.sender.safety_brake import trigger_pause
from src.sender.smtp_sender import (
    ALREADY_TEST_SENT_REASON,
    MailAccount,
    Transport,
    load_account,
    resolve_identity,
    send_application,
    smtp_transport,
)

logger = logging.getLogger(__name__)

#: Art arda bu kadar gönderim arızayla sonuçlanırsa worker kendini durdurur (hesabı korumak
#: için `sending_paused=True` yazar) — CLAUDE.md kural 5. **İSTİSNA:** `smtp_sender.
#: classify_smtp_error()`'ın tanıdığı kota/hız/kimlik hataları bu sayacı hiç BEKLEMEZ, İLK
#: seferde durur (bkz. aşağıdaki `send_result.smtp_pause_reason` kontrolü).
MAX_CONSECUTIVE_ERRORS = 3
#: Test modunda gönderimler arası bekleme sabit ve kısadır: 5 başvuruluk bir deneme yarım
#: saat değil, on saniye sürsün.
TEST_MODE_DELAY_SECONDS = 2.0

# `QueueEvent.kind` değerleri. Arayüzün (src/ui/queue_tab.py) canlı log paneli bunları
# gösterir; CLI (`scripts/run_sender.py`) bunlara hiç bakmaz, kendi `logger` çıktısını okur.
EVENT_RUN_STARTED = "run_started"
EVENT_SENT = "sent"
EVENT_REFUSED = "refused"
EVENT_FAILED = "failed"
EVENT_WAITING = "waiting"
EVENT_PAUSED_BY_WORKER = "paused_by_worker"
EVENT_STOPPED = "stopped"


@dataclass(frozen=True)
class QueueEvent:
    """`on_event` kancasına yayılan tek bir olay.

    `message`, ilgili `logger.info/warning` çağrısıyla AYNI Türkçe dizedir — arayüz ve terminal
    hiçbir zaman farklı bir cümle görmemeli (metin bir kez kurulur, hem loglanır hem yayılır).
    """

    kind: str
    message: str
    domain: Optional[str] = None
    seconds: Optional[float] = None  # yalnızca EVENT_WAITING


#: `process_queue(on_event=...)`'e verilen kancanın imzası.
EventCallback = Callable[[QueueEvent], None]


def _emit(on_event: Optional[EventCallback], event: QueueEvent) -> None:
    if on_event is not None:
        on_event(event)


@dataclass
class QueueRunResult:
    """`process_queue()`'nun döndürdüğü özet."""

    mode: str
    test_mode: bool = True
    #: Transport'a ulaşan (guard reddetmeyen) deneme sayısı.
    attempted: int = 0
    sent_count: int = 0
    #: Guard reddetti — ağa hiç çıkılmadı, kayda dokunulmadı.
    refused_count: int = 0
    failed_count: int = 0
    sent_domains: list[str] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)  # (domain, sebep)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (domain, hata)
    waits: list[float] = field(default_factory=list)
    paused_by_worker: bool = False
    #: Bir gönderim tam işlenirken (transport çalışırken) Ctrl+C basıldıysa şirketin alan
    #: adı burada kalır — o kayıt "sending" durumunda, bkz. `queue.mark_sent`/`mark_queued`.
    interrupted_domain: Optional[str] = None
    #: None ise istenen iş (max_count'a ulaşma) tamamlandığı için durulmuş demektir.
    stop_reason: Optional[str] = None


def _resolve_mode(session: Session, mode: Optional[str]) -> str:
    """Geçerli bir gönderim modu döner; geçersiz/eksik değerde GÜVENLİ tarafa (manuel) düşer."""
    candidate = mode if mode is not None else get_setting(session, KEY_SEND_MODE)
    if candidate not in SEND_MODES:
        logger.warning(f"geçersiz send_mode ({candidate!r}), 'manual'a düşülüyor")
        return MODE_MANUAL
    return candidate


def _wait(
    session: Session,
    result: QueueRunResult,
    sleeper: Callable[[float], None],
    rng: Optional[random.Random],
    on_event: Optional[EventCallback],
) -> None:
    """Gönderimler arası bekler. Uykudan ÖNCE commit eder (bkz. modül docstring'i, (A))."""
    if result.test_mode:
        seconds = TEST_MODE_DELAY_SECONDS
        message = f"test modu: bekleme kısaltıldı ({format_duration_tr(seconds)})"
    else:
        limits = load_limits(session)
        seconds = random_delay_seconds(limits, rng)
        message = f"sıradaki gönderim için {format_duration_tr(seconds)} bekleniyor…"
    logger.info(message)
    _emit(on_event, QueueEvent(EVENT_WAITING, message, seconds=seconds))
    result.waits.append(seconds)
    session.commit()
    sleeper(seconds)


def process_queue(
    session: Session,
    profile: MasterProfile,
    *,
    mode: Optional[str] = None,
    max_count: Optional[int] = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = now_istanbul,
    rng: Optional[random.Random] = None,
    transport: Transport = smtp_transport,
    account: Optional[MailAccount] = None,
    on_event: Optional[EventCallback] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> QueueRunResult:
    """Kuyruğu işler: limit/mesai kontrolü → seçim → gönderim → bekleme, `max_count`'a kadar.

    `mode` verilmezse `send_mode` ayarından okunur. `max_count`, BAŞARILI gönderim sayısıdır;
    limitler her durumda geçerlidir (`max_count=50` + günlük limit 20 ise 20'de durur).
    `account` verilmezse döngüden ÖNCE bir kez çözülür (her gönderimde keyring açmamak için).
    Hiçbir istisna fırlatmaz (Ctrl+C dahil); her zaman bir `QueueRunResult` döner.

    `on_event` verilirse her önemli adım (`logger` ile AYNI Türkçe metinle) bir `QueueEvent`
    olarak yayılır -- `src/ui/queue_tab.py`'nin canlı log paneli bunu kullanır, CLI (`scripts/
    run_sender.py`) hiç geçmez (`None` varsayılanı davranışı değiştirmez).

    `should_stop` verilirse her turun BAŞINDA (limit/mesai kontrolünden ÖNCE) kontrol edilir;
    `True` dönerse tur durur ve `stop_reason` kullanıcının durdurduğunu söyler. Bu, ACİL
    DURDUR'dan (`sending_paused`) FARKLIDIR: `should_stop` hiçbir ayarı YAZMAZ, yalnızca bu
    çalıştırmayı durdurur.
    """
    resolved_mode = _resolve_mode(session, mode)
    result = QueueRunResult(mode=resolved_mode, test_mode=bool(get_setting(session, KEY_TEST_MODE)))

    start_message = (
        f"gönderim başlıyor — mod: {resolved_mode}, test modu: "
        f"{'açık' if result.test_mode else 'kapalı'}"
    )
    logger.info(start_message)
    _emit(on_event, QueueEvent(EVENT_RUN_STARTED, start_message))

    if account is None:
        identity = resolve_identity(session, profile)
        account = load_account(identity, connection=resolve_mail_connection(session))
        if account is None:
            result.stop_reason = (
                "Mail uygulama şifresi kayıtlı değil (python scripts/set_mail_password.py)"
            )
            return result

    tried_ids: set[int] = set()
    errors_in_row = 0
    #: `ALREADY_TEST_SENT_REASON` yüzünden reddedilen başvuru sayısı — tek tek loglanmaz, turun
    #: sonunda TEK bir özet satırına toplanır (büyük bir test-modu backlog'unda log gürültüsünü
    #: önlemek için; bkz. modül docstring'i).
    skipped_test_repeat_count = 0

    try:
        while max_count is None or result.sent_count < max_count:
            # (A) İşlemi kapat: SQLite okuma kilidini bırakır VE `sending_paused` gibi
            # ayarların dışarıdan (başka bir pencereden) değiştirilmişse TAZE okunmasını
            # sağlar — `expire_on_commit=True` (varsayılan, bkz. db.py) sayesinde. Bu satır
            # gereksiz bir "temizlik" değil, acil durdurun çalışmasının dayandığı mekanizma.
            session.commit()

            if should_stop is not None and should_stop():
                result.stop_reason = "kullanıcı gönderimi durdurdu"
                break

            now = clock()
            window = can_send_now(session, now, test_mode=result.test_mode)
            if not window.allowed:
                result.stop_reason = window.reason
                break

            application = next_application(
                session, auto_mode=(resolved_mode == MODE_AUTO), exclude_ids=tried_ids
            )
            if application is None:
                result.stop_reason = (
                    "gönderilecek onaylanmış başvuru kalmadı (onaylamak için: "
                    "python scripts/queue_cli.py approve <alan adı>)"
                    if resolved_mode == MODE_MANUAL
                    else (
                        "gönderilecek uygun başvuru kalmadı; kuyrukta bekleyenler kişi "
                        "adresi veya MX kaydı doğrulanmamış olabilir (python "
                        "scripts/queue_cli.py list)"
                    )
                )
                break

            # Gönderimden ÖNCE: transport() istisna fırlatsa bile bu başvuru bir daha
            # seçilmesin (test modunda tekrar gönderim, gerçek modda sonsuz döngü riski).
            tried_ids.add(application.id)
            result.interrupted_domain = application.company.domain

            send_result = send_application(
                session,
                application,
                profile,
                auto_mode=guard_auto_mode(application),
                transport=transport,
                account=account,
                now=to_naive_utc(now),
            )
            session.commit()  # (B) sonucu kalıcı yaz
            result.interrupted_domain = None

            if send_result.refused_reason:
                # Ağa hiç çıkılmadı: bekleme yok, hata sayacına dokunulmaz (bu bir arıza
                # değil, veri eksikliği — 50 uygunsuz kayıt worker'ı 5 saat bekletmemeli).
                result.refused_count += 1
                result.refused.append((send_result.domain, send_result.refused_reason))
                if send_result.refused_reason == ALREADY_TEST_SENT_REASON:
                    # Büyük bir test-modu backlog'unda her biri için tek tek log basmak yerine
                    # (bkz. modül docstring'i) yalnızca sayılır, özet turun sonunda basılır.
                    skipped_test_repeat_count += 1
                else:
                    message = f"{send_result.domain}: atlandı — {send_result.refused_reason}"
                    logger.info(message)
                    _emit(on_event, QueueEvent(EVENT_REFUSED, message, domain=send_result.domain))
                continue

            result.attempted += 1

            if send_result.error:
                result.failed_count += 1
                result.failed.append((send_result.domain, send_result.error))
                message = f"{send_result.domain}: gönderim başarısız — {send_result.error}"
                logger.warning(message)
                _emit(on_event, QueueEvent(EVENT_FAILED, message, domain=send_result.domain))

                if send_result.smtp_pause_reason:
                    # Kota/hız/kimlik hatası (bkz. `smtp_sender.classify_smtp_error`) GEÇİCİ
                    # değil: art arda 3 hata beklemeden İLK seferde durur, ısrar hesabı daha
                    # kötü duruma sokabilir (ör. yanlış şifreyle tekrar tekrar login denemek).
                    trigger_pause(session, send_result.smtp_pause_reason)
                    session.commit()
                    result.paused_by_worker = True
                    result.stop_reason = send_result.smtp_pause_reason
                    _emit(on_event, QueueEvent(EVENT_PAUSED_BY_WORKER, result.stop_reason))
                    break

                errors_in_row += 1
                if errors_in_row >= MAX_CONSECUTIVE_ERRORS:
                    reason = (
                        "art arda 3 gönderim başarısız oldu; gönderim otomatik durduruldu "
                        "(sending_paused=true). Sorunu giderdikten sonra: python "
                        "scripts/set_setting.py sending_paused false"
                    )
                    trigger_pause(session, reason)
                    session.commit()
                    result.paused_by_worker = True
                    result.stop_reason = reason
                    _emit(on_event, QueueEvent(EVENT_PAUSED_BY_WORKER, result.stop_reason))
                    break
                _wait(session, result, sleeper, rng, on_event)  # arıza sonrası da bekle (geri çekilme)
                continue

            errors_in_row = 0
            result.sent_count += 1
            result.sent_domains.append(send_result.domain)
            message = f"{send_result.domain}: gönderildi"
            logger.info(message)
            _emit(on_event, QueueEvent(EVENT_SENT, message, domain=send_result.domain))

            if max_count is None or result.sent_count < max_count:
                _wait(session, result, sleeper, rng, on_event)  # son gönderimden sonra boşuna beklenmez
    except KeyboardInterrupt:
        result.stop_reason = "kullanıcı Ctrl+C ile durdurdu"

    session.commit()

    if skipped_test_repeat_count:
        summary = f"{skipped_test_repeat_count} başvuru test modunda zaten gönderildi, atlandı"
        logger.info(summary)
        _emit(on_event, QueueEvent(EVENT_REFUSED, summary))

    if result.stop_reason is None:
        result.stop_reason = "istenen gönderim sayısı tamamlandı"
    stop_message = f"gönderim durdu — {result.stop_reason}"
    logger.info(stop_message)
    _emit(on_event, QueueEvent(EVENT_STOPPED, stop_message))

    return result
