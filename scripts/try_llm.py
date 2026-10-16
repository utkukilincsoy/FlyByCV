"""Seçili LLM sağlayıcısını örnek bir şirket metni ve örnek bir red maili üzerinde dener.

Kullanım: `python scripts/try_llm.py`

DİKKAT: Bu script GERÇEK ağ çağrısı yapar (üç çağrı, her biri çok küçük) ve sağlayıcının
hesabından ücret düşebilir; ücretsiz kotanız varsa ücret alınmaz.

Sağlayıcı `SafeProvider` ile SARMALANMAZ. Sebebi: sarmalanırsa 401, olmayan model adı, bozuk
JSON gibi gerçek hatalar sessizce yutulur, ekranda anahtar kelime sonucu görünür ve hatanın
ne olduğu anlaşılmaz. Burada hata olduğu gibi gösterilir; ayrıca ham çıktının doğrulamadan
sonra neye dönüştüğü de yazdırılır.
"""
import logging
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.logging_setup import quiet_noisy_loggers  # noqa: E402
from src.core.secrets import get_api_key  # noqa: E402
from src.core.settings import DEFAULTS  # noqa: E402
from src.llm.base import (  # noqa: E402
    filter_tags,
    normalize_reply_category,
)
from src.llm.factory import _build_llm_provider  # noqa: E402
from src.llm.phrase_validator import validate_focus_phrase  # noqa: E402
from src.llm.providers import (  # noqa: E402
    API_KEY_PROVIDERS,
    MODEL_SETTING_KEYS,
    describe_llm_error,
)

logger = logging.getLogger(__name__)

SEPARATOR = "=" * 70

SAMPLE_COMPANY = "Örnek Ağ Çözümleri A.Ş."
SAMPLE_ABOUT = (
    "2010 yılından bu yana kurumsal müşterilere ağ altyapısı hizmeti veriyoruz. "
    "Cisco router ve switch kurulumu, LAN/WAN tasarımı, güvenlik duvarı yapılandırması ve "
    "7/24 ağ izleme hizmetlerimizle Türkiye'nin dört bir yanındaki fabrikalara destek "
    "sağlıyoruz. Ekibimizde sertifikalı ağ mühendisleri bulunmaktadır."
)

SAMPLE_REPLY_SUBJECT = "Re: Staj Başvurusu Hakkında"
SAMPLE_REPLY_BODY = (
    "Merhaba,\n"
    "\n"
    "Başvurunuz için teşekkür ederiz. Maalesef bu dönem için uygun bir pozisyonumuz "
    "bulunmamaktadır. Özgeçmişinizi kayıtlarımızda tutuyoruz.\n"
    "\n"
    "Saygılarımla,\n"
    "Ayşe Yılmaz | İnsan Kaynakları | 0532 111 22 33 | ayse@ornek-firma.com"
)


def _choose_provider() -> Optional[str]:
    """Anahtarı kayıtlı sağlayıcıları listeler ve kullanıcıya seçtirir."""
    available = [name for name in API_KEY_PROVIDERS if get_api_key(name)]
    if not available:
        logger.error(
            "Hiçbir sağlayıcının API anahtarı kayıtlı değil. "
            "Önce şunu çalıştırın: python scripts/set_api_key.py"
        )
        return None

    if len(available) == 1:
        logger.info(f"Tek kayıtlı sağlayıcı kullanılacak: {available[0]}")
        return available[0]

    options = "\n".join(f"  {i}) {name}" for i, name in enumerate(available, start=1))
    while True:
        answer = input(f"Hangi sağlayıcı denenecek?\n{options}\nSeçim: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(available):
            return available[int(answer) - 1]
        logger.warning("Geçersiz seçim, tekrar deneyin.")


def _choose_model(provider: str) -> str:
    """Model adını sorar; boş bırakılırsa ayarların varsayılanı kullanılır."""
    default = DEFAULTS[MODEL_SETTING_KEYS[provider]]
    answer = input(f"Model adı [Enter = {default}]: ").strip()
    return answer or default


def _try(label: str, call, validate) -> None:
    """Tek bir metodu çağırır; ham sonucu ve doğrulama sonrası halini yazdırır."""
    logger.info(SEPARATOR)
    logger.info(label)
    try:
        raw = call()
    except Exception as error:  # tanı aracı: hata yutulmaz, gösterilir
        logger.error(f"  HATA: {describe_llm_error(error)}")
        return

    logger.info(f"  Ham cevap        : {raw!r}")
    logger.info(f"  Doğrulama sonrası: {validate(raw)!r}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    quiet_noisy_loggers()
    logger.warning(
        "DİKKAT: Bu script gerçek ağ çağrısı yapar (üç küçük çağrı) ve ücretli olabilir "
        "(ücretsiz kotanız varsa ücret alınmaz).\n"
    )

    provider_name = _choose_provider()
    if provider_name is None:
        sys.exit(1)

    model = _choose_model(provider_name)
    api_key = get_api_key(provider_name)

    try:
        provider = _build_llm_provider(provider_name, model, api_key)
    except Exception as error:
        logger.error(f"Sağlayıcı kurulamadı: {describe_llm_error(error)}")
        sys.exit(1)

    logger.info(SEPARATOR)
    logger.info(f"Sağlayıcı: {provider_name}   Model: {model}")  # anahtar yazılmaz

    _try(
        f"1) classify_company — {SAMPLE_COMPANY}",
        lambda: provider.classify_company(SAMPLE_COMPANY, SAMPLE_ABOUT),
        filter_tags,
    )
    _try(
        "2) company_focus_phrase — şirketin ne yaptığı",
        lambda: provider.company_focus_phrase(SAMPLE_COMPANY, SAMPLE_ABOUT),
        lambda phrase: validate_focus_phrase(phrase, SAMPLE_ABOUT),
    )
    _try(
        "3) classify_reply — örnek red maili (beklenen: rejected)",
        lambda: provider.classify_reply(SAMPLE_REPLY_SUBJECT, SAMPLE_REPLY_BODY),
        normalize_reply_category,
    )
    logger.info(SEPARATOR)


if __name__ == "__main__":
    main()
