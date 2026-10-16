""""Yedekleme" grubu: son yedeğin durumu, "Şimdi yedekle", "Yedekten geri yükle", "Yedek klasörünü aç".

Geri yüklemenin kendisi burada YAPILMAZ: grup yalnızca seçilen dosyayı onaylatıp
`restore_requested` yayar. Geri yüklemeden önce çalışan işlerin denetlenmesi, veritabanı
bağlantılarının kapatılması ve uygulamanın yeniden başlatılması ana pencerenin işidir
(`main_window.py::_on_restore_requested`).

"Şimdi yedekle" arayüz iş parçacığında çalışır: yerel bir SQLite dosyasının kopyası saniyenin
altında biter (ağa çıkmaz, CLAUDE.md kural 8'deki "uzun iş" değil).
"""
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from src.core.backup import (
    KIND_AUTO,
    KIND_MANUAL,
    KIND_PRE_RESTORE,
    BackupError,
    BackupInfo,
    backups_dir_for,
    create_backup,
    list_backups,
)
from src.ui.settings_groups.common import hint_label

BACKUP_NOW_TEXT = "Şimdi yedekle"
RESTORE_TEXT = "Yedekten geri yükle"
OPEN_FOLDER_TEXT = "Yedek klasörünü aç"
HINT_TEXT = (
    "Uygulama her gün ilk açılışta veritabanınızın ve profilinizin yedeğini alır; en yeni 7 yedek "
    "tutulur. Şifreler ve API anahtarları yedeğe girmez (Windows şifre kasasında kalır)."
)
NO_BACKUP_TEXT = "Henüz yedek yok."
UNAVAILABLE_TEXT = "Bu oturumda yedekleme kullanılamıyor (veritabanı bir dosyada değil)."
KIND_LABELS = {KIND_AUTO: "otomatik", KIND_MANUAL: "elle", KIND_PRE_RESTORE: "geri yükleme öncesi"}


def status_text(backups: list[BackupInfo]) -> str:
    """Durum satırı: en yeni yedeğin zamanı ve türü + toplam yedek sayısı."""
    if not backups:
        return NO_BACKUP_TEXT
    latest = backups[0]
    kind = KIND_LABELS.get(latest.kind, latest.kind)
    return f"Son yedek: {latest.created_at:%d.%m.%Y %H:%M} ({kind}) · {len(backups)} yedek"


def confirm_restore_text(file_name: str) -> str:
    return (
        f"“{file_name}” geri yüklenecek. Şu anki şirketleriniz, başvurularınız, ayarlarınız ve "
        "profiliniz bu yedektekiyle değiştirilecek.\n\n"
        "Şu anki verileriniz önce ayrıca yedeklenecek; uygulama sonra yeniden başlatılacak.\n\n"
        "Devam edilsin mi?"
    )


class BackupGroup(QGroupBox):
    #: Kullanıcı bir yedek seçip geri yüklemeyi onayladı (`Path`).
    restore_requested = Signal(object)

    def __init__(
        self, db_path: Optional[Path], profile_path: Path, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__("Yedekleme", parent)
        self._db_path = db_path
        self._profile_path = profile_path
        self._backups_dir = backups_dir_for(db_path) if db_path is not None else None

        form = QFormLayout()
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        form.addRow(self.status_label)

        self.backup_now_button = QPushButton(BACKUP_NOW_TEXT, self)
        self.restore_button = QPushButton(RESTORE_TEXT, self)
        self.open_folder_button = QPushButton(OPEN_FOLDER_TEXT, self)
        self.backup_now_button.clicked.connect(self.on_backup_now)
        self.restore_button.clicked.connect(self.on_restore)
        self.open_folder_button.clicked.connect(self.on_open_folder)
        buttons = QHBoxLayout()
        for button in (self.backup_now_button, self.restore_button, self.open_folder_button):
            button.setEnabled(self._backups_dir is not None)
            buttons.addWidget(button)
        buttons.addStretch(1)
        form.addRow(buttons)

        form.addRow(hint_label(HINT_TEXT, self))
        self.setLayout(form)
        self.refresh_status()

    def refresh_status(self) -> None:
        if self._backups_dir is None:
            self.status_label.setText(UNAVAILABLE_TEXT)
            return
        self.status_label.setText(status_text(list_backups(self._backups_dir)))

    def on_backup_now(self) -> None:
        if self._db_path is None or self._backups_dir is None:
            return
        try:
            path = create_backup(self._db_path, self._profile_path, self._backups_dir, kind=KIND_MANUAL)
        except BackupError as error:
            QMessageBox.warning(self, "Yedekleme", str(error))
            return
        self.refresh_status()
        QMessageBox.information(self, "Yedekleme", f"Yedek alındı: {path.name}")

    def on_restore(self) -> None:
        if self._db_path is None or self._backups_dir is None:
            return
        start_dir = self._backups_dir if self._backups_dir.is_dir() else self._db_path.parent
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Geri yüklenecek yedeği seç", str(start_dir), "Yedek dosyaları (*.zip)"
        )
        if not path_str:
            return
        path = Path(path_str)
        answer = QMessageBox.question(
            self,
            RESTORE_TEXT,
            confirm_restore_text(path.name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.restore_requested.emit(path)

    def on_open_folder(self) -> None:
        if self._backups_dir is None:
            return
        self._backups_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._backups_dir)))
