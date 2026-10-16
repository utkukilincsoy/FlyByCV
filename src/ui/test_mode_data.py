"""Test modundan gerçek gönderime geçiş / geri dönüş (PySide6 İÇERMEZ).

Eskiden test modunu kapatmak için kutuya "GERCEK" yazmak gerekirdi; teknik olmayan kullanıcı için
anlaşılmazdı. Şimdi Ayarlar'da ve sihirbaz bitiş sayfasında açık bir "Gerçek gönderime geç" düğmesi
var (pencere: `src/ui/test_mode_dialog.py`). Bu modül düğmelerin arkasındaki kuralı taşır:

- Gerçek gönderime geçiş KVKK / 6563 onayı olmadan YAPILMAZ (`core/legal.has_consent`). Bu,
  `smtp_sender.send_application`'ın onaysız gerçek gönderimi reddetmesine (CLAUDE.md kural 12) EK bir
  katmandır: onaysız kullanıcı "gerçek mod"a hiç geçemez, sonradan sürpriz bir ret görmez.
- Test moduna dönüş (güvenli yön) onaysız ve anındadır.

Limitler, mesai saati, acil durdur ve rol adresi kuralı bu modda da aynen geçerlidir; burada onlara
dokunulmaz.
"""
import logging
from typing import Optional

from sqlmodel import Session

from src.core.legal import has_consent
from src.core.settings import KEY_TEST_MODE, set_setting

logger = logging.getLogger(__name__)

GO_LIVE_BUTTON_TEXT = "Gerçek gönderime geç"
BACK_TO_TEST_BUTTON_TEXT = "Test moduna dön"

TEST_MODE_ON_LABEL = "Test modu AÇIK: mailler şirketlere değil, kendi adresinize gider."
TEST_MODE_OFF_LABEL = "GERÇEK GÖNDERİM AÇIK: mailler şirketlere gidiyor."

GO_LIVE_DIALOG_TITLE = "Gerçek gönderime geçilsin mi?"
GO_LIVE_DIALOG_TEXT = (
    "Bundan sonra mailler gerçekten şirketlere gidecek, kendi adresinize değil.\n\n"
    "Gönderilen bir mail geri alınamaz. Günlük/saatlik limitler, mesai saati ve \"Acil durdur\" "
    "aynen geçerli kalır.\n\n"
    "Emin değilseniz önce Otopilot'u test modunda deneyip Onay Kuyruğu'ndaki maillere bakın. "
    "İstediğiniz zaman \"Test moduna dön\" düğmesiyle geri dönebilirsiniz."
)
GO_LIVE_ACK_TEXT = "Anladım: bundan sonra mailler gerçekten şirketlere gidecek."
GO_LIVE_CONFIRM_BUTTON_TEXT = "Başlat: gerçek gönderime geç"
GO_LIVE_CANCEL_BUTTON_TEXT = "Vazgeç"

NO_CONSENT_TITLE = "Gerçek gönderime geçilemiyor"
NO_CONSENT_TEXT = (
    "KVKK / 6563 bilgilendirmesi henüz onaylanmadı, bu yüzden gerçek gönderime geçilemez.\n\n"
    "Uygulamayı kapatıp yeniden açın: açılışta bilgilendirme tekrar gösterilir. Onayladıktan sonra "
    "bu düğmeyi yeniden kullanabilirsiniz. (Test modu onaysız da çalışır.)"
)


class GoLiveRefused(Exception):
    """Gerçek gönderime geçiş şartı sağlanmadı (şu an yalnızca: KVKK onayı yok)."""


def go_live_blocker(session: Session) -> Optional[str]:
    """Gerçek gönderime geçişi engelleyen sebebin Türkçe metni; engel yoksa `None`."""
    if not has_consent(session):
        return NO_CONSENT_TEXT
    return None


def enable_real_sending(session: Session) -> None:
    """Test modunu kapatır (gerçek gönderim). COMMIT ETMEZ.

    Raises:
        GoLiveRefused: KVKK onayı yoksa -- hiçbir şey yazılmaz. Arayüz bunu zaten önceden sorar;
            burada TEKRAR denetlenir ki başka bir yoldan çağrılsa da kapı açık kalmasın.
    """
    blocker = go_live_blocker(session)
    if blocker is not None:
        raise GoLiveRefused(blocker)
    set_setting(session, KEY_TEST_MODE, False)
    logger.info("Test modu kapatıldı: gerçek gönderime geçildi")


def return_to_test_mode(session: Session) -> None:
    """Test modunu açar (güvenli yön, onaysız). COMMIT ETMEZ."""
    set_setting(session, KEY_TEST_MODE, True)
    logger.info("Test moduna dönüldü")
