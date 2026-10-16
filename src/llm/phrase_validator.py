"""LLM'den gelen "şirket odağı" ifadesini doğrular: iki ayrı kontrol.

1. `validate_focus_phrase` — ifade şirketin KENDİ metninden mi geliyor?
   İfade yalnızca şirketin ne yaptığını anlatır (örn. "endüstriyel otomasyon çözümleri").
   Aday hakkında tek kelime geçemez ve içindeki kelimeler şirketin tanıtım metninden
   gelmek zorundadır — uydurulmuş bir ifade buradan geçemez (CLAUDE.md kural 1).
2. `phrase_matches_tags` — ifade, EŞLEŞEN etiketlerden birini mi anlatıyor?
   Mail cümlesi "ilgi alanlarımla örtüştüğü için" diyor; bu örtüşme eşleşen etiketlerden
   gelir. İfade başka bir alanı anlatıyorsa (eşleşme "ag-yonetimi" ama ifade "muhasebe
   yazılımı çözümleri") cümle yalan olur. Birinci kontrol bunu yakalayamaz: metinden
   gelip gelmediğine bakar, hangi alanı anlattığına bakmaz.

Geçmeyen ifade atılır ve mail ifadesiz haliyle (etiket adlarıyla) kurulur.

Karşılaştırmalar `fold_turkish` ile yapılır (Türkçe karakter kullanmadan yazılmış şirket
metinleri de eşleşsin diye), ama DÖNEN ifade her zaman orijinal yazımıyla döner.
"""
import logging
from typing import Optional

from src.core.text import fold_turkish
from src.llm.keyword_provider import KeywordProvider

logger = logging.getLogger(__name__)

MIN_WORDS = 2
MAX_WORDS = 6

# Anlamlı kelime sayılmak için gereken en az harf sayısı ("ve", "ile" gibi bağlaçlar sayılmaz).
MEANINGFUL_MIN_LENGTH = 3
# Kök eşleşmesi: Türkçe ekleri kovalamamak için kelimelerin ilk 5 harfi karşılaştırılır.
STEM_LENGTH = 5
# Anlamlı kelimelerin en az %60'ı şirketin metninde geçmeli (6/10, tam sayıyla karşılaştırılır).
SOURCE_RATIO_NUMERATOR = 6
SOURCE_RATIO_DENOMINATOR = 10

_PUNCTUATION = ".,;:!?()[]{}\"'`…-–—/\\|*"

# Birinci şahıs: tam kelime olarak yasak olanlar. Karşılaştırma katlanmış metinle
# yapıldığı için listeler de katlanır (kaynakta doğru yazım kalsın diye burada çevrilir).
_FIRST_PERSON_WORDS = frozenset(
    fold_turkish(word)
    for word in ("ben", "benim", "bana", "beni", "bende", "benden", "bizim", "bize", "bizi")
)
# Kök olarak yasak olanlar: bu köklerle BAŞLAYAN her kelime reddedilir
# ("deneyimimle", "tecrübemi", "kariyerimde", "yeteneklerim").
_FIRST_PERSON_ROOTS = tuple(
    fold_turkish(root) for root in ("deneyim", "tecrübe", "kariyer", "yetenek")
)

# İyelik eki: bu eklerle biten kelime, kökü şirketin metninde geçmiyorsa reddedilir.
# Koşul şart: "yazılım", "tasarım", "yönetim", "bilişim", "eğitim" de bu ekle biter ama
# şirketin metninde geçtikleri için geçerlidir; uydurulmuş "yeteneğim" geçmediği için düşer.
# Katlama sonrası "ım" da "im" olur, bu yüzden tek ek kalır.
_POSSESSIVE_SUFFIXES = tuple(sorted({fold_turkish(suffix) for suffix in ("im", "ım")}))
_POSSESSIVE_MIN_LENGTH = 4


def _stem(word: str) -> str:
    return word[:STEM_LENGTH]


def _clean_word(word: str) -> str:
    return word.strip(_PUNCTUATION)


def _rejected(reason: str, phrase: str) -> Optional[str]:
    """Red sebebini loglar ve None döndürür (çağıran `return _rejected(...)` yazabilsin)."""
    logger.debug(f"Şirket odağı ifadesi reddedildi ({reason}): {phrase!r}")
    return None


def _first_person_reason(words: list[str], about: str) -> Optional[str]:
    """Birinci şahıs ihlali varsa sebebini döndürür, yoksa None."""
    for word in words:
        if word in _FIRST_PERSON_WORDS:
            return f"birinci şahıs kelimesi '{word}'"
        for root in _FIRST_PERSON_ROOTS:
            if word.startswith(root):
                return f"'{root}' kökünden gelen kelime '{word}'"
        ends_with_possessive = word.endswith(_POSSESSIVE_SUFFIXES)
        if (
            ends_with_possessive
            and len(word) >= _POSSESSIVE_MIN_LENGTH
            and _stem(word) not in about
        ):
            return f"iyelik ekiyle biten ve şirket metninde geçmeyen kelime '{word}'"
    return None


def _is_sourced(words: list[str], about: str) -> bool:
    """Anlamlı kelimelerin en az %60'ı şirket metninde (kök olarak) geçiyor mu?"""
    meaningful = [word for word in words if len(word) >= MEANINGFUL_MIN_LENGTH]
    if not meaningful:
        return False
    found = sum(1 for word in meaningful if _stem(word) in about)
    return found * SOURCE_RATIO_DENOMINATOR >= len(meaningful) * SOURCE_RATIO_NUMERATOR


def validate_focus_phrase(phrase: Optional[str], about_text: str) -> Optional[str]:
    """İfadeyi doğrular; geçerse temizlenmiş halini, geçmezse None döndürür.

    Args:
        phrase: Sağlayıcıdan gelen ifade; None olabilir.
        about_text: Şirketin kendi tanıtım metni; ifadenin kaynağı budur.
    """
    if phrase is None:
        return None

    stripped = phrase.strip()
    if not stripped:
        return _rejected("boş", phrase)
    if "\n" in stripped or "\r" in stripped:
        return _rejected("birden fazla satır", phrase)
    if any(char.isdigit() for char in stripped):
        return _rejected("rakam içeriyor", phrase)

    raw_words = stripped.split()
    if not MIN_WORDS <= len(raw_words) <= MAX_WORDS:
        return _rejected(f"{len(raw_words)} kelime ({MIN_WORDS}-{MAX_WORDS} olmalı)", phrase)

    about = fold_turkish(about_text or "")
    words = [word for word in (_clean_word(fold_turkish(w)) for w in raw_words) if word]
    if not words:
        return _rejected("harf içermiyor", phrase)

    reason = _first_person_reason(words, about)
    if reason:
        return _rejected(reason, phrase)

    if not _is_sourced(words, about):
        return _rejected("kelimeler şirket metninde geçmiyor", phrase)

    cleaned = " ".join(raw_words)
    logger.debug(f"Şirket odağı ifadesi doğrulandı: {cleaned!r}")
    return cleaned


def phrase_matches_tags(phrase: str, matched_tags: list[str]) -> bool:
    """İfade, eşleşen etiketlerden en az birini anlatıyor mu?

    İfade anahtar kelime sağlayıcısına verilir (ağa çıkmaz, metin üretmez) ve bulduğu
    etiketlerin `matched_tags` ile kesişimine bakılır. İfade hiçbir etikete düşmüyorsa da
    `False` döner: bilinçli olarak sıkı bir kural, çünkü mailde yanlış bir örtüşme iddiası
    kalmasındansa ifadenin hiç kullanılmaması yeğdir.

    Args:
        phrase: Doğrulamayı geçmiş ifade.
        matched_tags: CV'de eşleşen etiketler; boşsa ifade zaten kullanılmaz.
    """
    if not phrase or not matched_tags:
        return False

    phrase_tags = KeywordProvider().classify_company("", phrase)
    return bool(set(phrase_tags) & set(matched_tags))
