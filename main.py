"""FlybyCV – giriş noktası."""
import logging
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from src.core import paths, purge_secrets, rename_migration, secrets, selftest
from src.core.backup import backups_dir_for, run_startup_backup
from src.core.db import get_engine, get_session, init_db
from src.core.settings import KEY_UI_THEME, get_setting
from src.core.logging_setup import configure_logging
from src.core.version import APP_NAME, __version__
from src.ui.crash_handler import install_crash_handler
from src.ui.main_window import MainWindow
from src.ui.onboarding_wizard import run_onboarding_wizard
from src.ui.startup import prepare_startup
from src.ui.theme import apply_theme

logger = logging.getLogger(__name__)


def _apply_saved_theme(app: QApplication, engine) -> None:
    try:
        with get_session(engine) as session:
            theme = get_setting(session, KEY_UI_THEME)
    except Exception:
        logger.warning("Tema ayarı okunamadı; açık tema.", exc_info=True)
        theme = None
    apply_theme(app, theme)


def _migrate_legacy_secrets() -> None:
    """Eski adla (cv-autopilot) kasadaki kayıtları yeni servise kopyalar; hiçbir hata açılışı engellemez."""
    try:
        secrets.migrate_legacy_secrets()
    except Exception:
        logger.warning("Eski kasa kayıtları taşınamadı.", exc_info=True)


def main() -> int:
    # Eski `%APPDATA%\CV Autopilot` verisinin kopyası logging'den ÖNCE: log dosyası yeni klasörü
    # oluşturur ve "yeni klasör yok" koşulunu bozar. Sonuç logging kurulunca yazılır.
    home_migration = rename_migration.run_startup_migration()
    configure_logging()
    rename_migration.log_outcome(home_migration)
    paths.configure_playwright_browsers_path()
    secrets.configure_backend()

    # `--selftest`: arayüzü açmadan, ağa çıkmadan kasa (keyring) durumunu raporlar
    # (Adım 8.4). Windows Sandbox gibi temiz bir ortamda paketlemeyi hızlıca doğrulamak
    # için.
    if "--selftest" in sys.argv[1:]:
        return selftest.run()

    # `--purge-secrets`: kaldırıcının "verilerimi de sil" seçeneği için kasadaki (keyring) Gmail
    # şifresini ve API anahtarlarını siler (Adım 10.9e). Arayüz/veritabanı AÇILMAZ, geri alınamaz.
    if "--purge-secrets" in sys.argv[1:]:
        return purge_secrets.run()

    _migrate_legacy_secrets()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(paths.app_icon_path())))
    install_crash_handler()

    profile_path = paths.profile_path()

    # Günde bir açılış yedeği (veritabanı + profil), motor açılmadan ve şema göçlerinden ÖNCE:
    # yedek, kullanıcının dokunulmamış hâlini yansıtsın. Hiçbir durumda açılışı engellemez.
    db_path = paths.db_path()
    run_startup_backup(db_path, profile_path, backups_dir_for(db_path))

    engine = get_engine()
    init_db(engine=engine)
    _apply_saved_theme(app, engine)

    # Taze kurulumda ilk açılış sihirbazı, var olan kullanıcıda (göç `onboarding_completed`'ı
    # True yazar) profil kontrolü + KVKK onayı; sihirbaz ona kendiliğinden açılmaz (bkz.
    # src/ui/startup.py).
    exit_code = prepare_startup(engine, profile_path, run_wizard=run_onboarding_wizard)
    if exit_code is not None:
        return exit_code

    window = MainWindow(engine, profile_path)
    window.show()
    window.start_update_check()  # arka planda, sessiz; KVKK onayından SONRA (yeni dış bağlantı)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
