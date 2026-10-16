"""İlk açılışta KVKK / 6563 bilgilendirmesi + onay penceresi (PLAN.md Adım 10.8b).

Asıl mantık `src/core/legal.py`'de (Qt YOK); bu dosya yalnızca metni gösteren ve onay kutusunu
işaretletip düğmeyi açan ince Qt kabuğudur. `ensure_legal_consent()` onay ZATEN varsa hiçbir şey
göstermez (bu bilgisayarda bir kez sorulur); reddedilirse uygulama yine açılır ama GERÇEK gönderim
yapılmaz (`smtp_sender.send_application` onay yoksa reddeder, test modu çalışır) ve bir sonraki
açılışta tekrar sorulur.
"""
import logging
from typing import Callable, Optional

from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine

from src.core.db import get_session
from src.core.legal import has_consent, load_legal_text, record_consent
from src.ui.legal_panel import CHECKBOX_TEXT, INTRO_TEXT, LegalConsentPanel  # noqa: F401

logger = logging.getLogger(__name__)

TITLE = "KVKK ve 6563 sayılı Kanun – Bilgilendirme"
ACCEPT_BUTTON_TEXT = "Onaylıyorum ve devam et"
DECLINE_BUTTON_TEXT = "Onaylamadan devam et (gerçek gönderim kapalı)"
LOAD_ERROR_TEXT = (
    "Bilgilendirme metni okunamadı, bu yüzden onay alınamıyor. Gerçek gönderim kapalı kalır.\n\n{error}"
)


class LegalConsentDialog(QDialog):
    """Metni gösterir; onay kutusu işaretlenmeden "Onaylıyorum" düğmesi kapalıdır."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(720, 600)

        self._panel = LegalConsentPanel(text, self)
        self._browser = self._panel.browser
        self._checkbox = self._panel.checkbox
        self._panel.consent_toggled.connect(self._on_checkbox_toggled)

        self._accept_button = QPushButton(ACCEPT_BUTTON_TEXT, self)
        self._accept_button.setEnabled(False)
        self._accept_button.clicked.connect(self.accept)

        self._decline_button = QPushButton(DECLINE_BUTTON_TEXT, self)
        self._decline_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._decline_button)
        buttons.addWidget(self._accept_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self._panel, 1)
        layout.addLayout(buttons)

    def _on_checkbox_toggled(self, checked: bool) -> None:
        self._accept_button.setEnabled(checked)


def _ask_with_dialog(text: str) -> bool:
    return LegalConsentDialog(text).exec() == QDialog.Accepted


def ensure_legal_consent(
    engine: Engine, *, ask: Callable[[str], bool] = _ask_with_dialog
) -> bool:
    """Onay yoksa bilgilendirmeyi gösterir; onaylanırsa kaydeder. Onay VAR mı döner.

    `ask(metin) -> bool` test için enjekte edilir. Metin okunamazsa (bozuk paket) onay
    ALINMAZ, hata loglanır ve `False` döner -- uygulama çökmez, gerçek gönderim kapalı kalır.
    """
    with get_session(engine) as session:
        if has_consent(session):
            return True

    try:
        text = load_legal_text()
    except OSError:
        logger.exception("KVKK/6563 bilgilendirme metni okunamadı")
        return False

    if not ask(text):
        logger.info("KVKK/6563 bilgilendirmesi onaylanmadı; gerçek gönderim kapalı kalacak")
        return False

    with get_session(engine) as session:
        record_consent(session)
        session.commit()
    return True
