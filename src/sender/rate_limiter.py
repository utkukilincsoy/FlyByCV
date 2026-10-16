"""Gönderim limitlerini ve mesai saati kısıtını denetler (CLAUDE.md kural 5).

Tek giriş noktası `can_send_now()`: acil durdur, mesai saati/hafta sonu, günlük limit ve
saatlik limiti bu sırayla kontrol eder ve izin yoksa Türkçe bir sebep + ne kadar sonra tekrar
denenebileceğini döner. Sayımlar `Application.sent_at`'ten (naive UTC) alınır; mesai saati ve
"günlük limit" ise İstanbul takvim gününe göredir — ikisi arasındaki dönüşüm `core/timeutil.py`
üzerinden yapılır.

Ayarlar HER ÇAĞRIDA yeniden okunur: kullanıcı `scripts/set_setting.py` ile çalışma ortasında
değiştirebilir, `src/sender/worker.py` bunu her gönderimden önce tekrar sorar.
"""
import logging
import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import NamedTuple, Optional, Sequence

from sqlmodel import Session, select

from src.core.models import Application
from src.core.settings import (
    DEFAULTS,
    KEY_DAILY_LIMIT,
    KEY_HOURLY_LIMIT,
    KEY_MAX_DELAY_SECONDS,
    KEY_MIN_DELAY_SECONDS,
    KEY_SENDING_PAUSED,
    KEY_WEEKDAYS_ONLY,
    KEY_WORK_END,
    KEY_WORK_START,
    KEY_WORKING_HOURS_ONLY,
    get_setting,
)
from src.core.text import TURKISH_WEEKDAYS, format_duration_tr
from src.core.timeutil import (
    as_istanbul,
    combine_istanbul,
    istanbul_day_start_utc,
    now_istanbul,
    parse_hhmm,
    to_naive_utc,
)

logger = logging.getLogger(__name__)

#: Saatlik limit için "son ne kadar" (dakika). Değiştirilmesi gerekmez, sabit tutulur.
HOURLY_WINDOW_MINUTES = 60
#: `next_window_start` sonsuz döngüye girmesin diye en fazla bu kadar gün ileri bakar
#: (weekdays_only + tüm günler yasak gibi imkânsız bir ayar kombinasyonuna karşı).
MAX_LOOKAHEAD_DAYS = 8

#: Kodda sabit üst sınırlar (CLAUDE.md kural 5, PLAN.md Adım 10.8a). Ayarlardaki değer bunları
#: aşarsa `load_limits()` sessizce bu değerlere kırpar ve loglar — Ayarlar sekmesi de
#: (`src/ui/settings_data.py::validate_form`) daha büyük/küçük bir değeri baştan reddeder.
#: Amaç sıradan kullanıcıyı ve yanlış ayarı durdurmak, kaynak kodu değiştirebilecek bir
#: geliştiriciyi değil (bkz. PLAN.md Adım 10.8a "Risk" notu).
HARD_MAX_DAILY = 50
HARD_MAX_HOURLY = 20
HARD_MIN_DELAY_SECONDS = 30


#: `SendWindow.code` değerleri — Otopilot (Faz 9.5) Türkçe metni ayrıştırmak yerine buna
#: bakarak karar versin diye (`reason` yalnızca kullanıcıya göstermek/loglamak içindir).
CODE_PAUSED = "paused"
CODE_OUTSIDE_HOURS = "outside_hours"
CODE_DAILY_LIMIT = "daily_limit"
CODE_HOURLY_LIMIT = "hourly_limit"


class SendWindow(NamedTuple):
    """`can_send_now()`'ın sonucu."""

    allowed: bool
    #: İzin varsa None; yoksa Türkçe, sayılarla birlikte bir gerekçe.
    reason: Optional[str]
    #: İzin yoksa tahmini bekleme (saniye); paused durumunda 0 (elle müdahale gerekir).
    wait_seconds: int
    #: İzin varsa None; yoksa engelin türü (`CODE_*` sabitlerinden biri) — programatik karar
    #: için, Türkçe `reason` metnine bakmak/ayrıştırmak GEREKMEZ.
    code: Optional[str] = None


@dataclass(frozen=True)
class RateLimits:
    """Doğrulanmış, kullanıma hazır limitler. `load_limits()` ile üretilir."""

    daily_limit: int
    hourly_limit: int
    min_delay_seconds: int
    max_delay_seconds: int
    working_hours_only: bool
    work_start: time
    work_end: time
    weekdays_only: bool


def _int_setting(session: Session, key: str) -> int:
    """Negatif olmayan bir tamsayı ayarı okur; bozuksa uyarıp varsayılana düşer."""
    value = get_setting(session, key)
    default = DEFAULTS[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        logger.warning(f"{key} ayarı geçersiz ({value!r}), varsayılana ({default}) dönülüyor")
        return default
    return value


def _hhmm_setting(session: Session, key: str) -> time:
    """'SS:DD' biçimindeki bir ayarı okur; bozuksa uyarıp varsayılana düşer."""
    value = get_setting(session, key)
    try:
        return parse_hhmm(value)
    except ValueError:
        default = DEFAULTS[key]
        logger.warning(f"{key} ayarı geçersiz ({value!r}), varsayılana ({default!r}) dönülüyor")
        return parse_hhmm(default)


def _clamp_to_hard_cap(value: int, hard_cap: int, key: str) -> int:
    """Değer sabit üst sınırı aşarsa kırpar ve loglar (PLAN.md Adım 10.8a)."""
    if value > hard_cap:
        logger.warning(
            f"{key} ayarı ({value}) sabit üst sınırı ({hard_cap}) aşıyor, {hard_cap}'e kırpılıyor"
        )
        return hard_cap
    return value


def _clamp_to_hard_floor(value: int, hard_floor: int, key: str) -> int:
    """Değer sabit alt sınırın altındaysa yükseltir ve loglar (PLAN.md Adım 10.8a)."""
    if value < hard_floor:
        logger.warning(
            f"{key} ayarı ({value}) sabit alt sınırın ({hard_floor}) altında, "
            f"{hard_floor}'e yükseltiliyor"
        )
        return hard_floor
    return value


def load_limits(session: Session) -> RateLimits:
    """Limit ayarlarını okur, doğrular ve tek bir `RateLimits` nesnesine toplar.

    Bekleme ayarları (`KEY_MIN_DELAY_SECONDS`/`KEY_MAX_DELAY_SECONDS`) zaten saniye cinsinden
    tutulur, `RateLimits`'e olduğu gibi aktarılır. Günlük/saatlik limit ve en az bekleme,
    kodda sabit üst/alt sınırların (`HARD_MAX_DAILY`/`HARD_MAX_HOURLY`/
    `HARD_MIN_DELAY_SECONDS`) İÇİNE kırpılır — bu, `can_send_now()` ve `random_delay_seconds()`
    dahil bu fonksiyonu çağıran HER yolu (Otopilot, kuyruk işçisi, `scripts/send_one.py`)
    otomatik olarak kapsar.
    """
    working_hours_only = bool(get_setting(session, KEY_WORKING_HOURS_ONLY))
    weekdays_only = bool(get_setting(session, KEY_WEEKDAYS_ONLY))
    work_start = _hhmm_setting(session, KEY_WORK_START)
    work_end = _hhmm_setting(session, KEY_WORK_END)

    if working_hours_only and work_start >= work_end:
        # Gece vardiyası (work_start > work_end) desteklenmiyor; work_start == work_end de
        # hiçbir pencere bırakmaz. İkisi de varsayılana düşürülür, çökme yerine temiz mesaj.
        logger.warning(
            f"work_start ({work_start}) work_end'den ({work_end}) önce olmalı; gece vardiyası "
            "desteklenmiyor, varsayılan mesai saatlerine (09:00-18:00) dönülüyor"
        )
        work_start = parse_hhmm(DEFAULTS[KEY_WORK_START])
        work_end = parse_hhmm(DEFAULTS[KEY_WORK_END])

    daily_limit = _clamp_to_hard_cap(
        _int_setting(session, KEY_DAILY_LIMIT), HARD_MAX_DAILY, KEY_DAILY_LIMIT
    )
    hourly_limit = _clamp_to_hard_cap(
        _int_setting(session, KEY_HOURLY_LIMIT), HARD_MAX_HOURLY, KEY_HOURLY_LIMIT
    )
    min_delay_seconds = _clamp_to_hard_floor(
        _int_setting(session, KEY_MIN_DELAY_SECONDS), HARD_MIN_DELAY_SECONDS, KEY_MIN_DELAY_SECONDS
    )
    max_delay_seconds = _int_setting(session, KEY_MAX_DELAY_SECONDS)
    if max_delay_seconds < min_delay_seconds:
        # min_delay_seconds kırpma sonrası max'ı geçmiş olabilir (ör. ayarda 5-10 sn yazılmışsa,
        # min 30'a kırpılınca max < min kalır); random_delay_seconds zaten max(low, high)
        # savunması yapıyor ama burada da düzeltmek limitin GERÇEK değerini şeffaf tutar.
        max_delay_seconds = min_delay_seconds

    return RateLimits(
        daily_limit=daily_limit,
        hourly_limit=hourly_limit,
        min_delay_seconds=min_delay_seconds,
        max_delay_seconds=max_delay_seconds,
        working_hours_only=working_hours_only,
        work_start=work_start,
        work_end=work_end,
        weekdays_only=weekdays_only,
    )


def sent_timestamps_since(
    session: Session, since: datetime, *, test_mode: bool = False
) -> list[datetime]:
    """`since`'ten (naive UTC) bu yana gönderilmiş maillerin zamanlarını artan sırada döner.

    `test_mode=True` iken `Application.sent_at` YERİNE `Application.test_sent_at` sayılır: test
    modunda gerçek `sent_at` hiç yazılmaz (bkz. `smtp_sender.py`), bu yüzden test modunun da
    günlük/saatlik limitlere tabi olması (CLAUDE.md kural 5) ancak bu sütuna bakarak mümkündür.
    Sütun `IS NULL` olan (hiç gönderilmemiş) başvurular her iki durumda da kendiliğinden dışarıda
    kalır.
    """
    column = Application.test_sent_at if test_mode else Application.sent_at
    statement = select(column).where(column >= since).order_by(column)
    return list(session.exec(statement).all())


def is_working_time(local: datetime, limits: RateLimits) -> bool:
    """`local` (İstanbul saatiyle, aware) bir gönderim penceresinin içinde mi?

    `weekdays_only` ve `working_hours_only` BİRBİRİNDEN BAĞIMSIZ çalışır: biri kapalıyken
    diğeri hâlâ geçerlidir (örn. hafta sonu kısıtı olmadan sadece mesai saatine uymak, ya da
    saat kısıtı olmadan sadece hafta sonunu atlamak isteyebilirsiniz).
    """
    if limits.weekdays_only and local.weekday() >= 5:  # 5=Cumartesi, 6=Pazar
        return False
    if limits.working_hours_only and not (limits.work_start <= local.time() < limits.work_end):
        return False
    return True


def next_window_start(local: datetime, limits: RateLimits) -> datetime:
    """`local`'den SONRAKİ ilk gönderim penceresinin başlangıcını döner (İstanbul, aware).

    `local` şu an bir pencerenin içindeyse (örn. günlük limit dolduğu için çağrılmışsa) o
    günün kendisi atlanır, sonraki uygun günün başlangıcı bulunur. En fazla
    `MAX_LOOKAHEAD_DAYS` gün ileri bakar; imkânsız bir ayar kombinasyonunda (teoride
    ulaşılamaz, `load_limits` zaten `work_start >= work_end`'i düzeltiyor) `local`'in kendisi
    döner — sonsuz döngü yerine çağıranın "pencere yok" demesine izin verir.
    """
    day_start_time = limits.work_start if limits.working_hours_only else time(0, 0)
    for offset in range(MAX_LOOKAHEAD_DAYS):
        candidate_date = (local + timedelta(days=offset)).date()
        if limits.weekdays_only and combine_istanbul(candidate_date, time(0, 0)).weekday() >= 5:
            continue
        candidate = combine_istanbul(candidate_date, day_start_time)
        if candidate > local:
            return candidate
    logger.warning("ayarlar hiçbir gönderim penceresi bırakmıyor, sıradaki pencere bulunamadı")
    return local


def random_delay_seconds(limits: RateLimits, rng: Optional[random.Random] = None) -> float:
    """Gönderimler arası bekleme süresini (saniye) rastgele seçer.

    `max_delay_seconds < min_delay_seconds` (yanlış ayar) durumunda `min`'e sabitlenir —
    `site_crawler._RequestBudget` ile aynı savunma.
    """
    rng = rng or random.Random()
    low = limits.min_delay_seconds
    high = max(low, limits.max_delay_seconds)
    return rng.uniform(low, high)


def is_hard_cap_block(window: SendWindow, limits: RateLimits) -> bool:
    """`window`'un engeli sabit üst sınıra mı denk geliyor, yoksa kullanıcının kendi (daha
    düşük) ayarına mı?

    `load_limits()` günlük/saatlik limiti zaten `HARD_MAX_DAILY`/`HARD_MAX_HOURLY`'nin İÇİNE
    kırptığı için, `limits.daily_limit`/`limits.hourly_limit` sabit tavana EŞİTSE gerçek engel
    sabit tavandır (kullanıcı ayarı ya tavanın üzerindeydi ya da zaten tavana eşitti) —
    `scripts/send_one.py`'deki tek seferlik "onayla limiti aş" yolu bunu AŞAMAZ (PLAN.md Adım
    10.8a-2), yalnızca kullanıcının kendi daha düşük ayarını (limit tavanın altındaysa) aşabilir.
    """
    if window.code == CODE_DAILY_LIMIT:
        return limits.daily_limit >= HARD_MAX_DAILY
    if window.code == CODE_HOURLY_LIMIT:
        return limits.hourly_limit >= HARD_MAX_HOURLY
    return False


def _format_window_start(start: datetime, local: datetime) -> str:
    """"yarın 09:00" / "Pazartesi 09:00" gibi okunur bir zaman ifadesi üretir."""
    clock = start.strftime("%H:%M")
    if start.date() == local.date():
        return clock
    if start.date() == local.date() + timedelta(days=1):
        return f"yarın {clock}"
    return f"{TURKISH_WEEKDAYS[start.weekday()]} {clock}"


def _outside_hours_window(session: Session, local: datetime, limits: RateLimits) -> SendWindow:
    next_start = next_window_start(local, limits)
    wait_seconds = max(0, round((next_start - local).total_seconds()))
    window_text = _format_window_start(next_start, local)
    duration_text = format_duration_tr(wait_seconds)

    if limits.weekdays_only and local.weekday() >= 5:
        weekday_name = TURKISH_WEEKDAYS[local.weekday()]
        reason = (
            f"bugün hafta sonu ({weekday_name}), hafta içi dışında gönderim yapılmıyor; "
            f"sıradaki pencere {window_text} ({duration_text} sonra)"
        )
    else:
        reason = (
            f"mesai saati dışı (şu an {local.strftime('%H:%M')}, çalışma penceresi "
            f"{limits.work_start.strftime('%H:%M')}–{limits.work_end.strftime('%H:%M')}); "
            f"sıradaki pencere {window_text} ({duration_text} sonra)"
        )
    return SendWindow(False, reason, wait_seconds, CODE_OUTSIDE_HOURS)


def can_send_now(
    session: Session,
    now: Optional[datetime] = None,
    *,
    extra_sent_at: Sequence[datetime] = (),
    test_mode: bool = False,
) -> SendWindow:
    """Şu an gönderim yapılabilir mi? Sırasıyla acil durdur, mesai/hafta sonu, günlük limit,
    saatlik limit kontrol edilir; ilk engelleyen sebep Türkçe döner.

    `test_mode`: `True` iken günlük/saatlik sayım `Application.test_sent_at`'ten yapılır (bkz.
    `sent_timestamps_since`) — test modunda gerçek `sent_at` hiç yazılmadığı için (6.1 kararı)
    bu olmadan test modunda limit hiç uygulanmazdı. `test_sent_at` her gönderimden hemen sonra
    COMMIT edildiğinden, bu tek başına ÇAĞRILAR ARASI (aynı gün içindeki farklı
    `process_queue()`/Otopilot turları dahil) doğru sayım için yeterlidir.

    `extra_sent_at`: HENÜZ COMMIT EDİLMEMİŞ (çağıranın şu an elinde tuttuğu) ek zamanları
    günlük/saatlik sayıma ve "en eski gönderim" hesabına DB'den gelenlerle birlikte katar —
    `test_mode` sayımıyla ÇİFT SAYILMAMASI için çağıran, DB'ye zaten yazılmış bir zamanı burada
    tekrar VERMEMELİDİR.
    """
    local = as_istanbul(now) if now is not None else now_istanbul()

    if get_setting(session, KEY_SENDING_PAUSED):
        return SendWindow(
            False,
            "gönderim acil durdur ile duraklatılmış (ayar: sending_paused). Devam etmek için: "
            "python scripts/set_setting.py sending_paused false",
            0,
            CODE_PAUSED,
        )

    limits = load_limits(session)

    if not is_working_time(local, limits):
        return _outside_hours_window(session, local, limits)

    # Saatlik pencere gece yarısını aşabilir (yerel 00:30'da saatlik pencere DÜNKÜ 23:30'da
    # başlar); tek sorgu için ikisinden ERKEN olanından itibaren çekilir.
    now_naive_utc = to_naive_utc(local)
    hour_start_naive = now_naive_utc - timedelta(minutes=HOURLY_WINDOW_MINUTES)
    day_start_naive = istanbul_day_start_utc(local)
    since = min(day_start_naive, hour_start_naive)

    stamps = sorted(sent_timestamps_since(session, since, test_mode=test_mode)) + sorted(extra_sent_at)

    daily_count = sum(1 for stamp in stamps if stamp >= day_start_naive)
    if daily_count >= limits.daily_limit:
        next_start = next_window_start(local, limits)
        wait_seconds = max(0, round((next_start - local).total_seconds()))
        reason = (
            f"günlük gönderim limiti doldu ({daily_count}/{limits.daily_limit}); sayaç Türkiye "
            f"saatiyle gece yarısında sıfırlanır, sıradaki pencere "
            f"{_format_window_start(next_start, local)} ({format_duration_tr(wait_seconds)} sonra)"
        )
        return SendWindow(False, reason, wait_seconds, CODE_DAILY_LIMIT)

    hourly_stamps = [stamp for stamp in stamps if stamp >= hour_start_naive]
    if len(hourly_stamps) >= limits.hourly_limit:
        oldest = min(hourly_stamps)
        free_at = oldest + timedelta(minutes=HOURLY_WINDOW_MINUTES)
        wait_seconds = max(0, round((free_at - now_naive_utc).total_seconds()))
        reason = (
            f"saatlik gönderim limiti doldu (son {HOURLY_WINDOW_MINUTES} dakikada "
            f"{len(hourly_stamps)}/{limits.hourly_limit}); en erken {format_duration_tr(wait_seconds)} "
            "sonra devam edilebilir"
        )
        return SendWindow(False, reason, wait_seconds, CODE_HOURLY_LIMIT)

    return SendWindow(True, None, 0)
