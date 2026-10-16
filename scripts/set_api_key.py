"""API anahtarını işletim sisteminin şifre kasasına kaydeder veya siler.

Kullanım: `python scripts/set_api_key.py`

Anahtar yazarken ekranda GÖRÜNMEZ (`getpass`) ve hiçbir yere loglanmaz. Kaydedildikten sonra
kasadan geri okunup doğrulanır, ama değeri asla ekrana basılmaz.
"""
import getpass
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.secrets import delete_api_key, get_api_key, set_api_key  # noqa: E402
from src.llm.providers import API_KEY_PROVIDERS  # noqa: E402

logger = logging.getLogger(__name__)

ACTION_SAVE = "1"
ACTION_DELETE = "2"


def _ask_provider() -> str:
    """Sağlayıcıyı numarayla sorar; geçerli bir seçim yapılana kadar tekrar sorar."""
    options = "\n".join(
        f"  {index}) {name}" for index, name in enumerate(API_KEY_PROVIDERS, start=1)
    )
    while True:
        answer = input(f"Hangi sağlayıcı?\n{options}\nSeçim: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(API_KEY_PROVIDERS):
            return API_KEY_PROVIDERS[int(answer) - 1]
        logger.warning("Geçersiz seçim, tekrar deneyin.")


def _ask_action() -> str:
    """Kaydetme mi silme mi olduğunu sorar."""
    while True:
        answer = input(f"Ne yapılsın?\n  {ACTION_SAVE}) Anahtar kaydet\n"
                       f"  {ACTION_DELETE}) Anahtarı sil\nSeçim: ").strip()
        if answer in (ACTION_SAVE, ACTION_DELETE):
            return answer
        logger.warning("Geçersiz seçim, tekrar deneyin.")


def _save(provider: str) -> None:
    """Anahtarı gizli olarak alır, kaydeder ve kasadan geri okuyup doğrular."""
    key = getpass.getpass(f"{provider} API anahtarı (yazdığınız görünmeyecek): ")
    try:
        set_api_key(provider, key)
    except ValueError as error:
        logger.error(f"Kaydedilmedi: {error}")
        return

    # Doğrulama: geri okunabiliyor mu? Değerin kendisi ASLA ekrana basılmaz.
    if get_api_key(provider):
        logger.info(f"{provider}: kaydedildi ve doğrulandı.")
    else:
        logger.error(f"{provider}: kaydedildi ama geri okunamadı. Şifre kasasını kontrol edin.")


def _delete(provider: str) -> None:
    """Onay aldıktan sonra anahtarı siler."""
    answer = input(f"{provider} anahtarı silinecek. Emin misiniz? (e/h): ").strip().lower()
    if answer not in ("e", "evet"):
        logger.info("Silme iptal edildi.")
        return

    if delete_api_key(provider):
        logger.info(f"{provider}: silindi.")
    else:
        logger.info(f"{provider}: silinecek bir anahtar yoktu.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    provider = _ask_provider()
    if _ask_action() == ACTION_SAVE:
        _save(provider)
    else:
        _delete(provider)


if __name__ == "__main__":
    main()
