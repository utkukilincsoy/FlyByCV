"""Europe/Istanbul saat dilimi dönüşümleri.

`Application.sent_at` (ve diğer zaman alanları) **naive UTC** tutuluyor (`models.utcnow()`).
Mesai saati kısıtı ve "günlük limit" kontrolü ise İstanbul takvim gününe/saatine göre. Bu modül
ikisi arasındaki TEK dönüşüm noktasıdır — dönüşüm başka hiçbir dosyada elle yapılmamalı.

Windows'ta IANA saat dilimi veritabanı yoktur; `ZoneInfo("Europe/Istanbul")` `tzdata` paketi
olmadan `ZoneInfoNotFoundError` fırlatır (bkz. requirements.txt). Bu yüzden modül import
edilirken bu hata yakalanıp Türkçe, ne yapılması gerektiğini söyleyen bir hataya çevrilir.

Sabit `timedelta(hours=3)` YAZILMAZ: Türkiye 2016'dan beri kalıcı UTC+3, ama kural değişirse
`astimezone()` tabanlı kod `tzdata` güncellemesiyle kendiliğinden doğru kalır; sabit bir ofset
sessizce bozulurdu.
"""
from datetime import date, datetime, time, timezone

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except Exception as error:  # pragma: no cover - zoneinfo her zaman stdlib'de var (3.9+)
    raise RuntimeError(f"zoneinfo modülü yüklenemedi: {error}") from error

try:
    ISTANBUL = ZoneInfo("Europe/Istanbul")
except ZoneInfoNotFoundError as error:
    raise RuntimeError(
        "Europe/Istanbul saat dilimi bulunamadı. Windows'ta bu, 'tzdata' paketinin kurulu "
        "olmadığı anlamına gelir. Kurmak için: pip install tzdata "
        "(veya: pip install -r requirements.txt)"
    ) from error


try:
    #: Google Maps Platform faturalaması/ücretsiz kota ayı Pasifik saatine göre döner.
    PACIFIC = ZoneInfo("America/Los_Angeles")
except ZoneInfoNotFoundError as error:
    raise RuntimeError(
        "America/Los_Angeles saat dilimi bulunamadı ('tzdata' paketi kurulu değil). "
        "Kurmak için: pip install tzdata"
    ) from error


def pacific_month_key(moment: datetime) -> str:
    """`moment`'in Pasifik saatindeki takvim ayı ("2026-09"). Naive ise UTC kabul edilir."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(PACIFIC).strftime("%Y-%m")


def now_istanbul() -> datetime:
    """Şu anki zamanı, İstanbul saat dilimiyle (aware) döner."""
    return datetime.now(ISTANBUL)


def as_istanbul(moment: datetime) -> datetime:
    """Herhangi bir zamanı İstanbul saat dilimine çevirir.

    `moment` naive ise (tzinfo yok) **UTC olduğu kabul edilir** — projedeki tüm naive zamanlar
    `models.utcnow()`'dan geliyor. Aware ise `astimezone()` ile doğru şekilde çevrilir.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ISTANBUL)


def to_istanbul(naive_utc: datetime) -> datetime:
    """`as_istanbul()`'un okunur takma adı: naive UTC bir zamanı İstanbul saatine çevirir."""
    return as_istanbul(naive_utc)


def to_naive_utc(moment: datetime) -> datetime:
    """Herhangi bir zamanı naive UTC'ye çevirir (veritabanı karşılaştırmaları için).

    DİKKAT: `.replace(tzinfo=None)` ile karıştırılmamalı — o saati aynı bırakıp offset'i
    sessizce atar (İstanbul saatinde 3 saatlik yanlış bir ana yol açar). Doğru dönüşüm her
    zaman ÖNCE `astimezone(timezone.utc)`, SONRA `.replace(tzinfo=None)`. Bu iki satır
    PROJEDE YALNIZCA burada geçmeli.
    """
    if moment.tzinfo is None:
        # Zaten naive: projedeki sözleşmeye göre bu zaten naive UTC'dir, dokunma.
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def combine_istanbul(day: date, at: time) -> datetime:
    """Bir tarih + saati, İstanbul saat diliminde aware bir `datetime`'a birleştirir."""
    return datetime.combine(day, at, tzinfo=ISTANBUL)


def istanbul_day_start_utc(moment: datetime) -> datetime:
    """`moment`'in İstanbul takvim gününün yerel gece yarısını, naive UTC olarak döner.

    Örnek: 2026-09-27 14:30 (İstanbul) -> yerel 2026-09-27 00:00 -> naive UTC 2026-09-26 21:00.
    """
    local = as_istanbul(moment)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return to_naive_utc(midnight)


def parse_hhmm(value: object) -> time:
    """"09:00" gibi bir metni `time(9, 0)`'a çevirir.

    `value` bilerek `object`: `scripts/set_setting.py`'nin değer çözümleyicisi ("0900" gibi bir
    girdiyi) sayıya çevirebilir; bu fonksiyon o durumda da anlaşılır bir `ValueError` versin.

    Raises:
        ValueError: Biçim "SS:DD" değilse veya saat/dakika aralık dışındaysa.
    """
    if not isinstance(value, str):
        raise ValueError(f"Saat 'SS:DD' biçiminde bir metin olmalı, gelen: {value!r}")

    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Saat 'SS:DD' biçiminde olmalı (örn. '09:00'), gelen: {value!r}")

    hour_text, minute_text = parts
    if not (hour_text.isdigit() and minute_text.isdigit()):
        raise ValueError(f"Saat 'SS:DD' biçiminde olmalı (örn. '09:00'), gelen: {value!r}")

    hour, minute = int(hour_text), int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Saat aralık dışında (00:00-23:59 olmalı), gelen: {value!r}")

    return time(hour, minute)


__all__ = [
    "ISTANBUL",
    "PACIFIC",
    "pacific_month_key",
    "now_istanbul",
    "as_istanbul",
    "to_istanbul",
    "to_naive_utc",
    "combine_istanbul",
    "istanbul_day_start_utc",
    "parse_hhmm",
]
