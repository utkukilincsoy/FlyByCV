"""Sihirbaz sayfalarının ortak tabanı (PLAN.md Adım 10.2d).

Her sayfa üç şeyi bilir: ekranı veritabanından doldurmak (`load`), ekrandakini doğrulamak
(`validation_errors`, `onboarding_data.validate_*` çağırır) ve yazmak (`save`). Geri kalan --
"İleri"ye basınca doğrula, hata varsa göster ve sayfada kal, yoksa YAZ ve COMMIT ET -- burada
bir kez yazılır; böylece sihirbaz yarıda kapatılsa bile girilenler kaybolmaz (kaldığı yerden
devam, bkz. `onboarding_wizard.py`).
"""
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtWidgets import QMessageBox, QWizardPage
from sqlalchemy.engine import Engine
from sqlmodel import Session

from src.core.db import get_session
from src.ui.workers import JobWorker, run_with_progress

logger = logging.getLogger(__name__)

VALIDATION_TITLE = "Eksik veya hatalı bilgi"

#: Profil dosyası VAR ama bozuksa profille çalışan sayfaların gösterdiği ortak metin (`{error}`).
PROFILE_ERROR_TEXT = (
    "Profil dosyası okunamadı veya bozuk: {error}\n"
    "Dosya sihirbaz tarafından üzerine yazılmaz; önce dosyayı düzeltin ya da yedeğe alıp silin."
)


class OnboardingPage(QWizardPage):
    """Alt sınıflar `page_id` (onboarding_data.PAGE_*) verir ve `load`/`validation_errors`/`save`
    yöntemlerini doldurur."""

    page_id: str = ""

    def __init__(self, engine: Engine, profile_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._profile_path = profile_path
        #: Ağa çıkan "test et" işleri için tek işçi (`SettingsTab._worker` ile aynı kalıp, kural 8).
        self._worker: Optional[JobWorker] = None

    # -- arka plan işleri -----------------------------------------------------------------

    def guard_running(self) -> bool:
        """Zaten çalışan bir iş varsa kullanıcıyı uyarır ve True döner."""
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(
                self, "İş sürüyor", "Başka bir işlem bitmeden yeni bir işlem başlatılamaz."
            )
            return True
        return False

    def start_job(
        self, title: str, job: Callable[..., Any], on_done: Callable[[Any], None]
    ) -> None:
        """İşi ilerleme penceresiyle arka planda çalıştırır; arayüz donmaz."""

        def _finish(result: Any) -> None:
            self._worker_finished()
            on_done(result)

        self._worker = run_with_progress(self, title, job, _finish)
        self._worker.failed.connect(lambda _message: self._worker_finished())

    def _worker_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def cancel_running_worker(self) -> None:
        """Sihirbaz kapanırken çalışan iş varsa iptal eder (`SettingsTab.cancel_running_worker`)."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)

    def load(self) -> None:
        """Sayfa her gösterilmeden önce çağrılır: ekranı veritabanındaki hâle getirir."""

    def validation_errors(self) -> list[str]:
        """Ekrandaki ham değerleri doğrular; Türkçe hata listesi (boş = geçilebilir)."""
        return []

    def save(self, session: Session) -> None:
        """Ekrandakini yazar. COMMIT ETMEZ (taban `validatePage` eder)."""

    # -- QWizardPage ----------------------------------------------------------------------

    def initializePage(self) -> None:
        self.load()

    def validatePage(self) -> bool:
        errors = self.validation_errors()
        if errors:
            QMessageBox.warning(self, VALIDATION_TITLE, "\n".join(errors))
            return False
        with get_session(self._engine) as session:
            self.save(session)
            session.commit()
        return True
