"""\"Google Places\" grubu (isteğe bağlı, ücretli olabilir): düğme, aylık sınır, anahtar, test.

Anahtarı kaydet/sil ve rehber penceresi grubun içinde; ağa çıkan "Anahtarı test et" ise yalnızca
`test_requested` sinyali yayar (işi barındıran pencere arka planda çalıştırır, kural 8).
Anahtar durumu değişince `key_status_changed(kayıtlı_mı)` yayılır (kaynak özeti buna bağlı).
"""
from typing import Any, Callable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.company_finder.google_places import PLACES_HARD_MAX_MONTHLY
from src.company_finder.source_selector import GOOGLE_PLACES_API_KEY_NAME
from src.core.secrets import get_api_key
from src.ui.jobs import PlacesKeyTestJobResult
from src.ui.settings_data import (
    PLACES_KEY_GUIDE_TITLE_TR,
    PLACES_KEY_GUIDE_TR,
    SettingsForm,
    format_places_usage,
    format_secret_status,
)
from src.ui.settings_groups.common import add_key_row, hint_label
from src.ui.settings_groups.secret_prompts import delete_api_key_with_notice, prompt_save_api_key


class PlacesGroup(QGroupBox):
    test_requested = Signal()
    key_status_changed = Signal(bool)

    def __init__(
        self, used_this_month: Callable[[], int], parent: Optional[QWidget] = None
    ) -> None:
        """`used_this_month()`: bu ay Google'a atılan istek sayısı (veritabanından, çağıran verir)."""
        super().__init__("Google Places (isteğe bağlı, ÜCRETLİ olabilir)", parent)
        self._used_this_month = used_this_month
        form = QFormLayout()

        warning = QLabel(
            "Google Places daha çok firma bulur ama Google'ın ücretli servisidir: ücretsiz "
            "kotayı aşarsanız Google hesabınıza fatura çıkabilir. Kapalı kalması önerilir; "
            "yalnızca OpenStreetMap ve CSV yetmezse açın. Önce 'Anahtar nasıl alınır?' "
            "rehberindeki günlük kota adımını yapın.",
            self,
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #b45309;")
        form.addRow(warning)

        self.use_places_check = QCheckBox("Google Places kullan", self)
        form.addRow(self.use_places_check)

        self.places_limit_spin = QSpinBox(self)
        self.places_limit_spin.setRange(0, PLACES_HARD_MAX_MONTHLY)
        self.places_limit_spin.setSuffix(" istek")
        self.places_limit_spin.setToolTip(
            f"Uygulamanın bir ayda Google'a atacağı en çok istek. Kodda sabit tavan: {PLACES_HARD_MAX_MONTHLY}."
        )
        form.addRow("Aylık istek sınırı", self.places_limit_spin)

        self.places_usage_label = hint_label("", self, wrap=False)
        form.addRow(self.places_usage_label)
        self.places_limit_spin.valueChanged.connect(self.refresh_usage)

        self.key_status_label = add_key_row(
            form,
            self,
            "Google Places API anahtarı",
            on_save=self.save_key,
            on_delete=self.delete_key,
        )

        self.test_button = QPushButton("Anahtarı test et", self)
        self.test_button.clicked.connect(lambda: self.test_requested.emit())
        self.guide_button = QPushButton("Anahtar nasıl alınır?", self)
        self.guide_button.clicked.connect(self.show_guide)
        button_row = QHBoxLayout()
        button_row.addWidget(self.test_button)
        button_row.addWidget(self.guide_button)
        button_row.addStretch(1)
        button_widget = QWidget(self)
        button_widget.setLayout(button_row)
        form.addRow(button_widget)

        test_hint = hint_label(
            "Test yalnızca kimlik (places.id) ister; Google'ın ücretsiz sınıfındadır, aylık "
            "sayaca da sayılmaz. Anahtar yalnızca Windows şifre kasasında durur.",
            self,
        )
        form.addRow(test_hint)

        self.setLayout(form)

    def refresh_usage(self, *_args) -> None:
        self.places_usage_label.setText(
            format_places_usage(self._used_this_month(), self.places_limit_spin.value())
        )

    def refresh_key_status(self) -> bool:
        saved = bool(get_api_key(GOOGLE_PLACES_API_KEY_NAME))
        self.key_status_label.setText(format_secret_status(saved))
        self.key_status_changed.emit(saved)
        return saved

    def save_key(self) -> None:
        if prompt_save_api_key(self, GOOGLE_PLACES_API_KEY_NAME, "Google Places API anahtarı"):
            self.refresh_key_status()

    def delete_key(self) -> None:
        delete_api_key_with_notice(
            self, GOOGLE_PLACES_API_KEY_NAME, self.refresh_key_status, "Google Places"
        )

    def show_guide(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(PLACES_KEY_GUIDE_TITLE_TR)
        dialog.resize(680, 620)
        text = QPlainTextEdit(dialog)
        text.setReadOnly(True)
        text.setPlainText(PLACES_KEY_GUIDE_TR)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text)
        layout.addWidget(buttons)
        dialog.exec()

    def values(self) -> dict[str, Any]:
        return {
            "use_google_places": self.use_places_check.isChecked(),
            "places_monthly_limit": self.places_limit_spin.value(),
        }

    def set_values(self, form: SettingsForm) -> None:
        self.use_places_check.setChecked(form.use_google_places)
        self.places_limit_spin.setValue(form.places_monthly_limit)


def show_places_test_result(parent: QWidget, result: PlacesKeyTestJobResult) -> None:
    """\"Anahtarı test et\" işinin sonucunu kullanıcıya gösterir."""
    if result.no_key:
        QMessageBox.warning(
            parent,
            "Anahtar kayıtlı değil",
            "Google Places için kayıtlı bir API anahtarı yok. Önce anahtarı kaydedin.",
        )
        return
    if result.error:
        QMessageBox.warning(parent, "Anahtar testi başarısız", result.error)
        return
    QMessageBox.information(
        parent,
        "Anahtar testi başarılı",
        "Google Places anahtarı çalışıyor. Test yalnızca kimlik (places.id) istedi; aylık "
        "sayaca sayılmadı. Kullanmak için 'Google Places kullan' kutusunu işaretleyip "
        "Kaydet'e basın.",
    )
