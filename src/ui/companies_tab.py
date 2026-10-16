"""Şirketler sekmesi: arama kutusu, buton şeridi, tablo.

İçe aktarma / eksikleri tarama / seçilileri tarama / başvuru hazırlama butonları arka planda
`src/ui/jobs.py` + `src/ui/workers.py` üzerinden çalışır -- pencere hiçbir zaman donmaz
(CLAUDE.md kural 8).
"""
import logging
from functools import partial
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from src.company_finder.csv_import import RowError, add_company
from src.core.db import get_session
from src.core.settings import CV_MODE_OWN_PDF, KEY_CV_MODE, get_setting
from src.cv_builder.own_cv import has_own_cv
from src.cv_builder.profile_loader import load_profile
from src.ui.companies_model import CompanySearchProxy, CompanyTableModel
from src.ui.company_data import ids_missing_email, ids_ready_for_application, load_company_rows
from src.ui.dialogs import AddCompanyDialog, CompanyDetailDialog, ImportReportDialog
from src.ui.icons import get_icon, set_icon
from src.ui.jobs import BuildJobResult, EnrichJobResult, ImportJobResult, build_job, enrich_job, import_job
from src.ui.workers import JobWorker, run_with_progress

logger = logging.getLogger(__name__)


class CompaniesTab(QWidget):
    """Şirketler sekmesi: üstte arama + buton şeridi, altında tablo."""

    def __init__(
        self, engine: Engine, profile_path: Path, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._profile_path = profile_path
        self._worker: Optional[JobWorker] = None
        self._autopilot_active = False

        self._search_box = QLineEdit(self)
        self._search_box.setPlaceholderText("Ara: şirket adı, alan adı veya e-posta")
        self._search_box.addAction(get_icon("search"), QLineEdit.LeadingPosition)
        self._search_box.textChanged.connect(self._on_search_changed)

        self._add_button = QPushButton("Şirket ekle", self)
        set_icon(self._add_button, "add")
        self._add_button.clicked.connect(self._on_add_company)

        self._import_button = QPushButton("Excel/CSV içe aktar", self)
        set_icon(self._import_button, "import")
        self._import_button.clicked.connect(self._on_import)

        self._enrich_missing_button = QPushButton("E-postası eksikleri tara", self)
        self._enrich_missing_button.clicked.connect(self._on_enrich_missing)

        self._enrich_selected_button = QPushButton("Seçilileri tara", self)
        self._enrich_selected_button.clicked.connect(self._on_enrich_selected)

        self._build_button = QPushButton("Başvuru hazırla", self)
        set_icon(self._build_button, "prepare")
        self._build_button.clicked.connect(self._on_build_applications)

        button_row = QHBoxLayout()
        for button in (
            self._add_button,
            self._import_button,
            self._enrich_missing_button,
            self._enrich_selected_button,
            self._build_button,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)

        self._model = CompanyTableModel(self)
        self._proxy = CompanySearchProxy(self)
        self._proxy.setSourceModel(self._model)

        self._table = QTableView(self)
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSortingEnabled(True)
        self._table.doubleClicked.connect(self._on_row_double_clicked)

        layout = QVBoxLayout(self)
        layout.addWidget(self._search_box)
        layout.addLayout(button_row)
        layout.addWidget(self._table)

        self.reload()

    def reload(self) -> None:
        """Tabloyu veritabanından yeniden yükler."""
        with get_session(self._engine) as session:
            rows = load_company_rows(session)
        self._model.set_rows(rows)

    def selected_company_ids(self) -> list[int]:
        """Tabloda o an seçili olan satırların şirket id'leri."""
        ids = []
        for proxy_index in self._table.selectionModel().selectedRows():
            source_index = self._proxy.mapToSource(proxy_index)
            ids.append(self._model.row_at(source_index.row()).id)
        return ids

    def cancel_running_worker(self) -> None:
        """Çalışan bir arka plan işi varsa iptal eder ve kısa bir süre bekler.

        `MainWindow.closeEvent` tarafından çağrılır -- pencere kapanırken yarım kalmış bir
        tarama/başvuru hazırlama işleminin arkada devam etmesini önler.
        """
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)

    def is_job_running(self) -> bool:
        """Otopilot sekmesinin "Başlat"tan önce sorduğu soru: kendi bir işim çalışıyor mu?"""
        return self._worker is not None and self._worker.isRunning()

    def set_autopilot_active(self, active: bool) -> None:
        """Otopilot çalışırken toplu işleri (içe aktar/tara/hazırla) kilitler.

        "Şirket ekle" BİLİNÇLİ olarak dışarıda bırakıldı (PLAN Adım 9.6) -- tek satırlık, anlık
        bir yazma, "toplu iş" değil; Otopilot'un kullandığı satırlarla (Company/Application)
        çakışma riski yaratmıyor. Kendi arka plan işim (`self._worker`) sürüyorsa burada hiçbir
        şey DEĞİŞTİRİLMEZ -- `_set_actions_enabled` zaten hepsini kilitlemiş durumda, iş
        bitince `_worker_finished` bu bayrağı (`_autopilot_active`) okuyarak doğru duruma döner.
        """
        self._autopilot_active = active
        self._set_actions_enabled(self._worker is None)

    # -- iş başlatma / bitirme ortak yardımcıları ---------------------------------------

    def _guard_running(self) -> bool:
        """Zaten çalışan bir iş varsa (kendi işi ya da Otopilot) kullanıcıyı uyarır ve `True` döner."""
        if self._autopilot_active:
            QMessageBox.information(
                self, "Otopilot çalışıyor", "Otopilot çalışırken bu işlem kullanılamaz."
            )
            return True
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(
                self, "İş sürüyor", "Başka bir işlem bitmeden yeni bir işlem başlatılamaz."
            )
            return True
        return False

    def _start_job(
        self, title: str, job: Callable[..., Any], on_done: Callable[[Any], None]
    ) -> None:
        self._set_actions_enabled(False)

        def _finish(result: Any) -> None:
            self._worker_finished()
            on_done(result)

        self._worker = run_with_progress(self, title, job, _finish)
        self._worker.failed.connect(lambda _message: self._worker_finished())

    def _worker_finished(self) -> None:
        self._set_actions_enabled(True)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _set_actions_enabled(self, enabled: bool) -> None:
        self._add_button.setEnabled(enabled)
        bulk_enabled = enabled and not self._autopilot_active
        for button in (
            self._import_button,
            self._enrich_missing_button,
            self._enrich_selected_button,
            self._build_button,
        ):
            button.setEnabled(bulk_enabled)

    # -- arama / şirket ekleme / detay ----------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        self._proxy.set_search_text(text)

    def _on_add_company(self) -> None:
        dialog = AddCompanyDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return

        values = dialog.values()
        if not values["name"]:
            QMessageBox.warning(self, "Eksik bilgi", "Şirket adı boş olamaz.")
            return

        with get_session(self._engine) as session:
            try:
                add_company(
                    session,
                    name=values["name"],
                    website=values["website"],
                    email=values["email"],
                    city=values["city"],
                )
            except RowError as error:
                session.rollback()
                QMessageBox.warning(self, "Şirket eklenemedi", str(error))
                return
            session.commit()

        self.reload()

    def _on_row_double_clicked(self, proxy_index: QModelIndex) -> None:
        source_index = self._proxy.mapToSource(proxy_index)
        company_id = self._model.row_at(source_index.row()).id

        dialog = CompanyDetailDialog(self._engine, company_id, self)
        dialog.exec()
        if dialog.email_changed:
            self.reload()

    # -- içe aktarma -----------------------------------------------------------------

    def _on_import(self) -> None:
        if self._guard_running():
            return

        path_str, _ = QFileDialog.getOpenFileName(
            self, "Şirket listesi seç", "", "Şirket listesi (*.csv *.xlsx)"
        )
        if not path_str:
            return
        path = Path(path_str)

        def _after_real_run(result: ImportJobResult) -> None:
            if result.error:
                QMessageBox.warning(self, "İçe aktarılamadı", result.error)
                return
            QMessageBox.information(self, "İçe aktarma tamamlandı", result.report.summary())
            self.reload()

        def _after_dry_run(result: ImportJobResult) -> None:
            if result.error:
                QMessageBox.warning(self, "İçe aktarılamadı", result.error)
                return
            report_dialog = ImportReportDialog(result.report, dry_run=True, parent=self)
            if report_dialog.exec() != QDialog.Accepted:
                return
            self._start_job(
                "İçe aktarılıyor",
                partial(import_job, self._engine, path, dry_run=False),
                _after_real_run,
            )

        self._start_job(
            "İçe aktarma raporu hazırlanıyor",
            partial(import_job, self._engine, path, dry_run=True),
            _after_dry_run,
        )

    # -- eksik e-posta tarama ---------------------------------------------------------

    def _on_enrich_missing(self) -> None:
        if self._guard_running():
            return

        with get_session(self._engine) as session:
            ids = ids_missing_email(session)

        if not ids:
            QMessageBox.information(
                self, "Taranacak şirket yok", "E-postası/tanıtım metni eksik şirket kalmadı."
            )
            return

        self._start_job(
            "E-postası eksikleri taranıyor",
            partial(enrich_job, self._engine, ids),
            self._on_enrich_done,
        )

    def _on_enrich_selected(self) -> None:
        if self._guard_running():
            return

        ids = self.selected_company_ids()
        if not ids:
            QMessageBox.information(self, "Seçim yok", "Önce taranacak şirketleri tablodan seçin.")
            return

        self._start_job(
            "Seçili şirketler taranıyor",
            partial(enrich_job, self._engine, ids),
            self._on_enrich_done,
        )

    def _on_enrich_done(self, result: EnrichJobResult) -> None:
        self.reload()
        lines = [f"{result.processed} şirket tarandı, {result.email_added} e-posta bulundu."]
        if result.cancelled:
            lines.append("Tarama iptal edildi.")
        if result.failed:
            lines.append(f"{len(result.failed)} şirkette sorun oluştu.")
        QMessageBox.information(self, "Tarama tamamlandı", "\n".join(lines))

    # -- başvuru hazırlama -------------------------------------------------------------

    def _on_build_applications(self) -> None:
        if self._guard_running():
            return

        with get_session(self._engine) as session:
            cv_mode = get_setting(session, KEY_CV_MODE)
        if cv_mode == CV_MODE_OWN_PDF and not has_own_cv():
            QMessageBox.critical(
                self,
                "PDF yüklenmedi",
                "CV modu 'Kendi PDF'imi olduğu gibi ekle' olarak ayarlı ama henüz bir PDF "
                "yüklenmedi. Önce Ayarlar sekmesi > CV bölümünden bir PDF seçin.",
            )
            return

        try:
            profile = load_profile(self._profile_path)
        except (FileNotFoundError, ValueError) as error:
            QMessageBox.critical(
                self,
                "Profil okunamadı",
                f"{self._profile_path} okunamadı veya bozuk:\n\n{error}",
            )
            return

        ids = self.selected_company_ids()
        if not ids:
            with get_session(self._engine) as session:
                ids = ids_ready_for_application(session)
            if not ids:
                QMessageBox.information(
                    self,
                    "Hazırlanacak şirket yok",
                    "Başvurusu hazırlanacak şirket yok (hepsi hazır veya e-postası yok).",
                )
                return

        self._start_job(
            "Başvurular hazırlanıyor",
            partial(build_job, self._engine, ids, profile),
            self._on_build_done,
        )

    def _on_build_done(self, result: BuildJobResult) -> None:
        self.reload()
        lines = [f"{result.prepared} başvuru hazırlandı."]
        if result.cancelled:
            lines.append("İşlem iptal edildi.")
        if result.failed:
            lines.append(f"{len(result.failed)} şirkette hata oluştu.")
        QMessageBox.information(self, "Başvuru hazırlama tamamlandı", "\n".join(lines))
