"""src/company_finder/places_cost.py testleri (PLAN.md 10.6h): fiyat dosyası + maliyet hesabı.
Ağ/veritabanı yok."""
import json
from datetime import date

import pytest

from src.company_finder.places_cost import PlacesPricing, estimate_places_cost, load_places_pricing

PRICING = PlacesPricing(
    last_checked=date(2026, 9, 30),
    source_url="https://example.com/pricing",
    currency="USD",
    sku="Text Search Enterprise",
    free_monthly_requests=1000,
    usd_per_1000_requests=35.0,
)


def _estimate(**overrides):
    params = dict(
        keyword_count=5,
        district_count=25,
        pending_searches=125,
        used_this_month=0,
        monthly_limit=900,
        pricing=PRICING,
    )
    params.update(overrides)
    return estimate_places_cost(**params)


# --- fiyat dosyası -----------------------------------------------------------------------------


def test_bundled_pricing_file_loads_with_check_date_and_enterprise_price():
    pricing = load_places_pricing()

    assert pricing is not None
    assert pricing.last_checked == date(2026, 9, 30)
    assert pricing.free_monthly_requests == 1000
    assert pricing.usd_per_1000_requests == 35.0
    assert pricing.note  # "fiyat değişebilir" notu boş bırakılmaz


@pytest.mark.parametrize(
    "content",
    ["{bozuk json", "[]", json.dumps({"last_checked": "yanlis-tarih"}), json.dumps({})],
    ids=["bozuk_json", "liste", "yanlis_tarih", "bos"],
)
def test_broken_pricing_file_returns_none_instead_of_crashing(tmp_path, content):
    path = tmp_path / "pricing.json"
    path.write_text(content, encoding="utf-8")

    assert load_places_pricing(path) is None


def test_missing_pricing_file_returns_none(tmp_path):
    assert load_places_pricing(tmp_path / "yok.json") is None


def test_negative_price_is_rejected(tmp_path):
    data = PRICING.model_dump(mode="json")
    data["usd_per_1000_requests"] = -1
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert load_places_pricing(path) is None


# --- hesap -------------------------------------------------------------------------------------


def test_max_requests_is_searches_times_three_pages():
    estimate = _estimate()

    assert estimate.max_requests == 125 * 3  # 5 kelime × 25 ilçe × 3 sayfa


def test_planned_requests_limited_by_remaining_monthly_quota_and_within_free_quota():
    estimate = _estimate(used_this_month=12)

    assert estimate.remaining_this_month == 888
    assert estimate.planned_requests == 375  # 375 < 888: sınır devreye girmez
    assert estimate.within_free_quota  # 12 + 375 < 1000 ücretsiz
    assert estimate.estimated_usd == 0
    assert not estimate.needs_warning


def test_monthly_limit_caps_planned_requests():
    estimate = _estimate(pending_searches=500, used_this_month=100)  # 1500 > kalan 800

    assert estimate.max_requests == 1500
    assert estimate.planned_requests == 800


def test_exceeding_free_quota_estimates_enterprise_price_and_warns():
    # Sınır 2000'e çıkarılmış, 950 kullanılmış: 950 + 1050 = 2000 → ücretsiz 1000'i 1000 aşar.
    estimate = _estimate(pending_searches=400, used_this_month=950, monthly_limit=2000)

    assert estimate.planned_requests == 1050
    assert estimate.billable_requests == 1000
    assert estimate.estimated_usd == pytest.approx(35.0)  # 1000 istek × $35 / 1000
    assert not estimate.within_free_quota
    assert estimate.needs_warning


def test_quota_already_exhausted_makes_every_planned_request_billable():
    estimate = _estimate(pending_searches=10, used_this_month=1200, monthly_limit=2000)

    assert estimate.planned_requests == 30
    assert estimate.billable_requests == 30
    assert estimate.estimated_usd == pytest.approx(30 / 1000 * 35.0)


def test_unknown_pricing_is_never_reported_as_free():
    estimate = _estimate(pricing=None)

    assert not estimate.cost_known
    assert not estimate.within_free_quota
    assert estimate.estimated_usd is None
    assert estimate.needs_warning  # istek atılacak ama maliyet bilinmiyor → uyarı


def test_nothing_pending_or_no_quota_left_needs_no_warning():
    assert not _estimate(pending_searches=0, pricing=None).needs_warning
    assert not _estimate(used_this_month=900, pricing=None).needs_warning  # kalan 0 → istek yok
