"""Ücretsiz e-posta sağlayıcıları listesi.

Şirketin sitesi yoksa ve e-postası bu sağlayıcılardan biriyse (örn. sadece "kariyer@gmail.com"),
o e-posta şirketin resmi alan adı olarak KAYDEDİLMEZ: aynı sağlayıcıyı kullanan iki farklı
şirket aynı "alan adı" ile çakışır ve tekrar kontrolü (CLAUDE.md, 4. kural) anlamını kaybeder.

Yeni bir sağlayıcı eklemek için `FREE_MAIL_BASES`'e bir kelime eklemek yeterli (".com" ve
".com.tr" varyantları otomatik türetilir). Kalıba uymayan sağlayıcılar `_EXTRA`'ya eklenir.

`src/company_finder/csv_import.py` (Faz 4 Adım 4.1) ve ileride `email_extractor.py`
(Faz 4 Adım 4.4) bu modülü kullanır.
"""
from src.core.models import normalize_domain

FREE_MAIL_BASES: tuple[str, ...] = (
    "gmail",
    "googlemail",
    "hotmail",
    "outlook",
    "live",
    "yahoo",
    "yandex",
    "icloud",
    "msn",
)

_SUFFIXES: tuple[str, ...] = (".com", ".com.tr")

# Türkiye'de sık görülen, yukarıdaki taban x uzantı kalıbına uymayan sağlayıcılar.
_EXTRA: frozenset[str] = frozenset(
    {"yandex.ru", "ymail.com", "mail.ru", "mynet.com", "protonmail.com", "proton.me"}
)

FREE_MAIL_DOMAINS: frozenset[str] = (
    frozenset(f"{base}{suffix}" for base in FREE_MAIL_BASES for suffix in _SUFFIXES) | _EXTRA
)


def is_free_mail_domain(domain: str) -> bool:
    """Verilen alan adı ücretsiz bir mail sağlayıcısına mı ait?

    Girdi zaten `normalize_domain()`'den geçmiş olabilir ya da olmayabilir; fonksiyon kendi
    içinde de `normalize_domain()` uygular, bu yüzden çağıran taraf önceden normalize etmek
    zorunda değildir. Türkçe kurallı `turkish_lower()` BURADA kullanılamaz: "GMAIL.COM" gibi
    büyük harfli bir alan adını "gmaıl.com" yapıp eşleşmeyi bozardı (bkz. `normalize_domain`
    docstring'i, `src/core/models.py`).
    """
    return normalize_domain(domain) in FREE_MAIL_DOMAINS


def is_free_mail_email(email: str) -> bool:
    """Verilen e-posta adresinin alan adı ücretsiz bir mail sağlayıcısına mı ait?"""
    _, _, domain = email.rpartition("@")
    return is_free_mail_domain(domain)
