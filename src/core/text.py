"""Türkçe metin yardımcıları.

Python'un yerleşik büyük/küçük harf fonksiyonları Türkçede yanlış sonuç verir:
`"İş".lower()` "i" + birleşik nokta bırakır, `"iş".capitalize()` ise noktasız
"Iş" üretir. Aşağıdaki iki fonksiyon I/ı ve İ/i çiftlerini önce çevirir.

Ayrıca anahtar kelime aramaları için kelime sınırına saygı duyan desen üreticisi
(`build_word_pattern`) burada durur: `src/llm/keyword_provider.py` ve ileride
`src/inbox/classifier.py` aynı mantığı kullanır.

DİKKAT: `fold_turkish` YALNIZCA eşleştirme içindir. Dışarı giden hiçbir metin (mail, CV,
şirket odağı ifadesi) katlanmaz; kullanıcıya ve şirkete her zaman orijinal yazım gider.
"""
import re
from typing import Sequence

# Önce eşleşmeyen harf çiftleri çevrilir, sonra kalanı için standart lower()/upper() kullanılır.
_TURKISH_LOWER_MAP = str.maketrans({"I": "ı", "İ": "i"})
_TURKISH_UPPER_MAP = str.maketrans({"i": "İ", "ı": "I"})

# Türkçe karakter kullanmadan yazılmış metinlerde de eşleşme sağlamak için
# ("acik pozisyonumuz bulunmamaktadir" = "açık pozisyonumuz bulunmamaktadır").
_TURKISH_FOLD_MAP = str.maketrans(
    {"ğ": "g", "ü": "u", "ş": "s", "ı": "i", "ö": "o", "ç": "c"}
)


def turkish_lower(text: str) -> str:
    """Türkçe kurallarıyla küçük harfe çevirir ('İş' -> 'iş', 'IŞIK' -> 'ışık')."""
    return text.translate(_TURKISH_LOWER_MAP).lower()


def turkish_capitalize_first(text: str) -> str:
    """Yalnızca ilk harfi Türkçe kurallarıyla büyütür ('iş' -> 'İş'); gerisine dokunmaz."""
    if not text:
        return text
    return text[0].translate(_TURKISH_UPPER_MAP).upper() + text[1:]


def fold_turkish(text: str) -> str:
    """Metni karşılaştırmaya hazır hale getirir: küçük harf + Türkçe karakterleri sadeleştirir.

    'Açık Ağ Çözümü' -> 'acik ag cozumu'. Böylece Türkçe karakter kullanmadan yazılmış
    metinler de eşleşir (iş maillerinde ve şirket sitelerinde çok yaygın).

    SADECE eşleştirme/arama için kullanılır; kullanıcıya veya şirkete gidecek hiçbir
    metin bu fonksiyondan geçirilmez. Aynı metne iki kez uygulanması bir şeyi değiştirmez.
    """
    return turkish_lower(text).translate(_TURKISH_FOLD_MAP)


def build_word_pattern(keywords: Sequence[str]) -> re.Pattern[str]:
    """Verilen anahtar kelimeleri kelime sınırına saygılı tek bir desene derler.

    Anahtar kelimeler `fold_turkish` ile katlanır; bu yüzden desen, `matches_any` ve
    `count_distinct_matches` ile (ya da elle katlanmış metinle) kullanılmalıdır.

    `\\b` yerine `(?<!\\w)`/`(?!\\w)` kullanılır; ikisi de Unicode modda Türkçe harfleri
    kapsar, ama lookaround "c++" gibi harf olmayan karakterle biten kelimelerde de çalışır.
    Böylece "ağ" deseni "bağlantı" / "sağlam" / "ağır" içinde eşleşmez; "ağ yönetimi",
    "(ağ)" ve "ağ," içinde eşleşir.

    Kelimeler uzundan kısaya sıralanır: alternatif desende ilk eşleşen kazandığı için
    "açık pozisyonumuz bulunmamaktadır" kısa bir parçasından önce denenir.

    Args:
        keywords: Küçük harfle yazılmış anahtar kelimeler; çok kelimeli ifade olabilir.

    Raises:
        ValueError: Liste boşsa (boş alternatif her şeyle eşleşirdi).
    """
    cleaned = [fold_turkish(kw).strip() for kw in keywords if kw.strip()]
    if not cleaned:
        raise ValueError("build_word_pattern boş anahtar kelime listesiyle çağrıldı")

    ordered = sorted(set(cleaned), key=lambda kw: (-len(kw), kw))
    alternatives = "|".join(re.escape(kw) for kw in ordered)
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")


def matches_any(pattern: re.Pattern[str], text: str) -> bool:
    """Desendeki anahtar kelimelerden biri metinde geçiyor mu? Metin katlanarak aranır."""
    return pattern.search(fold_turkish(text)) is not None


def count_distinct_matches(pattern: re.Pattern[str], text: str) -> int:
    """Desenin metinde kaç FARKLI anahtar kelimeyle eşleştiğini sayar.

    Metin `fold_turkish` ile katlanarak aranır. Aynı kelimenin on kez geçmesi skoru
    şişirmesin diye tekrarlar sayılmaz.
    """
    return len({match.group(0) for match in pattern.finditer(fold_turkish(text))})


#: `datetime.weekday()` (0=Pazartesi ... 6=Pazar) ile aynı sırada.
TURKISH_WEEKDAYS = (
    "Pazartesi",
    "Salı",
    "Çarşamba",
    "Perşembe",
    "Cuma",
    "Cumartesi",
    "Pazar",
)

#: (saniye, Türkçe birim) — büyükten küçüğe. `format_duration_tr` bekleme sürelerini
#: kullanıcıya okunur biçimde göstermek için kullanır (rate_limiter, worker).
_DURATION_UNITS: tuple[tuple[int, str], ...] = (
    (86400, "gün"),
    (3600, "saat"),
    (60, "dakika"),
    (1, "saniye"),
)


def format_duration_tr(seconds: float) -> str:
    """Saniyeyi 'X gün Y saat' / 'X saat Y dakika' / 'X dakika Y saniye' gibi okunur bir
    Türkçe süreye çevirir; en büyük iki birim gösterilir (örn. gün varsa saniye basılmaz).

    Negatif değerler 0 kabul edilir (geçmiş bir zaman "0 saniye" olarak gösterilir).
    """
    total = max(0, round(seconds))
    for index, (unit_seconds, unit_name) in enumerate(_DURATION_UNITS):
        is_last_unit = index == len(_DURATION_UNITS) - 1
        if total >= unit_seconds or is_last_unit:
            value = total // unit_seconds
            remainder = total - value * unit_seconds
            result = f"{value} {unit_name}"
            if not is_last_unit:
                next_seconds, next_name = _DURATION_UNITS[index + 1]
                result += f" {remainder // next_seconds} {next_name}"
            return result
    return "0 saniye"  # pragma: no cover - _DURATION_UNITS her zaman en az bir birim içerir
