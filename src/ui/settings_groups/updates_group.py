""""Güncellemeler" grubu (Faz 10 Adım 10.9d): mevcut sürüm, günlük kontrolü aç/kapa, "Şimdi kontrol et".

Ağa çıkan "Şimdi kontrol et" yalnızca `check_requested` yayar; işi barındıran pencere arka planda
çalıştırır (CLAUDE.md kural 8). Otomatik indirme/kurma yoktur: yeni sürüm bulunursa kullanıcı
sürüm sayfasına yönlendirilir.
"""
from typing import Any, Optional

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from src.core.version import __version__
from src.ui.jobs import UpdateCheckJobResult
from src.ui.settings_data import SettingsForm
from src.ui.settings_groups.common import hint_label

OPEN_PAGE_TEXT = "İndirme sayfasını aç"
CHECK_ENABLED_TEXT = "Yeni sürüm var mı diye günde bir kez kontrol et"
CHECK_NOW_TEXT = "Şimdi kontrol et"
CHECK_HINT_TEXT = (
    "Kontrol GitHub'a tek bir istek atar; kişisel veriniz, profiliniz ya da ayarlarınız "
    "gönderilmez (yalnızca uygulama adı/sürümü ve her internet isteğinde olduğu gibi IP adresiniz "
    "GitHub'a görünür). Bir şey otomatik indirilmez ya da kurulmaz; yeni sürümü siz indirirsiniz. "
    "'Şimdi kontrol et' bu kutu kapalı olsa da çalışır."
)


class UpdatesGroup(QGroupBox):
    check_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Güncellemeler", parent)
        form = QFormLayout()

        self.version_label = QLabel(f"Mevcut sürüm: {__version__}", self)
        form.addRow(self.version_label)

        self.check_enabled_check = QCheckBox(CHECK_ENABLED_TEXT, self)
        form.addRow(self.check_enabled_check)

        self.check_now_button = QPushButton(CHECK_NOW_TEXT, self)
        self.check_now_button.clicked.connect(lambda: self.check_requested.emit())
        form.addRow(self.check_now_button)

        form.addRow(hint_label(CHECK_HINT_TEXT, self))
        self.setLayout(form)

    def values(self) -> dict[str, Any]:
        return {"update_check_enabled": self.check_enabled_check.isChecked()}

    def set_values(self, form: SettingsForm) -> None:
        self.check_enabled_check.setChecked(form.update_check_enabled)


def show_update_check_result(parent: QWidget, result: UpdateCheckJobResult) -> None:
    """"Şimdi kontrol et" sonucunu gösterir; yeni sürüm varsa sürüm sayfasını açma seçeneği sunar."""
    if not result.configured:
        QMessageBox.information(
            parent,
            "Güncelleme kontrolü",
            "Güncelleme kontrolü bu sürümde henüz etkin değil (yayın adresi tanımlanmamış).",
        )
        return
    if result.info is None:
        QMessageBox.information(
            parent,
            "Güncelleme kontrolü",
            f"Yeni bir sürüm bulunamadı. {__version__} güncel olabilir ya da GitHub'a ulaşılamamış olabilir.",
        )
        return

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle("Yeni sürüm var")
    box.setText(f"Yeni sürüm var: {result.info.version} (şu an: {__version__}).")
    open_button = box.addButton(OPEN_PAGE_TEXT, QMessageBox.AcceptRole)
    box.addButton("Kapat", QMessageBox.RejectRole)
    box.exec()
    if box.clickedButton() is open_button:
        QDesktopServices.openUrl(QUrl(result.info.url))
