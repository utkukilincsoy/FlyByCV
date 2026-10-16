"""Ana pencere: en üstte durum şeridi, altında sekmeler.

Otopilot sekmesi (`src/ui/autopilot_tab.py`, İLK sekme) tek "Başlat" ile keşif → tara → hazırla
→ gönder döngüsünü (`src/autopilot/runner.py`) çalıştırır; çalışırken Şirketler ve Onay
Kuyruğu'nun çakışabilecek butonlarını kilitler. Şirketler sekmesi (`src/ui/companies_tab.py`)
tablo, arama ve arka plan işlerini (içe aktarma, tarama, başvuru hazırlama) içerir. Onay
Kuyruğu sekmesi (`src/ui/queue_tab.py`) kuyruk tablosu, önizleme, tekli gönderim ve toplu
gönderim (başlat/durdur) işlemlerini içerir. Cevaplar sekmesi (`src/ui/replies_tab.py`) gelen
mail cevaplarını, elle düzeltmeyi ve "Şimdi kontrol et" (IMAP taraması) içerir. Ayarlar sekmesi
(`src/ui/settings_tab.py`) tüm ayarları düzenler; kaydedince `settings_saved` sinyaliyle durum
şeridini ve Otopilot sekmesinin sayaçlarını tazeler. Dashboard sekmesi (`src/ui/dashboard_tab.py`)
şirket/başvuru/cevap sayılarının salt okunur özetidir.
"""
import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from src.core.backup import BackupError, backups_dir_for, restore_backup
from src.core.db import sqlite_file_path
from src.core.version import APP_NAME, __version__
from src.sender.smtp_sender import SMTP_TIMEOUT
from src.ui.autopilot_tab import AutopilotTab
from src.ui.companies_tab import CompaniesTab
from src.ui.icons import get_icon
from src.ui.dashboard_tab import DashboardTab
from src.ui.profile_tab import ProfileTab
from src.ui.queue_tab import QueueTab
from src.ui.replies_tab import RepliesTab
from src.ui.restart import relaunch
from src.ui.settings_tab import SettingsTab
from src.ui.status_strip import StatusStrip
from src.ui.update_banner import UpdateBanner

logger = logging.getLogger(__name__)

#: Sekme başlıkları, gösterileceği sırayla. Otopilot BİLİNÇLİ olarak ilk sırada (PLAN Faz 9
#: girişi: "tek Başlat ile uçtan uca" -- kullanıcının açılışta ilk göreceği sekme bu olmalı).
_TAB_ICON_KEYS = (
    "tab_autopilot", "tab_companies", "tab_queue", "tab_replies", "tab_profile", "tab_settings",
    "tab_dashboard",
)
TAB_TITLES = (
    "Otopilot",
    "Şirketler",
    "Onay Kuyruğu",
    "Cevaplar",
    "Profil",
    "Ayarlar",
    "Dashboard",
)
#: Ayarlar sekmesinin `tab_widget` içindeki sırası -- sekme değişiminde `confirm_leave()`'in
#: hangi indeksten ÇIKILDIĞINI anlamak için kullanılır.
SETTINGS_TAB_INDEX = TAB_TITLES.index("Ayarlar")
#: Profil sekmesinin sırası -- `SettingsTab` ile AYNI kirlilik/onay kalıbı (Not: Adım 10.3d).
PROFILE_TAB_INDEX = TAB_TITLES.index("Profil")
#: Dashboard sekmesinin sırası -- oraya her geçişte istatistikler tazelenir (bkz. `_on_tab_changed`).
DASHBOARD_TAB_INDEX = TAB_TITLES.index("Dashboard")
#: Otopilot sekmesinin sırası -- oraya her geçişte sayaçlar tazelenir (bkz. `_on_tab_changed`).
AUTOPILOT_TAB_INDEX = TAB_TITLES.index("Otopilot")

RESTORE_TITLE = "Yedekten geri yükle"
RESTARTING_TEXT = "Geri yükleme tamamlandı. Uygulama yeniden başlatılıyor."
MANUAL_RESTART_TEXT = (
    "Geri yükleme tamamlandı ama uygulama kendiliğinden yeniden açılamadı. "
    "Lütfen uygulamayı elle açın."
)
RESTORE_UNAVAILABLE_TEXT = "Bu oturumda geri yükleme kullanılamıyor (veritabanı bir dosyada değil)."


class MainWindow(QMainWindow):
    """Uygulamanın ana penceresi: üstte durum şeridi, altında sekmeler."""

    def __init__(
        self,
        engine: Engine,
        profile_path: Path,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._profile_path = profile_path

        self.setWindowTitle(f"{APP_NAME} {__version__}")

        self.status_strip = StatusStrip(engine)
        self.update_banner = UpdateBanner(engine)  # başta gizli; kontrol `start_update_check()` ile

        self.companies_tab = CompaniesTab(engine, profile_path)
        self.queue_tab = QueueTab(engine, profile_path)
        self.autopilot_tab = AutopilotTab(engine, profile_path, self.companies_tab, self.queue_tab)
        self.replies_tab = RepliesTab(engine, profile_path)
        self.profile_tab = ProfileTab(profile_path)
        self.settings_tab = SettingsTab(engine, profile_path)
        self.dashboard_tab = DashboardTab(engine)
        self.status_strip.emergency_stop_activated.connect(self.queue_tab.on_emergency_stop)
        self.status_strip.emergency_stop_activated.connect(self.autopilot_tab.on_emergency_stop)
        self.settings_tab.settings_saved.connect(self.status_strip.refresh)
        self.settings_tab.settings_saved.connect(self.autopilot_tab.reload)
        self.settings_tab.settings_saved.connect(self.autopilot_tab.refresh_source_preview)
        self.settings_tab.update_available.connect(self.update_banner.show_update)
        self.settings_tab.restore_requested.connect(self._on_restore_requested)

        tabs: list[tuple[str, QWidget]] = [
            (TAB_TITLES[0], self.autopilot_tab),
            (TAB_TITLES[1], self.companies_tab),
            (TAB_TITLES[2], self.queue_tab),
            (TAB_TITLES[3], self.replies_tab),
            (TAB_TITLES[4], self.profile_tab),
            (TAB_TITLES[5], self.settings_tab),
            (TAB_TITLES[6], self.dashboard_tab),
        ]

        self.tab_widget = QTabWidget()
        for (title, widget), icon_key in zip(tabs, _TAB_ICON_KEYS):
            self.tab_widget.addTab(widget, get_icon(icon_key), title)

        # Ayarlar'dan/Profil'den başka bir sekmeye geçerken kaydedilmemiş değişiklik varsa
        # sorar (`confirm_leave()`); reddedilirse sekme geri alınır. `_changing_tab` bu geri
        # almanın kendi `currentChanged`'ini tekrar işlemesini önler.
        self._previous_tab_index = self.tab_widget.currentIndex()
        self._changing_tab = False
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        self.settings_tab.autopilot_requested.connect(self._show_autopilot_tab)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.status_strip)
        layout.addWidget(self.update_banner)
        layout.addWidget(self.tab_widget)
        self.setCentralWidget(central)

    def start_update_check(self) -> None:
        """Açılıştaki sessiz "yeni sürüm var mı?" kontrolü (`main.py` çağırır; kurucu çağırmaz ki
        testlerde ve KVKK onayından önce hiçbir arka plan işi/ağ çağrısı kendiliğinden başlamasın)."""
        self.update_banner.start_check()

    # -- yedekten geri yükleme (Ayarlar > Yedekleme) -----------------------------------------

    def _running_job_name(self) -> Optional[str]:
        """Veritabanına yazabilecek, şu an çalışan bir arka plan işi varsa adı; yoksa None."""
        checks: tuple[tuple[str, Callable[[], bool]], ...] = (
            ("Otopilot", self.autopilot_tab.is_autopilot_running),
            ("Onay Kuyruğu'ndaki gönderim/iş", self.queue_tab.is_job_running),
            ("Şirketler sekmesindeki iş", self.companies_tab.is_job_running),
            ("Cevaplar kontrolü", self.replies_tab.is_job_running),
        )
        for name, is_running in checks:
            if is_running():
                return name
        return None

    def _on_restore_requested(self, zip_path: Path) -> None:
        """Seçilen yedeği geri yükler, sonra uygulamayı kapatıp yeniden açar.

        Çalışan bir iş varken yapılmaz: işçi, üzerine yazılan veritabanına yazmaya devam ederdi.
        Geri yükleme sonrası açık sekmeler eski veriyi tuttuğu için uygulama yeniden başlatılır;
        yeni süreç açılamazsa kullanıcıdan elle açması istenir. Her iki durumda da kapanılır.
        """
        running = self._running_job_name()
        if running is not None:
            QMessageBox.warning(
                self,
                RESTORE_TITLE,
                f"Şu an çalışan bir iş var ({running}). Geri yüklemeden önce durdurun ya da "
                "bitmesini bekleyin.",
            )
            return
        db_path = sqlite_file_path(self._engine)
        if db_path is None:
            QMessageBox.warning(self, RESTORE_TITLE, RESTORE_UNAVAILABLE_TEXT)
            return

        try:
            restore_backup(
                zip_path,
                db_path,
                self._profile_path,
                backups_dir_for(db_path),
                dispose_engine=self._engine.dispose,
            )
        except BackupError as error:
            QMessageBox.warning(self, RESTORE_TITLE, str(error))
            return

        QMessageBox.information(self, RESTORE_TITLE, RESTARTING_TEXT)
        if not relaunch():
            QMessageBox.warning(self, RESTORE_TITLE, MANUAL_RESTART_TEXT)
        QApplication.quit()

    def _show_autopilot_tab(self) -> None:
        """Kurulum sihirbazının "Otopilot'a git" düğmesi (Ayarlar'dan yeniden açıldıysa)."""
        self.tab_widget.setCurrentIndex(AUTOPILOT_TAB_INDEX)

    def _on_tab_changed(self, index: int) -> None:
        if self._changing_tab:
            return

        previous = self._previous_tab_index
        if previous == SETTINGS_TAB_INDEX and not self.settings_tab.confirm_leave():
            self._changing_tab = True
            self.tab_widget.setCurrentIndex(previous)
            self._changing_tab = False
            return
        if previous == PROFILE_TAB_INDEX and not self.profile_tab.confirm_leave():
            self._changing_tab = True
            self.tab_widget.setCurrentIndex(previous)
            self._changing_tab = False
            return

        if index == SETTINGS_TAB_INDEX:
            self.settings_tab.reload()
        if index == PROFILE_TAB_INDEX:
            self.profile_tab.reload()
        if index == DASHBOARD_TAB_INDEX:
            self.dashboard_tab.reload()
        if index == AUTOPILOT_TAB_INDEX:
            self.autopilot_tab.reload()
        self._previous_tab_index = index

    def _stop_and_wait(
        self,
        *,
        is_running: Callable[[], bool],
        stop: Callable[[], None],
        wait: Callable[[int], bool],
        title: str,
        running_text: str,
    ) -> bool:
        """Bir arka plan çalışmasını (gönderim turu/Otopilot) durdurur ve bitmesini bekler.

        Çalışma yoksa hemen `True` döner. Çalışıyorsa önce onay istenir -- reddedilirse pencere
        KAPANMAMALI, bu yüzden `False` döner. Onaylanırsa durdurulur ve sürmekte olan mail
        bitene kadar (en fazla `SMTP_TIMEOUT` + tampon) beklenir -- süre aşılsa bile pencere
        yine de kapanır (bütünlük "sending" durumunun write-ahead işareti ve 5.2a'nın kurtarma
        butonlarıyla korunur), bu yüzden zaman aşımı da `True` döner.
        """
        if not is_running():
            return True

        answer = QMessageBox.question(
            self,
            title,
            running_text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return False

        stop()
        self.setEnabled(False)
        self.setWindowTitle(f"{APP_NAME} — durduruluyor, bekleniyor…")
        timeout_ms = (SMTP_TIMEOUT + 5) * 1000
        if not wait(timeout_ms):
            logger.warning(f"{title}: zaman aşımına uğradı, pencere yine de kapatılıyor.")
        return True

    def closeEvent(self, event: QCloseEvent) -> None:
        """Pencere kapanırken çalışan bir arka plan işi varsa iptal edip kısa süre bekler.

        Otopilot ya da bir gönderim turu (`queue_tab`'in "Gönderimi başlat" işi) sürüyorsa
        önce onay istenir; onaylanmazsa pencere KAPANMAZ (`_stop_and_wait`). İkisi normalde
        aynı anda çalışamaz (Otopilot çalışırken Onay Kuyruğu'nun "Gönderimi başlat"ı kilitli,
        bkz. `autopilot_tab.py`) ama her ikisi de bağımsız kontrol edilir.
        """
        if not self._stop_and_wait(
            is_running=self.autopilot_tab.is_autopilot_running,
            stop=self.autopilot_tab.stop_autopilot_run,
            wait=self.autopilot_tab.wait_for_autopilot_run,
            title="Otopilot çalışıyor",
            running_text="Otopilot çalışıyor. Kapatmak için önce durdurulması gerekiyor. "
            "Durdurup kapatmak istiyor musunuz?",
        ):
            event.ignore()
            return

        if not self._stop_and_wait(
            is_running=self.queue_tab.is_queue_running,
            stop=self.queue_tab.stop_queue_run,
            wait=self.queue_tab.wait_for_queue_run,
            title="Gönderim sürüyor",
            running_text="Bir gönderim turu sürüyor. Kapatmak için önce durdurulması gerekiyor. "
            "Durdurup kapatmak istiyor musunuz?",
        ):
            event.ignore()
            return

        self.companies_tab.cancel_running_worker()
        self.queue_tab.cancel_running_worker()
        self.replies_tab.cancel_running_worker()
        self.settings_tab.cancel_running_worker()
        self.update_banner.cancel_running_worker()
        super().closeEvent(event)
