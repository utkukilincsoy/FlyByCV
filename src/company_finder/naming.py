"""Şirket adı çıkarma — PLAN.md Adım 10.6e/10.6f (`derive_company_name`).

Adı kaynağı (OSM `name` etiketi) vermeyen adaylarda `Company.name`, şirketin KENDİ sitesinin
başlığından üretilir. Sayfa başlıkları çoğu zaman "Anasayfa | Xyz Mimarlık" ya da "Xyz'e Hoş
Geldiniz" gibi kirli olur; mail şablonundaki `{sirket_adi}` bu haliyle kullanılırsa utandırıcı
olur, bu yüzden burada temizlenir. Saf fonksiyon: ağ/veritabanı yok.
"""
import re
from typing import Optional

from bs4 import BeautifulSoup

from src.core.text import fold_turkish

MAX_NAME_LENGTH = 60

_SEPARATORS = re.compile(r"\s+[|\-–—•·:»«/]+\s+|\s*\|\s*|\s*::\s*")

#: Tek başına anlamsız başlık parçaları (`fold_turkish` ile katlanmış biçimde).
_GENERIC_PIECES = frozenset(
    {
        "anasayfa", "ana sayfa", "home", "home page", "homepage", "index", "main",
        "hos geldiniz", "hosgeldiniz", "welcome", "hos geldin", "resmi web sitesi",
        "official website", "official site", "web sitesi", "website", "giris",
    }
)

#: Adın başına/sonuna yapışmış kalıplar (katlanmış metinde aranır, ama orijinalden kesilir).
_PREFIX_PATTERNS = [
    re.compile(r"^(welcome to|hos ?geldiniz[,:!]?)\s+"),
    re.compile(r"^(anasayfa|ana sayfa|home)\s*[-–—:]?\s+"),
]
_SUFFIX_PATTERNS = [
    # "Xyz'e Hoş Geldiniz" (kesme işareti ZORUNLU: "Zone Hoş Geldiniz" adı bozulmasın)
    re.compile(r"['’](?:y?[ea]|n?[iu]n)\s+(?:web sitesine\s+)?hos ?geldiniz[!.]*$"),
    re.compile(r"\s+(?:web )?sitesine\s+hos ?geldiniz[!.]*$"),
    re.compile(r"\s+hos ?geldiniz[!.]*$"),
    re.compile(r"\s+(anasayfa|ana sayfa|home(page)?)$"),
]

#: Sonundaki nokta ad'ın parçası olan kısaltmalar (`fold_turkish` ile katlanmış, noktasız).
#: "Xyz Ltd. Şti." -> sondaki nokta KALIR; "Xyz Mimarlık." -> nokta atılır. Noktalı kısaltmalar
#: ("A.Ş.", "T.C.") ayrıca "içinde nokta var" kuralıyla korunur.
_ABBREVIATIONS = frozenset({"ltd", "sti", "san", "tic", "inc", "co", "corp", "llc", "ltd.sti"})


def derive_company_name(html: str, domain: str) -> str:
    """Sayfa HTML'inden (`og:site_name`, yoksa `<title>`) temiz bir şirket adı üretir.

    Adımlar: ayraçlarla (` | `, ` - `, ` – `...) parçala; "Anasayfa", "Home", "Hoş Geldiniz",
    "Welcome" gibi anlamsız parçaları ve yapışık kalıpları at; geriye birden çok parça kalırsa
    alan adıyla örtüşeni, yoksa ilkini seç. Sonuç boşsa, 2 karakterden kısaysa ya da
    `MAX_NAME_LENGTH`'i aşıyorsa alan adından üretilir (`xyzmimarlik.com` -> `Xyzmimarlik`).
    """
    raw = _raw_title(html)
    cleaned = _clean_title(raw, domain) if raw else ""
    if 2 <= len(cleaned) <= MAX_NAME_LENGTH:
        return cleaned
    return name_from_domain(domain)


def name_from_domain(domain: str) -> str:
    """`xyzmimarlik.com` -> `Xyzmimarlik`; `xyz-mimarlik.com.tr` -> `Xyz Mimarlik`."""
    host = domain.strip().lower().removeprefix("www.")
    label = host.split(".")[0] if host else ""
    words = [w for w in re.split(r"[-_]+", label) if w]
    return " ".join(w[:1].upper() + w[1:] for w in words)


def _raw_title(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"property": "og:site_name"})
    if meta is not None and (meta.get("content") or "").strip():
        return str(meta["content"]).strip()
    if soup.title is not None:
        return soup.title.get_text(" ", strip=True)
    return ""


def _clean_title(raw: str, domain: str) -> str:
    pieces = [_strip_patterns(p) for p in _SEPARATORS.split(raw.replace("\xa0", " "))]
    kept = [p for p in pieces if p and fold_turkish(p).strip(" !.,:-") not in _GENERIC_PIECES]
    if not kept:
        return ""
    if len(kept) == 1:
        return kept[0]
    return _pick_by_domain(kept, domain) or kept[0]


def _strip_patterns(piece: str) -> str:
    """Parçanın başındaki/sonundaki "Hoş Geldiniz" benzeri kalıpları keser."""
    piece = piece.strip()
    for _ in range(2):  # kalıplar üst üste gelebilir ("Anasayfa Xyz'e Hoş Geldiniz")
        folded = fold_turkish(piece)
        for pattern in _PREFIX_PATTERNS:
            match = pattern.match(folded)
            if match and match.end() < len(folded):
                piece = piece[match.end():].strip()
                folded = fold_turkish(piece)
        for pattern in _SUFFIX_PATTERNS:
            match = pattern.search(folded)
            if match and match.start() > 0:
                piece = piece[: match.start()].strip()
                folded = fold_turkish(piece)
    return _trim_edges(piece)


def _ends_with_abbreviation(piece: str) -> bool:
    words = piece.split()
    if not words:
        return False
    last = fold_turkish(words[-1]).rstrip(".")
    return last in _ABBREVIATIONS or "." in last


def _trim_edges(piece: str) -> str:
    """Uçlardaki ayraç/noktalama işaretlerini atar; ama kısaltmanın sonundaki noktayı korur
    ("Xyz Ltd. Şti." -> "Xyz Ltd. Şti.", "Xyz Mimarlık." -> "Xyz Mimarlık")."""
    piece = piece.lstrip(" -–—|:!.,'’")
    while True:
        piece = piece.rstrip(" -–—|:!,'’")
        if piece.endswith(".") and not _ends_with_abbreviation(piece):
            piece = piece[:-1]
            continue
        return piece


def _pick_by_domain(pieces: list[str], domain: str) -> Optional[str]:
    """Alan adı etiketi (`xyzmimarlik`) ile harf harf örtüşen ilk parçayı döner."""
    label = re.sub(r"[^a-z0-9]", "", fold_turkish(domain.split(".")[0])) if domain else ""
    if len(label) < 3:
        return None
    for piece in pieces:
        if label in re.sub(r"[^a-z0-9]", "", fold_turkish(piece)):
            return piece
    return None
