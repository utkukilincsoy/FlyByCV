"""İlk açılış sihirbazının iskeleti (PLAN.md Adım 10.2d): `QWizard` + geri/ileri + kayıt.

Sayfalar `onboarding_pages/` altında; burası onları `PAGE_ORDER` sırasıyla dizer ve üç şeyi yapar:
- Sayfa değiştikçe (İleri VE Geri) kaldığı yeri `onboarding_step` ayarına yazar; sihirbaz yarıda
  kapatılırsa sonraki açılışta o sayfadan devam eder. Her sayfanın kendi girdisi ise o sayfanın
  "İleri"sinde kaydedilir (`OnboardingPage.validatePage`).
- Son sayfada "Bitir" ile kapanınca `onboarding_completed`'ı True yapar -- sihirbaz bir daha
  kendiliğinden açılmaz.
- `reopen=True` (Ayarlar'daki "Kurulum sihirbazını yeniden aç"): var olan kullanıcı için sihirbaz
  baştan açılır, kaldığı yer YAZILMAZ ve `onboarding_completed` hiçbir an False olmaz; yani
  yarıda kapatmak bir sonraki açılışta sihirbazı kendiliğinden açtırmaz.
"""
import logging
from pathlib import Path
from typing import Mapping, Optional

from PySide6.QtWidgets import QDialog, QWidget, QWizard
from sqlalchemy.engine import Engine

from src.core.db import get_session
from src.ui.onboarding_data import (
    PAGE_ORDER,
    complete_onboarding,
    pick_start_page,
    resume_page_id,
    save_step,
)
from src.ui.onboarding_pages import PAGE_FACTORIES, PageFactory

logger = logging.getLogger(__name__)

WINDOW_TITLE = "Kurulum sihirbazı"
BACK_TEXT = "< Geri"
NEXT_TEXT = "İleri >"
FINISH_TEXT = "Bitir"
CANCEL_TEXT = "Kapat"


class OnboardingWizard(QWizard):
    """Sayfa kimliği (`QWizard` id'si) = sayfanın `PAGE_ORDER`'daki sırası."""

    def __init__(
        self,
        engine: Engine,
        profile_path: Path,
        *,
        reopen: bool = False,
        page_factories: Optional[Mapping[str, PageFactory]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        factories = PAGE_FACTORIES if page_factories is None else page_factories
        unknown = set(factories) - set(PAGE_ORDER)
        if unknown:
            raise ValueError(f"Bilinmeyen sihirbaz sayfaları: {sorted(unknown)}")

        self._engine = engine
        self._reopen = reopen
        self.page_ids = [page_id for page_id in PAGE_ORDER if page_id in factories]

        self.setWindowTitle(WINDOW_TITLE)
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setButtonText(QWizard.BackButton, BACK_TEXT)
        self.setButtonText(QWizard.NextButton, NEXT_TEXT)
        self.setButtonText(QWizard.FinishButton, FINISH_TEXT)
        self.setButtonText(QWizard.CancelButton, CANCEL_TEXT)
        self.resize(780, 640)

        for page_id in self.page_ids:
            self.setPage(PAGE_ORDER.index(page_id), factories[page_id](engine, profile_path))

        self.setStartId(PAGE_ORDER.index(self._start_page_id()))
        if not reopen:
            self.currentIdChanged.connect(self._on_page_changed)

    def _start_page_id(self) -> str:
        if self._reopen:
            return self.page_ids[0]
        with get_session(self._engine) as session:
            resume_id = resume_page_id(session)
        return pick_start_page(resume_id, self.page_ids)

    def current_page_id(self) -> str:
        return PAGE_ORDER[self.currentId()]

    def _on_page_changed(self, index: int) -> None:
        if index < 0:
            return
        with get_session(self._engine) as session:
            save_step(session, PAGE_ORDER[index])
            session.commit()

    def done(self, result: int) -> None:
        # `QWizard.done`, kabul ederken son sayfayı doğrular ve doğrulama geçmezse pencereyi
        # KAPATMAZ; bu yüzden "tamamlandı" yalnızca gerçekten kapandıktan sonra yazılır.
        super().done(result)
        if not self.isVisible():
            for page_id in self.page_ids:
                self.page(PAGE_ORDER.index(page_id)).cancel_running_worker()
        if result == QDialog.Accepted and self.result() == QDialog.Accepted:
            with get_session(self._engine) as session:
                complete_onboarding(session)
                session.commit()
            logger.info("İlk açılış sihirbazı tamamlandı")


def run_onboarding_wizard(
    engine: Engine,
    profile_path: Path,
    *,
    reopen: bool = False,
    parent: Optional[QWidget] = None,
) -> bool:
    """Sihirbazı gösterir; TAMAMLANDIYSA True. `startup.WizardRunner` ile uyumludur."""
    wizard = OnboardingWizard(engine, profile_path, reopen=reopen, parent=parent)
    return wizard.exec() == QDialog.Accepted
