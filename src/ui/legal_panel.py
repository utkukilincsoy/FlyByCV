"""KVKK / 6563 bilgilendirme metni + onay kutusu -- yeniden kullanılabilir parça (PLAN.md 10.2c).

`LegalConsentDialog` (`legal_dialog.py`, ilk açılış) ve ileride kurulum sihirbazının ilk sayfası
aynı paneli kullanır: metin tek yerde, onay kutusunun yazısı tek yerde. Panel YALNIZCA gösterir ve
`consent_toggled(bool)` yayar; onayı kaydetmek (`core/legal.record_consent`) barındıran yerin işi.
"""
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QTextBrowser, QVBoxLayout, QWidget

INTRO_TEXT = (
    "Devam etmeden önce lütfen aşağıdaki kısa bilgilendirmeyi okuyun. Tüm verileriniz "
    "yalnızca bu bilgisayarda kalır."
)
CHECKBOX_TEXT = (
    "Okudum, anladım. Bunun hukuki tavsiye olmadığını ve gerçek gönderimlerin sorumluluğunun "
    "bende olduğunu kabul ediyorum."
)


class LegalConsentPanel(QWidget):
    """Giriş cümlesi, metin (Markdown) ve onay kutusu."""

    consent_toggled = Signal(bool)

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        intro = QLabel(INTRO_TEXT, self)
        intro.setWordWrap(True)

        self.browser = QTextBrowser(self)
        self.browser.setMarkdown(text)

        self.checkbox = QCheckBox(CHECKBOX_TEXT, self)
        self.checkbox.toggled.connect(self.consent_toggled)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(intro)
        layout.addWidget(self.browser, 1)
        layout.addWidget(self.checkbox)

    def is_consent_checked(self) -> bool:
        return self.checkbox.isChecked()
