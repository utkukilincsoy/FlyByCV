"""Bir alan adının mail alabilecek MX kaydına sahip olup olmadığını kontrol eder.

Üç durumlu (`Company.mx_valid` ile birebir): `True` = MX kaydı var, `False` = alan adı yok ya
da MX kaydı yok, `None` = **bilinmiyor** (zaman aşımı, DNS'e erişilemedi). `None` "kontrol
edilmedi" anlamını korur — geçici bir ağ sorununu kalıcı bir "MX yok" sonucuna çevirmemek için.

Hiçbir exception dışarı sızmaz: uygulama çökmemeli (CLAUDE.md, 3. kural). Hata Türkçe
`logger.warning` ile yazılır.
"""
import logging
from typing import Optional

import dns.exception
import dns.resolver

logger = logging.getLogger(__name__)

MX_TIMEOUT_SECONDS = 5.0

# Alan adı -> sonuç. `None` sonuçlar YAZILMAZ (bkz. clear_mx_cache ve has_mx_record docstring'i).
_cache: dict[str, bool] = {}


def clear_mx_cache() -> None:
    """Önbelleği boşaltır; testler arası sızıntıyı önlemek için kullanılır."""
    _cache.clear()


def has_mx_record(domain: str, *, resolver: Optional[object] = None) -> Optional[bool]:
    """`domain` için MX kaydı var mı? Sonuç alan adı başına önbelleklenir.

    `None` (geçici hata) sonuçları önbelleğe ALINMAZ: bir sonraki çağrı yeniden dener. `True`/
    `False` kalıcı kabul edilip önbelleğe yazılır.
    """
    if domain in _cache:
        return _cache[domain]

    if resolver is None:
        resolver = dns.resolver.Resolver()
        resolver.timeout = MX_TIMEOUT_SECONDS
        resolver.lifetime = MX_TIMEOUT_SECONDS

    try:
        answer = resolver.resolve(domain, "MX")
        result = bool(len(answer))
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        result = False
    except dns.exception.Timeout:
        logger.warning(f"{domain}: MX sorgusu zaman aşımına uğradı, sonuç bilinmiyor")
        return None
    except Exception as exc:  # DNS sunucusuna erişilemedi, beklenmeyen hata vb.
        logger.warning(f"{domain}: MX sorgusu yapılamadı ({exc!r}), sonuç bilinmiyor")
        return None

    _cache[domain] = result
    return result
