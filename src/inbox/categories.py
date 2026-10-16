"""Gelen cevap kategorilerinin Türkçe etiketleri.

PySide6 İÇERMEZ (`src/ui/company_data.py`/`queue_data.py` ile aynı kalıp): arayüz katmanı
bu sözlüğü Qt widget'larına bağlar, testler Qt'siz çalışır.
"""
from src.core.models import (
    CLASSIFIED_BY_KEYWORD,
    CLASSIFIED_BY_LLM,
    CLASSIFIED_BY_RULE,
    CLASSIFIED_BY_USER,
    REPLY_AUTO,
    REPLY_BOUNCE,
    REPLY_INTERVIEW,
    REPLY_OPT_OUT,
    REPLY_PENDING,
    REPLY_REJECTED,
)

#: Cevaplar sekmesinde ve loglarda gösterilen Türkçe etiketler. Anahtarlar `EmailReply.category`
#: ile birebir aynı (`src/core/models.py`); yeni bir kategori eklenirse burası da güncellenmeli
#: — `tests/test_inbox_categories.py` bunu `REPLY_CATEGORIES_ALL`'a karşı denetler.
REPLY_LABELS_TR: dict[str, str] = {
    REPLY_INTERVIEW: "Mülakat/Olumlu",
    REPLY_REJECTED: "Red",
    REPLY_PENDING: "Bekliyor/Süreçte",
    REPLY_AUTO: "Otomatik cevap",
    REPLY_BOUNCE: "Teslim edilemedi",
    REPLY_OPT_OUT: "Bir daha yazmayın",
}

#: `EmailReply.classified_by` için Türkçe etiketler (Cevaplar sekmesi önizleme paneli).
CLASSIFIED_BY_LABELS_TR: dict[str, str] = {
    CLASSIFIED_BY_LLM: "yapay zekâ",
    CLASSIFIED_BY_KEYWORD: "anahtar kelime",
    CLASSIFIED_BY_RULE: "kural",
    CLASSIFIED_BY_USER: "elle düzeltildi",
}
