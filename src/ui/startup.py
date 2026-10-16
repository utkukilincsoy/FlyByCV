"""Uygulama açılış akışı (PLAN.md Faz 10 Adım 10.2b) -- `main.py`'den ayrıldı, test edilebilsin.

Sıra: veritabanı hazır olduktan sonra (1) sihirbaz GEREKİYORSA (`onboarding_completed` False,
yani taze kurulum) önce o çalışır -- KVKK onayı sihirbazın ilk sayfasındadır, bu yüzden ayrıca
`ensure_legal_consent` sorulmaz; profil dosyası da sihirbazda oluşur, bu yüzden profil kontrolü
sihirbazdan SONRA yapılır. (2) Sihirbaz gerekmiyorsa (var olan kullanıcı) eski akış aynen
sürer: önce profil kontrolü, sonra KVKK onayı. Sihirbaz henüz yazılmadıysa (`run_wizard=None`,
Adım 10.2d'ye kadar) taze kurulum da bu eski akışa düşer.
"""
import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtWidgets import QMessageBox
from sqlalchemy.engine import Engine

from src.core.db import get_session
from src.cv_builder.profile_loader import load_profile
from src.ui.legal_dialog import ensure_legal_consent
from src.ui.onboarding_data import is_onboarding_needed

logger = logging.getLogger(__name__)

PROFILE_ERROR_TITLE = "Profil dosyası okunamadı"
PROFILE_ERROR_TEXT = (
    "{path} dosyası okunamadı veya bozuk:\n\n{error}\n\n"
    "Başlangıç noktası olarak data/master_profile.example.json dosyasını kopyalayıp "
    "kendi bilgilerinizle doldurabilirsiniz."
)

EXIT_OK = 0
EXIT_PROFILE_ERROR = 1

#: `run_wizard(engine, profile_path) -> bool`: sihirbaz TAMAMLANDIYSA True; kullanıcı yarıda
#: kapattıysa False (ilerleme kaydedilmiştir, sonraki açılışta kaldığı yerden devam eder).
WizardRunner = Callable[[Engine, Path], bool]


def _show_profile_error(path: Path, error: Exception) -> None:
    QMessageBox.critical(
        None, PROFILE_ERROR_TITLE, PROFILE_ERROR_TEXT.format(path=path, error=error)
    )


def prepare_startup(
    engine: Engine,
    profile_path: Path,
    *,
    run_wizard: Optional[WizardRunner] = None,
    ask_consent: Callable[[Engine], bool] = ensure_legal_consent,
    show_profile_error: Callable[[Path, Exception], None] = _show_profile_error,
) -> Optional[int]:
    """Ana pencere açılmadan önceki adımları yürütür.

    `None` döner = devam et, ana pencereyi aç. Bir tamsayı döner = uygulama bu çıkış koduyla
    kapanmalı (sihirbaz yarıda kapatıldı -> `EXIT_OK`; profil bozuk -> `EXIT_PROFILE_ERROR`).
    """
    with get_session(engine) as session:
        needs_wizard = run_wizard is not None and is_onboarding_needed(session)

    if needs_wizard:
        if not run_wizard(engine, profile_path):
            logger.info("İlk açılış sihirbazı tamamlanmadan kapatıldı; ilerleme kaydedildi")
            return EXIT_OK

    # json.JSONDecodeError, ValueError'ın alt sınıfıdır; bu yüzden "bozuk dosya" da bu iki
    # exception ile yakalanır.
    try:
        load_profile(profile_path)
    except (FileNotFoundError, ValueError) as error:
        logger.error(f"Profil dosyası okunamadı: {error}")
        show_profile_error(profile_path, error)
        return EXIT_PROFILE_ERROR

    if not needs_wizard:
        # KVKK / 6563 bilgilendirmesi: bu bilgisayarda bir kez sorulur (Adım 10.8b). Sihirbaz
        # çalıştıysa onay onun ilk sayfasında alındı. Onaylanmazsa uygulama yine açılır ama
        # gerçek gönderim yapılmaz (smtp_sender.send_application).
        ask_consent(engine)

    return None
