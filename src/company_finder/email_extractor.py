"""Bir HTML sayfasından e-posta adresi çıkarma, temizleme, sıralama.

Ağa hiç çıkmaz — girdi olarak zaten indirilmiş HTML metni alır. `src/company_finder/
site_crawler.py` her ziyaret ettiği sayfa için `extract_emails()` çağırır, tüm sayfaların
sonuçlarını birleştirip `rank_emails()`/`best_email()` ile en iyi adresi seçer.

`normalize_email()` bu paketteki KANONİK e-posta temizleyicisidir: `csv_import.py` da aynı
fonksiyonu kullanır (aksi halde iki ayrı temizleyici birbirinden ayrışırdı). Sadece biçim
doğrular; web sayfalarına özgü gürültü elemesi (dosya adları, örnek alan adları, sistem
adresleri) burada, `extract_emails()` içinde ayrı bir katman olarak yapılır — CSV'den gelen bir
adres asla bu gürültü filtresinden geçmez, çünkü kullanıcı zaten kendi verdiği adresi biliyor.
"""
import re
from dataclasses import dataclass
from html import unescape as html_unescape
from typing import Iterable, Optional
from urllib.parse import unquote

from bs4 import BeautifulSoup

from src.company_finder.free_mail import is_free_mail_domain
from src.core.models import normalize_domain
from src.core.text import fold_turkish

#: Bir e-postanın bulunduğu sayfanın türüne göre öncelik (küçük = daha güvenilir).
#: iletişim/kariyer sayfası en güvenilir, "diğer" sayfa (ör. sitemap) en az güvenilir.
PAGE_RANK_PRIORITY = 0  # iletişim veya kariyer sayfası
PAGE_RANK_ABOUT = 1  # hakkımızda / kurumsal sayfası
PAGE_RANK_HOME = 2  # ana sayfa
PAGE_RANK_OTHER = 3  # diğer

#: Rol adresi değil (kişi adı gibi görünüyor) — is_role_address() ve _tier()'in üst sınırı.
_TIER_NOT_ROLE = 3

_EMAIL_PATTERN = re.compile(r"[^@\s]+@[a-z0-9][a-z0-9.-]*\.[a-z]{2,}")

# Sayfa metnindeki düz adresler için: yerel kısımda nokta/alt çizgi/tire/yüzde/artı, alan
# adında nokta/tire olabilir. Bu ilk kaba tarama; sonuç yine normalize_email()'den geçer.
_PLAIN_EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)

# "at"/"dot" yalnızca bu kalıpların İÇİNDE @/. sayılır; metindeki her "at" kelimesi değil
# (örn. "we met at noon" adres üretmemeli). Bu yüzden aşağıdaki tek regex hem yerel kısmı hem
# "at" işaretini hem de en az bir "dot" içeren alan adını BİR ARADA yakalar.
_AT_TOKEN = r"(?:\[at\]|\(at\)|\{at\}|\bat\b)"
_DOT_TOKEN = r"(?:\[dot\]|\(dot\)|\{dot\}|\[nokta\]|\(nokta\)|\bdot\b|\bnokta\b)"
_OBFUSCATED_PATTERN = re.compile(
    rf"([A-Za-z0-9][A-Za-z0-9._%+\-]*)\s*{_AT_TOKEN}\s*"
    rf"([A-Za-z0-9][A-Za-z0-9\-]*(?:\s*{_DOT_TOKEN}\s*[A-Za-z0-9\-]+)+)",
    re.IGNORECASE,
)

# Basamak 0: kariyer/İK. "ik"/"hr" yalnızca TAM eşleşmede sayılır (ikram@ gibi adları
# yanlışlıkla "ik" sanmamak için); diğerleri ön ek (prefix) eşleşir ("hrturkey" -> "hr" değil,
# ama "recruitment" -> "recruit" eşleşir çünkü "recruit" ön-ek-eşleşen bir kelime).
_TIER_KEYWORDS: list[tuple[int, frozenset[str]]] = [
    (
        0,
        frozenset(
            {
                "kariyer", "career", "careers", "ik", "hr", "insankaynaklari",
                "jobs", "basvuru", "isbasvuru", "recruit", "recruitment", "cv",
            }
        ),
    ),
    (1, frozenset({"info", "bilgi", "iletisim", "contact", "merhaba", "hello", "mail"})),
    (
        2,
        frozenset(
            {
                "destek", "support", "satis", "sales", "muhasebe", "kurumsal",
                "ofis", "office", "admin", "genel",
            }
        ),
    ),
]
#: Bu kelimeler yalnızca TAM eşleşmede sayılır (ön ek eşleşmesi "ikram" gibi adları bozar).
_EXACT_ONLY_KEYWORDS = frozenset({"ik", "hr", "cv"})

# Dosya adı uzantıları: "logo@2x.png" gibi görsel/betik dosya adları e-posta gibi görünür ama
# değildir. `.` olmadan tutulur, çünkü domain'in TLD'siyle (son parçasıyla) karşılaştırılır.
_ASSET_EXTENSIONS = frozenset(
    {"png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "css", "js", "woff", "woff2", "ttf"}
)

# Yer tutucu/örnek alan adları: TAM eşleşme. Bu depoda testler şirket alan adı olarak
# "acme.example.com" gibi *.example.com kullanıyor (bkz. CLAUDE.md, tests/); sonek eşleşmesi
# kullanılsaydı gerçek bir şirketin tüm test verisi "junk" sayılırdı. Bariz yer tutucu yalnızca
# alan adının TAMAMI birebir bunlardan biriyse ("info@example.com"), bir alt alan adı değil.
_JUNK_DOMAIN_EXACT = frozenset(
    {"example.com", "example.org", "example.net", "domain.com", "yourdomain.com",
     "site.com", "test.com", "localhost"}
)

# Üçüncü parti platform alan adları: bir sonek eşleşmesiyle karşılaştırılır, çünkü gerçek
# şirketler bu platformların ALT alan adını kullanır ("magaza.myshopify.com",
# "sentry.wixpress.com" izleme betiği gibi) — hiçbiri şirketin kendi iletişim adresi olamaz.
_JUNK_DOMAIN_PLATFORM_SUFFIXES = (
    "sentry.io", "w3.org", "schema.org", "wixpress.com", "wix.com",
    "squarespace.com", "shopify.com", "cloudflare.com",
)

# Sistem yerel adları ve şablon kalıntıları; ayırıcılar (-, _, .) atılmış hâliyle tutulur.
_JUNK_LOCAL_PARTS = frozenset(
    {
        "noreply", "donotreply", "mailerdaemon", "postmaster", "abuse", "bounce",
        "sentry", "ornek", "example", "adiniz", "yourname", "youremail",
        "webmaster", "hostmaster", "root", "wordpress",
    }
)

# KEP (Kayıtlı Elektronik Posta) adresleri: normal bir e-postadan KEP'e gönderim yapılamaz,
# mail geri döner. "hs01.kep.tr", "hs02.kep.tr" gibi tüm sağlayıcı alt alan adlarını kapsar.
_KEP_DOMAIN_SUFFIX = "kep.tr"


@dataclass(frozen=True)
class FoundEmail:
    email: str
    source_url: str
    is_role: bool
    on_company_domain: bool
    #: Bu adresin bulunduğu sayfanın türü (`PAGE_RANK_*`); sıralamada kullanılır.
    page_rank: int = PAGE_RANK_OTHER


def normalize_email(value: str) -> Optional[str]:
    """E-postayı sadeleştirir ve biçimini doğrular; geçersizse `None` döner.

    `mailto:` öneki, kırılmaz boşluk (NBSP), birden çok adresi ayıran `,`/`;`/boşluk/`|`
    (ilk geçerli olan alınır) temizlenir. Adres TÜMÜYLE küçültülür: `İ`→`i` özel olarak
    değiştirilip ardından `.lower()` uygulanır — `turkish_lower()` KULLANILMAZ, o "I" harfini
    "ı" yapar ve "info@ICLOUD.COM" gibi büyük harfli bir alan adını "ıcloud.com" yapıp
    eşleşmeyi bozardı (bkz. `src/core/models.normalize_domain` docstring'i, aynı tuzak).

    Bu sadece biçim doğrular; web sayfası gürültüsü (dosya adı, örnek alan adı) elemez —
    o filtre yalnızca `extract_emails()` içindedir, çünkü CSV/elle girilen bir adres zaten
    kullanıcının kendi verisidir.
    """
    if not value:
        return None
    cleaned = value.replace("\xa0", " ").strip()
    if cleaned.lower().startswith("mailto:"):
        cleaned = cleaned[len("mailto:") :]
    cleaned = unquote(cleaned)
    for token in re.split(r"[,;\s|]+", cleaned):
        candidate = token.replace("İ", "i").lower().strip(".")
        if _EMAIL_PATTERN.fullmatch(candidate):
            return candidate
    return None


def decode_cfemail(encoded: str) -> Optional[str]:
    """Cloudflare'in `data-cfemail` gizlemesini çözer (ilk bayt XOR anahtarı)."""
    try:
        raw = bytes.fromhex(encoded.strip())
    except ValueError:
        return None
    if len(raw) < 2:
        return None
    key = raw[0]
    decoded = bytes(byte ^ key for byte in raw[1:])
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _segments(email: str) -> list[str]:
    """Yerel kısmı ayırıcılardan (`.`/`_`/`-`/rakam) bölüp Türkçe katlar."""
    local = email.split("@", 1)[0]
    folded = fold_turkish(local)
    return [segment for segment in re.split(r"[._\-+0-9]+", folded) if segment]


def _tier(email: str) -> int:
    """En iyi (en düşük) rol basamağını döndürür; hiçbiri eşleşmezse `_TIER_NOT_ROLE`."""
    matched: list[int] = []
    for segment in _segments(email):
        for tier, keywords in _TIER_KEYWORDS:
            for keyword in keywords:
                if keyword in _EXACT_ONLY_KEYWORDS:
                    if segment == keyword:
                        matched.append(tier)
                elif segment == keyword or segment.startswith(keyword):
                    matched.append(tier)
    return min(matched, default=_TIER_NOT_ROLE)


def is_role_address(email: str) -> bool:
    """E-posta bir rol/kurumsal adresi mi (kariyer@, info@) yoksa kişi adı mı?"""
    return _tier(email) < _TIER_NOT_ROLE


def _is_asset_filename(domain: str) -> bool:
    _, _, tld = domain.rpartition(".")
    return tld in _ASSET_EXTENSIONS


def _is_junk_domain(domain: str) -> bool:
    if domain in _JUNK_DOMAIN_EXACT:
        return True
    return any(
        domain == suffix or domain.endswith(f".{suffix}")
        for suffix in _JUNK_DOMAIN_PLATFORM_SUFFIXES
    )


def _is_junk_local(local: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", fold_turkish(local))
    return compact in _JUNK_LOCAL_PARTS


def _is_kep_domain(domain: str) -> bool:
    """KEP adreslerine normal e-postadan gönderim yapılamaz (geri döner) — asla saklanmaz."""
    return domain == _KEP_DOMAIN_SUFFIX or domain.endswith(f".{_KEP_DOMAIN_SUFFIX}")


def _is_junk(email: str) -> bool:
    """Web sayfası taramasına özgü gürültü: dosya adı, örnek alan adı, sistem adresi, KEP."""
    local, _, domain = email.partition("@")
    return (
        _is_asset_filename(domain)
        or _is_junk_domain(domain)
        or _is_junk_local(local)
        or _is_kep_domain(domain)
    )


def _on_company_domain(email: str, company_domain: str) -> bool:
    """Adres şirketin kendi alan adında mı (ya da bir alt alan adında)?

    Ücretsiz mail sağlayıcıları asla "şirketin kendi alan adı" sayılmaz — bir şirketin gerçek
    alan adı bir kaza sonucu `gmail.com` olamaz, ama açıklık için burada da kontrol edilir.
    """
    _, _, raw_domain = email.rpartition("@")
    domain = normalize_domain(raw_domain)
    if is_free_mail_domain(domain):
        return False
    return domain == company_domain or domain.endswith(f".{company_domain}")


def extract_emails(
    html: str,
    *,
    company_domain: str,
    source_url: str,
    page_rank: int = PAGE_RANK_OTHER,
) -> list[FoundEmail]:
    """Bir sayfanın HTML'inden e-posta adreslerini çıkarır (aynı sayfada tekilleştirilmiş).

    `company_domain` çağıran tarafından zaten `normalize_domain()`'den geçirilmiş olmalıdır.
    """
    soup = BeautifulSoup(html, "html.parser")
    raw_candidates: list[str] = []

    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if href.lower().startswith("mailto:"):
            address = href[len("mailto:") :].split("?", 1)[0]
            raw_candidates.append(unquote(address))

    for tag in soup.find_all(attrs={"data-cfemail": True}):
        decoded = decode_cfemail(tag["data-cfemail"])
        if decoded:
            raw_candidates.append(decoded)

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = html_unescape(soup.get_text(" "))

    raw_candidates.extend(_PLAIN_EMAIL_PATTERN.findall(text))

    for match in _OBFUSCATED_PATTERN.finditer(text):
        local, domain_part = match.group(1), match.group(2)
        domain = re.sub(_DOT_TOKEN, ".", domain_part, flags=re.IGNORECASE)
        domain = re.sub(r"\s+", "", domain)
        raw_candidates.append(f"{local}@{domain}")

    found: dict[str, FoundEmail] = {}
    for raw in raw_candidates:
        email = normalize_email(raw)
        if email is None or _is_junk(email) or email in found:
            continue
        found[email] = FoundEmail(
            email=email,
            source_url=source_url,
            is_role=is_role_address(email),
            on_company_domain=_on_company_domain(email, company_domain),
            page_rank=page_rank,
        )
    return list(found.values())


def rank_emails(found: Iterable[FoundEmail]) -> list[FoundEmail]:
    """Bulunan tüm adresleri en iyisi başta sıralar; tekrarlar en iyi bulunuşuyla teklenir.

    Sıra: (1) şirketin kendi alan adında mı, (2) rol basamağı, (3) bulunduğu sayfanın türü,
    (4) kararlılık için ilk görülme sırası.
    """
    indexed = list(enumerate(found))

    def sort_key(item: tuple[int, FoundEmail]) -> tuple[int, int, int, int]:
        index, entry = item
        return (0 if entry.on_company_domain else 1, _tier(entry.email), entry.page_rank, index)

    seen: set[str] = set()
    result: list[FoundEmail] = []
    for _, entry in sorted(indexed, key=sort_key):
        if entry.email in seen:
            continue
        seen.add(entry.email)
        result.append(entry)
    return result


def best_email(found: Iterable[FoundEmail]) -> Optional[FoundEmail]:
    """En iyi sıradaki adresi döndürür; hiç adres yoksa `None`."""
    ranked = rank_emails(found)
    return ranked[0] if ranked else None
