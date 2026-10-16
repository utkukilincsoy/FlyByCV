"""Google Places maliyet önizlemesi — PLAN.md Faz 10 Adım 10.6h.

SAF hesap: ağa/veritabanına dokunmaz. Kullanıcıya "Başlat"tan ÖNCE şunları söylemek için:
en fazla kaç istek atılabilir (kelime × ilçe × 3 sayfa), bu ay kaçı kullanıldı / sınır kaç,
ücretsiz kota içinde mi, değilse Enterprise fiyatıyla tahmini ek maliyet.

Fiyatlar kodda DEĞİL `presets/google_places_pricing.json`'dadır ("son kontrol tarihi" ile; fiyat
değişebilir). Bozuk/eksik dosya uygulamayı ÇÖKERTMEZ (`professions.py`/`cities.py` ile aynı ilke):
`load_places_pricing()` None döner ve önizleme "maliyet hesaplanamadı" der — bilinmeyen fiyatı
asla "ücretsiz" diye göstermez.
"""
import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ValidationError, field_validator

from src.company_finder.google_places import MAX_PAGES_PER_QUERY
from src.core.paths import places_pricing_path

logger = logging.getLogger(__name__)


class PlacesPricing(BaseModel):
    last_checked: date
    source_url: str
    currency: str
    sku: str
    free_monthly_requests: int
    usd_per_1000_requests: float
    note: str = ""

    @field_validator("free_monthly_requests", "usd_per_1000_requests")
    @classmethod
    def not_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("negatif olamaz")
        return value

    @field_validator("currency")
    @classmethod
    def currency_is_usd(cls, value: str) -> str:
        if value != "USD":
            raise ValueError("şimdilik yalnızca USD destekleniyor")
        return value


def load_places_pricing(path: Optional[Path] = None) -> Optional[PlacesPricing]:
    """Fiyat dosyasını okur; yoksa/bozuksa uyarı loglayıp `None` döner."""
    file_path = path if path is not None else places_pricing_path()
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        return PlacesPricing.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        logger.warning(f"Google Places fiyat dosyası okunamadı: {file_path}: {error}")
        return None


@dataclass(frozen=True)
class PlacesCostEstimate:
    """Bir Otopilot çalıştırmasında Google Places'in en kötü durumdaki kullanımı/maliyeti."""

    keyword_count: int
    district_count: int  # ilçe listesi olmayan ilde 1
    pending_searches: int  # henüz bitmemiş (kelime × ilçe) araması
    max_requests: int  # pending_searches × MAX_PAGES_PER_QUERY
    used_this_month: int
    monthly_limit: int  # etkin sınır (sabit tavanla kırpılmış)
    planned_requests: int  # min(max_requests, kalan aylık hak)
    pricing: Optional[PlacesPricing]
    billable_requests: Optional[int]  # None = fiyat bilinmiyor
    estimated_usd: Optional[float]

    @property
    def remaining_this_month(self) -> int:
        return max(self.monthly_limit - self.used_this_month, 0)

    @property
    def pages_per_search(self) -> int:
        return MAX_PAGES_PER_QUERY

    @property
    def cost_known(self) -> bool:
        return self.pricing is not None

    @property
    def within_free_quota(self) -> bool:
        """Yalnızca fiyat biliniyorsa anlamlı; bilinmiyorsa HER ZAMAN False (ücretsiz denmez)."""
        return self.cost_known and self.billable_requests == 0

    @property
    def needs_warning(self) -> bool:
        """Kırmızı uyarı: para çıkabilir YA DA maliyet hesaplanamadı."""
        return self.planned_requests > 0 and not self.within_free_quota


def estimate_places_cost(
    *,
    keyword_count: int,
    district_count: int,
    pending_searches: int,
    used_this_month: int,
    monthly_limit: int,
    pricing: Optional[PlacesPricing],
) -> PlacesCostEstimate:
    """`pending_searches` = henüz tamamlanmamış (kelime × ilçe) arama sayısı.

    En fazla istek = `pending_searches × 3 sayfa`; gerçekte aylık kalan hakla sınırlıdır.
    Ücretli istek sayısı: bu ayki sayaç + planlanan istek ücretsiz kotayı ne kadar aşıyorsa o kadar
    (kota zaten aşılmışsa planlananın tamamı). Bu yalnızca UYGULAMANIN sayacıdır; Google hesabınızda
    başka bir yerden yapılan Text Search Enterprise istekleri de aynı ücretsiz kotayı tüketir.
    """
    max_requests = pending_searches * MAX_PAGES_PER_QUERY
    remaining = max(monthly_limit - used_this_month, 0)
    planned = min(max_requests, remaining)

    billable: Optional[int] = None
    cost: Optional[float] = None
    if pricing is not None:
        free_left = max(pricing.free_monthly_requests - used_this_month, 0)
        billable = max(planned - free_left, 0)
        cost = billable / 1000 * pricing.usd_per_1000_requests
    return PlacesCostEstimate(
        keyword_count=keyword_count,
        district_count=district_count,
        pending_searches=pending_searches,
        max_requests=max_requests,
        used_this_month=used_this_month,
        monthly_limit=monthly_limit,
        planned_requests=planned,
        pricing=pricing,
        billable_requests=billable,
        estimated_usd=cost,
    )
