"""Otopilot sekmesinin veri katmanı — PySide6 İÇERMEZ (bkz. `src/ui/dashboard_data.py`).

`src/autopilot/runner.py`'nin döndürdüğü/yaydığı nesneleri (`AutopilotStats`, `AutopilotEvent`)
Türkçe metne çevirir; sekme (`src/ui/autopilot_tab.py`) yalnızca bu metinleri widget'lara basar.
`format_log_line`, `src/ui/queue_data.py::format_log_line` ile AYNI imza/üslupla (saf biçim,
Qt yok) -- iki sekme de aynı desende ("[HH:MM:SS] sembol mesaj") canlı log tutuyor.
"""
from datetime import datetime
from typing import Optional

from src.autopilot.runner import (
    STAGE_CRAWL,
    STAGE_DISCOVER,
    STAGE_IDLE,
    STAGE_INBOX,
    STAGE_PAUSED,
    STAGE_PREPARE,
    STAGE_SEND,
    STAGE_STOPPED,
    STAGE_WAITING,
    AutopilotEvent,
    AutopilotStats,
    SourcePreview,
)
from src.company_finder.places_cost import PlacesCostEstimate
from src.ui.settings_data import SOURCE_LABELS_TR

#: `AutopilotEvent.stage` -> "o anki aşama" etiketinde gösterilecek Türkçe ad.
STAGE_LABELS_TR: dict[str, str] = {
    STAGE_DISCOVER: "Keşif",
    STAGE_CRAWL: "Tarama",
    STAGE_PREPARE: "Başvuru hazırlama",
    STAGE_INBOX: "Gelen kutusu kontrolü",
    STAGE_SEND: "Gönderim",
    STAGE_WAITING: "Bekleniyor",
    STAGE_PAUSED: "Duraklatıldı",
    STAGE_STOPPED: "Durduruldu",
    STAGE_IDLE: "Boşta",
}

#: Canlı log satırındaki sembol -- yalnızca kontrol akışı bakımından farklı olan aşamalar
#: ayrı bir sembol alır (`src/ui/queue_data.py::EVENT_SYMBOLS` ile aynı üslup); geri kalanı
#: (keşif/tara/hazırla/gelen kutusu/gönderim) tek bir nötr sembolü paylaşır.
_DEFAULT_LOG_SYMBOL = "•"
STAGE_LOG_SYMBOLS: dict[str, str] = {
    STAGE_WAITING: "⏳",
    STAGE_PAUSED: "⛔",
    STAGE_STOPPED: "■",
    STAGE_IDLE: "■",
}

DEFAULT_STAGE_TEXT = "Henüz çalıştırılmadı."
DEFAULT_STOP_REASON_TEXT = "Henüz çalıştırılmadı."

#: Otopilot sekmesindeki test modu şeridi -- durum şeridininkinden (`status_strip.py`)
#: BİLİNÇLİ olarak farklı bir dize, görev tanımında birebir bu metin isteniyor.
TEST_MODE_BANNER_TEXT = "TEST MODU — mailler size gidiyor"

REAL_SEND_CONFIRM_TITLE = "GERÇEK GÖNDERİM"

#: Sayaç kutularının başlıkları, `format_stat_values()`'in döndürdüğü değerlerle AYNI sırada.
STAT_TITLES: tuple[str, ...] = (
    "Bulunan şirket",
    "E-postası olan",
    "Rol adresi",
    "Kişi adresi",
    "Bugün gönderilen",
    "Kuyrukta bekleyen",
    "Sıradaki gönderim",
)


def format_stage_text(stage: Optional[str]) -> str:
    """"O anki aşama" etiketi; hiç çalıştırılmadıysa (`stage=None`) sabit bir metin döner."""
    if stage is None:
        return DEFAULT_STAGE_TEXT
    return f"Aşama: {STAGE_LABELS_TR.get(stage, stage)}"


def format_log_line(event: AutopilotEvent, *, at: datetime) -> str:
    """Canlı log panelindeki tek satır: `"[14:05:12] • firma.com: Otopilot taraması tamamlandı"`.

    `at` dışarıdan verilir (testler deterministik olsun diye, `queue_data.format_log_line`
    ile aynı gerekçe); sekme `now_istanbul()` geçer.
    """
    symbol = STAGE_LOG_SYMBOLS.get(event.stage, _DEFAULT_LOG_SYMBOL)
    return f"[{at.strftime('%H:%M:%S')}] {symbol} {event.message}"


def format_stop_reason_text(reason: Optional[str]) -> str:
    """Kalıcı "durma sebebi" etiketi -- bir sonraki "Başlat"a kadar ekranda KALIR."""
    if reason is None:
        return DEFAULT_STOP_REASON_TEXT
    return f"Durma sebebi: {reason}"


def _format_next_send_value(stats: AutopilotStats) -> str:
    if stats.next_send_reason is None:
        return "şu an"
    if stats.next_send_at is None:  # acil durdur/güvenlik freni -- elle müdahale gerekir
        return "elle müdahale gerekiyor"
    return stats.next_send_at.strftime("%H:%M")


def format_stat_values(stats: AutopilotStats) -> tuple[str, ...]:
    """`STAT_TITLES` ile AYNI sırada, sayaç kutularında gösterilecek değerler."""
    return (
        str(stats.companies_found),
        str(stats.companies_with_email),
        str(stats.role_address_count),
        str(stats.person_address_count),
        f"{stats.sent_today}/{stats.daily_limit}",
        str(stats.queued_count),
        _format_next_send_value(stats),
    )


def _format_date_tr(day) -> str:
    return day.strftime("%d.%m.%Y")


def _format_usd(amount: float) -> str:
    return f"${amount:,.2f}"


def format_places_preview(places: PlacesCostEstimate) -> tuple[str, bool]:
    """Google Places maliyet önizlemesi (metin, kırmızı uyarı mı). Fiyat bilinmiyorsa ASLA
    "ücretsiz" denmez: kırmızı uyarıyla "hesaplanamadı" yazılır."""
    lines = []
    if places.pending_searches == 0:
        lines.append("Google Places: bekleyen arama yok (hepsi daha önce tamamlanmış); yeni istek atılmayacak.")
    else:
        lines.append(
            f"Google Places: en fazla {places.max_requests} istek "
            f"({places.keyword_count} arama kelimesi × {places.district_count} "
            f"{'ilçe' if places.district_count > 1 else 'bölge (ilçe listesi yok)'} × "
            f"{places.pages_per_search} sayfa; tamamlanmış aramalar düşüldü: {places.pending_searches} arama)."
        )
    lines.append(
        f"Bu ay kullanılan / sınır: {places.used_this_month} / {places.monthly_limit} "
        f"(kalan {places.remaining_this_month})."
    )
    if places.pending_searches > 0 and places.remaining_this_month == 0:
        lines.append("Aylık sınır dolu: bu çalıştırmada Google'a hiç istek atılmayacak.")
        return "\n".join(lines), False
    if places.pending_searches > 0 and places.planned_requests < places.max_requests:
        lines.append(
            f"Aylık sınır yüzünden bu çalıştırmada en çok {places.planned_requests} istek atılabilir."
        )

    pricing = places.pricing
    if pricing is None:
        if places.planned_requests > 0:
            lines.append(
                "⚠ Fiyat bilgisi (presets/google_places_pricing.json) okunamadı: maliyet HESAPLANAMADI, "
                "ücretsiz olduğu varsayılmamalı."
            )
            return "\n".join(lines), True
        return "\n".join(lines), False

    lines.append(
        f"Fiyat: {pricing.sku}, aylık {pricing.free_monthly_requests} istek ücretsiz, sonrası "
        f"{_format_usd(pricing.usd_per_1000_requests)} / 1000 istek "
        f"(son kontrol {_format_date_tr(pricing.last_checked)}; fiyat değişebilir)."
    )
    if places.planned_requests == 0:
        return "\n".join(lines), False
    if places.within_free_quota:
        lines.append("Bu çalıştırma ücretsiz kota içinde: tahmini ek maliyet $0.")
        return "\n".join(lines), False
    lines.append(
        f"⚠ ÜCRETSİZ KOTA AŞILIR: {places.billable_requests} istek ücretli, tahmini ek maliyet "
        f"≈ {_format_usd(places.estimated_usd or 0.0)}. Google hesabınıza fatura çıkabilir."
    )
    return "\n".join(lines), True


def format_source_preview(preview: SourcePreview) -> tuple[str, bool]:
    """"Başlat"ın yanındaki kaynak özeti ve (Places seçiliyse) maliyet önizlemesi.

    Döner: (metin, kırmızı uyarı mı). Places kapalıyken yalnızca iki kısa satır çıkar.
    """
    who = []
    if preview.profession_name:
        who.append(f"Meslek: {preview.profession_name}")
    if preview.city_name:
        who.append(f"Şehir: {preview.city_name}")
    names = ", ".join(SOURCE_LABELS_TR[source] for source in preview.source_ids)
    lines = [f"Kaynaklar: {names}." if names else "Kaynaklar: otomatik kaynak yok (CSV her zaman açık)."]
    if who:
        lines[0] = " · ".join(who) + " — " + lines[0]
    if preview.places is None:
        return "\n".join(lines), False
    text, warning = format_places_preview(preview.places)
    return "\n".join([*lines, text]), warning


PLACES_CONFIRM_TITLE = "Google Places kullanılacak"


def remaining_today(stats: AutopilotStats) -> int:
    """Bugün gönderilebilecek en fazla mail sayısı (kalan günlük hak); negatife düşmez."""
    return max(stats.daily_limit - stats.sent_today, 0)


def format_real_send_confirm_text(stats: AutopilotStats) -> str:
    """Test modu KAPALIYKEN "Başlat"a basınca çıkan TEK onay kutusunun metni."""
    return f"GERÇEK GÖNDERİM: bugün en fazla {remaining_today(stats)} mail gidecek. Devam?"
