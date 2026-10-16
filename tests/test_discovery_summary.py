"""src/company_finder/discovery_summary.py testleri (PLAN.md 10.6h "dürüst sonuç mesajı")."""
from src.company_finder.discovery_summary import (
    LOW_RESULT_THRESHOLD,
    DiscoveryTally,
    format_discovery_summary,
)


def test_summary_names_city_total_and_per_source_counts():
    tally = DiscoveryTally(city_name="Ankara")
    tally.record("teknokent", added=12, skipped=3)
    tally.record("osm", added=30, skipped=0)

    text = format_discovery_summary(tally)

    assert text.startswith("Keşif bitti: Ankara ilinde 42 yeni firma bulundu")
    assert "Teknokent rehberleri: 12" in text and "OpenStreetMap: 30" in text
    assert "3 aday zaten kayıtlıydı ya da eklenmeye uygun değildi" in text
    assert "Az firma" not in text  # 42 ≥ eşik


def test_low_result_without_google_suggests_google_key_or_csv():
    tally = DiscoveryTally(city_name="Bartın")
    tally.record("osm", added=LOW_RESULT_THRESHOLD - 1, skipped=0)

    text = format_discovery_summary(tally)

    assert "Bartın ilinde 19 yeni firma bulundu" in text
    assert "Google Places anahtarı" in text and "CSV" in text


def test_low_result_with_google_already_used_only_suggests_csv():
    tally = DiscoveryTally(city_name="Bartın")
    tally.record("osm", added=1, skipped=0)
    tally.record("google", added=2, skipped=0)

    text = format_discovery_summary(tally)

    assert "Google Places anahtarı" not in text  # zaten kullanıldı
    assert "CSV" in text


def test_zero_found_is_reported_honestly():
    tally = DiscoveryTally()
    tally.record("teknokent", added=0, skipped=5)

    text = format_discovery_summary(tally)

    assert text.startswith("Keşif bitti: 0 yeni firma bulundu")  # şehir yoksa "ilinde" yok
    assert "5 aday zaten kayıtlıydı" in text


def test_failures_are_listed_and_marked_for_retry():
    tally = DiscoveryTally(city_name="Ankara")
    tally.record("google", added=25, skipped=0)
    tally.record_failure("google", "Google Places (Çankaya: yazılım şirketi) (aylık istek sınırı doldu)")

    text = format_discovery_summary(tally)

    assert "yeniden denenecek" in text and "aylık istek sınırı doldu" in text


def test_duplicate_failure_reasons_are_listed_once():
    tally = DiscoveryTally()
    tally.record_failure("osm", "OpenStreetMap")
    tally.record_failure("osm", "OpenStreetMap")

    assert tally.failures == ["OpenStreetMap"]
