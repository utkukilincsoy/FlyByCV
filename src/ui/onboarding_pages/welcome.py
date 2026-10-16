"""Sihirbazın ilk sayfası: hoş geldin + KVKK / 6563 bilgilendirmesi ve onayı (PLAN.md Adım 10.2e).

Metin ve onay kutusu `LegalConsentPanel`den gelir (`LegalConsentDialog` ile aynı parça). Onay,
"İleri"ye basılınca `core/legal.record_consent` ile kaydedilir -- ilk açılışta ayrıca KVKK
penceresi sorulmaz (bkz. `startup.py`). Metin okunamazsa onay ALINMAZ (kural 12): sayfa hata
gösterir ve ilerletmez.
"""
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine
from sqlmodel import Session

from src.core.db import get_session
from src.core.legal import has_consent, load_legal_text, record_consent
from src.ui.legal_panel import LegalConsentPanel
from src.ui.onboarding_data import PAGE_WELCOME, validate_welcome
from src.ui.onboarding_pages.base import OnboardingPage

logger = logging.getLogger(__name__)

TITLE = "FlybyCV'ye hoş geldiniz"
WELCOME_TEXT = (
    "FlybyCV, mesleğinize uygun şirketleri bulur, e-posta adreslerini çıkarır ve "
    "CV'nizi ekleyerek başvuru e-postasını gönderir; gelen cevapları da sınıflandırır. "
    "Bu sihirbaz birkaç adımda gerekli ayarları yapar.\n\n"
    "\"İleri\"ye bastığınızda o adımda girdikleriniz kaydedilir. Sihirbazı kapatırsanız "
    "uygulamayı bir sonraki açışınızda kaldığınız yerden devam edersiniz."
)
LOAD_ERROR_TEXT = (
    "Bilgilendirme metni okunamadı, bu yüzden onay alınamıyor ve kuruluma devam edilemiyor. "
    "Uygulama dosyaları eksik veya bozuk olabilir; uygulamayı yeniden kurmayı deneyin."
)


class WelcomePage(OnboardingPage):
    page_id = PAGE_WELCOME

    def __init__(
        self, engine: Engine, profile_path: Path, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(engine, profile_path, parent)
        self.setTitle(TITLE)

        intro = QLabel(WELCOME_TEXT, self)
        intro.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)

        self._panel: Optional[LegalConsentPanel] = None
        try:
            text = load_legal_text()
        except OSError:
            logger.exception("KVKK/6563 bilgilendirme metni okunamadı")
            error_label = QLabel(LOAD_ERROR_TEXT, self)
            error_label.setWordWrap(True)
            layout.addWidget(error_label)
            layout.addStretch(1)
        else:
            self._panel = LegalConsentPanel(text, self)
            layout.addWidget(self._panel, 1)

    def consent_given(self) -> bool:
        return self._panel is not None and self._panel.is_consent_checked()

    # -- OnboardingPage -------------------------------------------------------------------

    def load(self) -> None:
        """Kaldığı yerden dönen kullanıcı onayı zaten verdiyse kutu işaretli gelir."""
        if self._panel is None:
            return
        with get_session(self._engine) as session:
            already_consented = has_consent(session)
        if already_consented:
            self._panel.checkbox.setChecked(True)

    def validation_errors(self) -> list[str]:
        return validate_welcome(self.consent_given())

    def save(self, session: Session) -> None:
        if not has_consent(session):
            record_consent(session)
