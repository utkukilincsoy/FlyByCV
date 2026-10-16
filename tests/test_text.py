import pytest

from src.core.text import (
    TURKISH_WEEKDAYS,
    build_word_pattern,
    count_distinct_matches,
    fold_turkish,
    format_duration_tr,
    matches_any,
    turkish_capitalize_first,
    turkish_lower,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("İş", "iş"),
        ("IŞIK", "ışık"),
        ("Staj", "staj"),
        ("STAJ", "staj"),
        ("", ""),
    ],
)
def test_turkish_lower(value, expected):
    """Türkçe küçük harf: İ -> i, I -> ı."""
    assert turkish_lower(value) == expected


def test_turkish_lower_leaves_no_combining_dot():
    """str.lower() 'İ' için birleşik nokta bırakır; bizim fonksiyon bırakmamalı."""
    assert len(turkish_lower("İş")) == 2
    assert "̇" not in turkish_lower("İş")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("iş", "İş"),
        ("ıslak", "Islak"),
        ("staj", "Staj"),
        ("", ""),
        ("iki kelime", "İki kelime"),
    ],
)
def test_turkish_capitalize_first(value, expected):
    """Yalnızca ilk harf büyür, gerisi olduğu gibi kalır."""
    assert turkish_capitalize_first(value) == expected


@pytest.mark.parametrize(
    "text",
    ["ağ yönetimi", "(ağ)", "ağ, sunucu", "kurumsal ağ", "ağ"],
)
def test_build_word_pattern_matches_whole_word(text):
    """Kelime tek başına geçtiğinde eşleşmeli (noktalama araya girse de)."""
    assert matches_any(build_word_pattern(["ağ"]), text)


@pytest.mark.parametrize("text", ["bağlantı", "sağlam", "ağır yük", "dağ", "ağaç"])
def test_build_word_pattern_ignores_substrings(text):
    """Kelime başka bir kelimenin içinde geçiyorsa eşleşmemeli."""
    assert not matches_any(build_word_pattern(["ağ"]), text)


def test_build_word_pattern_matches_multiword_keyword():
    """Çok kelimeli ifade de aranabilmeli."""
    pattern = build_word_pattern(["makine öğrenmesi"])
    assert matches_any(pattern, "Şirket makine öğrenmesi üzerine çalışır")
    assert not matches_any(pattern, "makine bakımı")


def test_build_word_pattern_matches_keyword_with_symbols():
    """İçinde harf olmayan karakter bulunan ifadeler de eşleşmeli (ci/cd, ar-ge)."""
    pattern = build_word_pattern(["ci/cd", "ar-ge"])
    assert matches_any(pattern, "ci/cd süreçleri kuruyoruz")
    assert matches_any(pattern, "Ar-Ge merkezi")


def test_build_word_pattern_prefers_longest_keyword():
    """Uzun ifade kısa olanın önünde denenmeli (desen katlanmış biçimde eşleşir)."""
    pattern = build_word_pattern(["ağ", "ağ yönetimi"])
    match = pattern.search(fold_turkish("ağ yönetimi hizmeti"))
    assert match is not None and match.group(0) == "ag yonetimi"


def test_build_word_pattern_rejects_empty_list():
    """Boş liste her şeyle eşleşen bir desen üretirdi; hata vermeli."""
    with pytest.raises(ValueError, match="boş anahtar kelime"):
        build_word_pattern([])


def test_count_distinct_matches_counts_each_keyword_once():
    """Aynı kelimenin tekrarı skoru şişirmemeli."""
    pattern = build_word_pattern(["test", "qa"])
    assert count_distinct_matches(pattern, "test test test qa") == 2
    assert count_distinct_matches(pattern, "hiçbir şey") == 0


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Açık Ağ Çözümü", "acik ag cozumu"),
        ("ĞÜŞIÖÇ", "gusioc"),
        ("İstanbul", "istanbul"),
        ("Yazılım Geliştirme", "yazilim gelistirme"),
        ("already folded", "already folded"),
        ("", ""),
    ],
)
def test_fold_turkish(value, expected):
    """Türkçe karakterler eşleştirme için sadeleştirilmeli."""
    assert fold_turkish(value) == expected


def test_fold_turkish_is_idempotent():
    """İki kez katlamak bir şeyi değiştirmemeli (yardımcılar üst üste çağrılabiliyor)."""
    once = fold_turkish("Açık Pozisyonumuz Bulunmamaktadır")
    assert fold_turkish(once) == once


def test_pattern_matches_text_without_turkish_characters():
    """Türkçe karakter kullanmadan yazılmış metin de eşleşmeli."""
    pattern = build_word_pattern(["açık pozisyonumuz bulunmamaktadır"])
    assert matches_any(pattern, "Su an icin acik pozisyonumuz bulunmamaktadir.")
    assert matches_any(pattern, "Şu an için açık pozisyonumuz bulunmamaktadır.")


def test_folding_does_not_break_word_boundaries():
    """Katlama kelime sınırını gevşetmemeli: 'ağ' yine 'bağlantı' içinde eşleşmemeli."""
    pattern = build_word_pattern(["ağ"])
    assert matches_any(pattern, "kurumsal ag altyapısı")
    assert not matches_any(pattern, "bağlantı elemanları")
    assert not matches_any(pattern, "baglanti elemanlari")


def test_turkish_weekdays_matches_datetime_weekday_order():
    """datetime.weekday() 0=Pazartesi...6=Pazar döner; sıra bununla uyumlu olmalı."""
    assert TURKISH_WEEKDAYS[0] == "Pazartesi"
    assert TURKISH_WEEKDAYS[6] == "Pazar"
    assert len(TURKISH_WEEKDAYS) == 7


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0 saniye"),
        (30, "30 saniye"),
        (59, "59 saniye"),
        (60, "1 dakika 0 saniye"),
        (750, "12 dakika 30 saniye"),
        (3600, "1 saat 0 dakika"),
        (6480, "1 saat 48 dakika"),
        (48000, "13 saat 20 dakika"),
        (86400, "1 gün 0 saat"),
        (162000, "1 gün 21 saat"),
        (223200, "2 gün 14 saat"),
        (-5, "0 saniye"),
    ],
)
def test_format_duration_tr(seconds, expected):
    assert format_duration_tr(seconds) == expected
