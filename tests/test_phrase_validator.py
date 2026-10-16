import pytest

from src.llm.phrase_validator import phrase_matches_tags, validate_focus_phrase

ABOUT = (
    "Endüstriyel otomasyon alanında PLC ve SCADA tabanlı yazılım çözümleri üretiyoruz. "
    "Üretim hattı için robotik sistemler kuruyor, bakım ve eğitim hizmeti veriyoruz."
)


@pytest.mark.parametrize(
    "phrase",
    [
        "endüstriyel otomasyon çözümleri",
        "otomasyon çözümleri",
        "robotik üretim hattı sistemleri",
        # İyelik ekiyle biten ama şirketin metninde geçen kelimeler geçerli olmalı.
        "yazılım çözümleri",
        "endüstriyel yazılım ve otomasyon",
    ],
)
def test_valid_phrases_pass(phrase):
    """Şirketin kendi metninden gelen 2-6 kelimelik ifade geçmeli."""
    assert validate_focus_phrase(phrase, ABOUT) == phrase


def test_extra_whitespace_is_cleaned():
    """Baştaki/sondaki ve aradaki fazla boşluk temizlenmeli."""
    assert validate_focus_phrase("  otomasyon   çözümleri  ", ABOUT) == "otomasyon çözümleri"


@pytest.mark.parametrize("phrase", [None, "", "   ", "\t"])
def test_empty_phrase_rejected(phrase):
    """Boş ya da None ifade reddedilmeli."""
    assert validate_focus_phrase(phrase, ABOUT) is None


def test_single_word_rejected():
    """Tek kelime en az 2 kelime kuralını geçmemeli."""
    assert validate_focus_phrase("otomasyon", ABOUT) is None


def test_too_many_words_rejected():
    """7 kelimelik ifade en fazla 6 kelime kuralını geçmemeli."""
    phrase = "endüstriyel otomasyon ve robotik üretim hattı çözümleri"
    assert len(phrase.split()) == 7
    assert validate_focus_phrase(phrase, ABOUT) is None


def test_multiline_rejected():
    """İfade tek satır olmalı."""
    assert validate_focus_phrase("otomasyon\nçözümleri", ABOUT) is None


def test_digits_rejected():
    """Rakam içeren ifade reddedilmeli (uydurma tarih/sayı riski)."""
    assert validate_focus_phrase("30 yıllık otomasyon", ABOUT) is None


def test_punctuation_only_rejected():
    """Harf içermeyen ifade reddedilmeli."""
    assert validate_focus_phrase("--- ...", ABOUT) is None


@pytest.mark.parametrize(
    "phrase",
    ["benim otomasyon çözümlerim", "bizim otomasyon çözümleri", "bana otomasyon"],
)
def test_first_person_word_rejected(phrase):
    """Birinci şahıs kelimesi içeren ifade reddedilmeli."""
    assert validate_focus_phrase(phrase, ABOUT) is None


@pytest.mark.parametrize(
    "phrase",
    [
        "otomasyon deneyimlerimle",
        "otomasyon tecrübemi",
        "otomasyon kariyerimde",
        "otomasyon yeteneklerim",
    ],
)
def test_first_person_root_rejected(phrase):
    """deneyim / tecrübe / kariyer / yetenek kökünden gelen kelimeler reddedilmeli."""
    assert validate_focus_phrase(phrase, ABOUT) is None


def test_possessive_suffix_rejected_when_not_in_about_text():
    """Şirket metninde geçmeyen, iyelik ekiyle biten kelime reddedilmeli."""
    assert validate_focus_phrase("otomasyon yeteneğim", ABOUT) is None


def test_possessive_suffix_allowed_when_in_about_text():
    """'yazılım' ve 'eğitim' şirket metninde geçtiği için ek kuralına takılmamalı."""
    assert validate_focus_phrase("yazılım ve eğitim", ABOUT) == "yazılım ve eğitim"


def test_unsourced_phrase_rejected():
    """Kelimeleri şirket metninde geçmeyen ifade reddedilmeli (uydurma)."""
    assert validate_focus_phrase("kurumsal muhasebe paketleri", ABOUT) is None


def test_partially_sourced_phrase_rejected():
    """Anlamlı kelimelerin yarısı geçiyorsa %60 kuralı düşmeli."""
    assert validate_focus_phrase("bulut tabanlı muhasebe paketleri", ABOUT) is None


def test_empty_about_text_rejects_everything():
    """Şirket metni yoksa doğrulanacak kaynak da yok."""
    assert validate_focus_phrase("otomasyon çözümleri", "") is None


def test_stopwords_do_not_count_toward_source_ratio():
    """'ve' gibi kısa kelimeler oranı bozmamalı."""
    assert validate_focus_phrase("otomasyon ve robotik", ABOUT) == "otomasyon ve robotik"


def test_returned_phrase_keeps_turkish_characters():
    """Karşılaştırma katlanmış metinle yapılır ama dönen ifade orijinal yazımını korumalı."""
    phrase = "endüstriyel yazılım çözümleri"
    result = validate_focus_phrase(phrase, ABOUT)
    assert result == phrase
    assert "ü" in result and "ı" in result and "ö" in result


def test_phrase_without_turkish_characters_matches_folded_about_text():
    """Türkçe karakter kullanmadan yazılmış ifade, Türkçe karakterli metinle eşleşmeli."""
    assert validate_focus_phrase("yazilim cozumleri", ABOUT) == "yazilim cozumleri"


def test_about_text_without_turkish_characters_accepts_turkish_phrase():
    """Şirket metni Türkçe karakter kullanmadan yazılmışsa da doğrulama çalışmalı."""
    about = "Endustriyel otomasyon ve yazilim cozumleri uretiyoruz."
    assert validate_focus_phrase("endüstriyel otomasyon", about) == "endüstriyel otomasyon"


def test_possessive_rule_uses_folded_comparison():
    """'yazılım' şirket metninde karaktersiz yazılmış olsa da ek kuralına takılmamalı."""
    about = "Kurumsal yazilim cozumleri uretiyoruz."
    assert validate_focus_phrase("kurumsal yazılım", about) == "kurumsal yazılım"


# --- İfade eşleşen etiketle ilgili mi? ---


def test_phrase_matching_one_of_the_tags_passes():
    """İfade eşleşen etiketlerden birini anlatıyorsa geçmeli."""
    assert phrase_matches_tags("kurumsal ağ altyapısı", ["ag-yonetimi", "backend"]) is True


def test_phrase_about_another_field_fails():
    """Eşleşme 'ag-yonetimi' ama ifade başka bir alanı anlatıyorsa kullanılmamalı."""
    assert phrase_matches_tags("mobil uygulama geliştirme", ["ag-yonetimi"]) is False


@pytest.mark.parametrize(
    "phrase", ["muhasebe yazılımı çözümleri", "müşteri memnuniyeti odaklı hizmet"]
)
def test_phrase_matching_no_tag_at_all_fails(phrase):
    """İfade hiçbir etikete düşmüyorsa (emin olamıyorsak) kullanılmamalı."""
    assert phrase_matches_tags(phrase, ["ag-yonetimi"]) is False


@pytest.mark.parametrize("matched_tags", [[], ["bulut-devops"]])
def test_empty_phrase_fails(matched_tags):
    assert phrase_matches_tags("", matched_tags) is False


def test_empty_matched_tags_fails():
    """Eşleşen etiket yoksa ifade zaten maile girmiyor."""
    assert phrase_matches_tags("bulut bilişim çözümleri", []) is False


def test_phrase_matching_is_case_and_accent_insensitive():
    """Anahtar kelime eşleşmesi büyük harf ve Türkçe karakterden bağımsız çalışmalı."""
    assert phrase_matches_tags("Kurumsal AG Altyapisi", ["ag-yonetimi"]) is True
