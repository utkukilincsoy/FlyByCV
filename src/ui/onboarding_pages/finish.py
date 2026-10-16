"""Sihirbazın bitiş sayfası (PLAN.md Adım 10.2m).

Kurulumun özetini (kişi, hedef/meslek/şehir, CV, mail hesabı, kaynaklar, şablon konusu) ve test modunun
durumunu gösterir. **Test modu AÇIK kalır:** bu sayfa hiçbir ayarı değiştirmez (taze kurulumda varsayılan
zaten AÇIK; Ayarlar'dan yeniden açılan sihirbazda önceki durum korunur ve KAPALIysa kırmızı uyarıyla
söylenir). Gerçek gönderime geçmek için açık bir "Gerçek gönderime geç" düğmesi vardır (KVKK onayı +
"Anladım" kutulu pencere, `src/ui/test_mode_dialog.py`); "Test moduna dön" ile geri alınır. Son düğme "Otopilot'a git": ana pencere zaten
Otopilot sekmesiyle açılır (ilk sekme), sihirbaz Ayarlar'dan açıldıysa `SettingsTab` sekmeyi değiştirir.
"""
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QWizard,
)
from sqlalchemy.engine import Engine

from src.core.db import get_session
from src.ui.onboarding_data import PAGE_FINISH
from src.ui.onboarding_pages.base import OnboardingPage
from src.ui.onboarding_summary import HOW_TO_GO_LIVE_TEXT, FinishSummary, build_finish_summary
from src.ui.settings_groups.common import hint_label
from src.ui.test_mode_data import BACK_TO_TEST_BUTTON_TEXT, GO_LIVE_BUTTON_TEXT
from src.ui.test_mode_dialog import switch_test_mode
from src.ui.wheel_guard import install_wheel_guard

logger = logging.getLogger(__name__)

TITLE = "Kurulum tamam"
INTRO_TEXT = (
    "Her şey hazır. Aşağıda seçimlerinizin özeti var; hepsini Ayarlar ve Profil sekmelerinden sonradan "
    "değiştirebilirsiniz. \"Otopilot'a git\"e basınca Otopilot sekmesi açılır; \"Başlat\" ile firmaları "
    "bulup başvuruları hazırlamaya başlayabilirsiniz."
)
FINISH_BUTTON_TEXT = "Otopilot'a git"
SUMMARY_TITLE = "Kurulum özeti"
TEST_MODE_TITLE = "Test modu"
SUMMARY_ERROR_TEXT = "Özet okunamadı: {error}. Bu, kurulumun tamamlanmasını engellemez."
TEST_MODE_ON_STYLE = "color: #15803d; font-weight: bold;"
TEST_MODE_OFF_STYLE = "color: #b91c1c; font-weight: bold;"


class FinishPage(OnboardingPage):
    page_id = PAGE_FINISH

    def __init__(self, engine: Engine, profile_path: Path, parent: Optional[QWidget] = None) -> None:
        super().__init__(engine, profile_path, parent)
        self._test_mode = True
        self.setTitle(TITLE)
        self.setFinalPage(True)
        self.setButtonText(QWizard.FinishButton, FINISH_BUTTON_TEXT)

        intro = QLabel(INTRO_TEXT, self)
        intro.setWordWrap(True)

        self._summary_form = QFormLayout()
        summary_box = QGroupBox(SUMMARY_TITLE, self)
        summary_box.setLayout(self._summary_form)

        self.test_mode_label = QLabel(self)
        self.test_mode_label.setWordWrap(True)
        test_mode_layout = QVBoxLayout()
        test_mode_layout.addWidget(self.test_mode_label)
        test_mode_layout.addWidget(hint_label(HOW_TO_GO_LIVE_TEXT, self))
        # Bu sayfa kendiliğinden test modunu DEĞİŞTİRMEZ; yalnızca bu düğme, açık onayla (KVKK onayı +
        # "Anladım" kutusu) değiştirir. Sihirbazın "Bitir" düğmesiyle karışmasın diye varsayılan değil.
        self.test_mode_button = QPushButton(GO_LIVE_BUTTON_TEXT, self)
        self.test_mode_button.setAutoDefault(False)
        self.test_mode_button.clicked.connect(self._on_test_mode_button_clicked)
        test_mode_layout.addWidget(self.test_mode_button, 0, Qt.AlignLeft)
        test_mode_box = QGroupBox(TEST_MODE_TITLE, self)
        test_mode_box.setLayout(test_mode_layout)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(intro)
        content_layout.addWidget(summary_box)
        content_layout.addWidget(test_mode_box)
        content_layout.addStretch(1)
        install_wheel_guard(content)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    def _clear_summary(self) -> None:
        while self._summary_form.rowCount():
            self._summary_form.removeRow(0)

    def _show_summary(self, summary: FinishSummary) -> None:
        self._clear_summary()
        for line in summary.lines:
            value = QLabel(line.value, self)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._summary_form.addRow(f"{line.label}:", value)
        self.test_mode_label.setText(summary.test_mode_text)
        self.test_mode_label.setStyleSheet(TEST_MODE_ON_STYLE if summary.test_mode else TEST_MODE_OFF_STYLE)
        self._test_mode = summary.test_mode
        self.test_mode_button.setText(GO_LIVE_BUTTON_TEXT if summary.test_mode else BACK_TO_TEST_BUTTON_TEXT)
        self.test_mode_button.setEnabled(True)

    def _on_test_mode_button_clicked(self) -> None:
        if switch_test_mode(self, self._engine, want_test_mode=not self._test_mode):
            self.load()  # özet + etiket + düğme yazılan değerden yeniden kurulur

    # -- OnboardingPage -------------------------------------------------------------------

    def load(self) -> None:
        try:
            with get_session(self._engine) as session:
                summary = build_finish_summary(session, self._profile_path)
        except Exception as error:  # özet yalnızca bilgi: hata kurulumu bitirmeyi engellemesin
            logger.exception("Bitiş özeti oluşturulamadı")
            self._clear_summary()
            self._summary_form.addRow(QLabel(SUMMARY_ERROR_TEXT.format(error=error), self))
            self.test_mode_label.setText("")
            self.test_mode_button.setEnabled(False)  # durum okunamadı: kör değişiklik yapılmaz
            return
        self._show_summary(summary)
