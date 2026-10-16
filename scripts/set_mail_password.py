"""Mail uygulama şifresini işletim sisteminin şifre kasasına kaydeder veya siler.

Kullanım: `python scripts/set_mail_password.py`

Normal mail şifreniz (Gmail/Yandex/…) burada genelde ÇALIŞMAZ; sağlayıcınızın hesap
güvenliği bölümünden özel bir "uygulama şifresi" üretmeniz gerekir (Gmail: İki Adımlı
Doğrulama açık olmalı, sonra Google Hesabı → Güvenlik → "Uygulama şifreleri"; Yandex: Hesap →
Güvenlik → "Uygulama şifreleri"). Bazı sağlayıcılar şifreyi ekranda "abcd efgh ijkl mnop" gibi
4'erli gösterir, aradaki boşluklarla da kopyalayabilirsiniz — kaydederken otomatik temizlenir.
Hangi sağlayıcıyı kullandığınız Ayarlar sekmesindeki "Mail sağlayıcısı" seçiminden gelir, bu
script yalnızca şifreyi kasaya yazar/okur.

Şifre yazarken ekranda GÖRÜNMEZ (`getpass`) ve hiçbir yere loglanmaz. Kaydedildikten sonra
kasadan geri okunup doğrulanır, ama değeri asla ekrana basılmaz.
"""
import getpass
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.secrets import delete_mail_password, get_mail_password, set_mail_password  # noqa: E402

logger = logging.getLogger(__name__)

ACTION_SAVE = "1"
ACTION_DELETE = "2"


def _ask_action() -> str:
    """Kaydetme mi silme mi olduğunu sorar."""
    while True:
        answer = input(f"Ne yapılsın?\n  {ACTION_SAVE}) Şifre kaydet\n"
                       f"  {ACTION_DELETE}) Şifreyi sil\nSeçim: ").strip()
        if answer in (ACTION_SAVE, ACTION_DELETE):
            return answer
        logger.warning("Geçersiz seçim, tekrar deneyin.")


def _save() -> None:
    """Şifreyi gizli olarak alır, kaydeder ve kasadan geri okuyup doğrular."""
    password = getpass.getpass("Mail uygulama şifresi (yazdığınız görünmeyecek): ")
    try:
        set_mail_password(password)
    except ValueError as error:
        logger.error(f"Kaydedilmedi: {error}")
        return

    # Doğrulama: geri okunabiliyor mu? Değerin kendisi ASLA ekrana basılmaz.
    if get_mail_password():
        logger.info("Mail uygulama şifresi kaydedildi ve doğrulandı.")
    else:
        logger.error("Kaydedildi ama geri okunamadı. Şifre kasasını kontrol edin.")


def _delete() -> None:
    """Onay aldıktan sonra şifreyi siler."""
    answer = input("Mail uygulama şifresi silinecek. Emin misiniz? (e/h): ").strip().lower()
    if answer not in ("e", "evet"):
        logger.info("Silme iptal edildi.")
        return

    if delete_mail_password():
        logger.info("Şifre silindi.")
    else:
        logger.info("Silinecek bir şifre yoktu.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if _ask_action() == ACTION_SAVE:
        _save()
    else:
        _delete()


if __name__ == "__main__":
    main()
