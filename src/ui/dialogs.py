"""Şirketler sekmesi için diyalog pencereleri: içe aktarma raporu, şirket ekleme, şirket ayrıntısı."""
import logging
from html import escape
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from src.company_finder.csv_import import ImportReport
from src.company_finder.email_extractor import is_role_address
from src.core.db import get_session
from src.core.models import Company
from src.ui.company_data import WARN_NOT_ROLE_ADDRESS, use_email

logger = logging.getLogger(__name__)


def format_report_lines(report: ImportReport, *, dry_run: bool) -> list[str]:
    """İçe aktarma raporunun Türkçe satırları (Qt'siz, test edilebilir).

    `scripts/import_companies.py`'deki `_print_report`/`_print_notes` ile aynı içerik.
    """
    lines: list[str] = []
    if dry_run:
        lines.append("MOD: DENEME – veritabanına hiçbir şey yazılmadı")

    if report.added:
        lines.append(f"Eklenen ({len(report.added)}):")
        for added in report.added:
            lines.append(f"  + {added.row}. satır: {added.name} ({added.domain})")

    if report.warnings:
        lines.append(f"Uyarı ({len(report.warnings)}):")
        for note in report.warnings:
            lines.append(f"  ! {note.row}. satır: {note.name or '(ad yok)'} — {note.reason}")

    if report.skipped:
        lines.append(f"Atlanan ({len(report.skipped)}):")
        for note in report.skipped:
            lines.append(f"  - {note.row}. satır: {note.name or '(ad yok)'} — {note.reason}")

    lines.append(report.summary())
    return lines


class ImportReportDialog(QDialog):
    """İçe aktarma raporunu gösterir.

    `dry_run=True` iken butonlar "İçe aktar" / "Vazgeç"; `False` iken tek "Kapat" butonu.
    """

    def __init__(
        self, report: ImportReport, *, dry_run: bool, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("İçe aktarma raporu" if dry_run else "İçe aktarma tamamlandı")
        self.resize(520, 400)

        text_view = QTextEdit(self)
        text_view.setReadOnly(True)
        text_view.setPlainText("\n".join(format_report_lines(report, dry_run=dry_run)))

        layout = QVBoxLayout(self)
        layout.addWidget(text_view)

        if dry_run:
            buttons = QDialogButtonBox(self)
            buttons.addButton("İçe aktar", QDialogButtonBox.AcceptRole)
            buttons.addButton("Vazgeç", QDialogButtonBox.RejectRole)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
        else:
            buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
            buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)


class AddCompanyDialog(QDialog):
    """"Şirket ekle" formu: ad, site, e-posta, şehir."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Şirket ekle")

        self._name_edit = QLineEdit(self)
        self._website_edit = QLineEdit(self)
        self._email_edit = QLineEdit(self)
        self._city_edit = QLineEdit(self)

        form = QFormLayout()
        form.addRow("Ad*", self._name_edit)
        form.addRow("Web sitesi", self._website_edit)
        form.addRow("E-posta", self._email_edit)
        form.addRow("Şehir", self._city_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict[str, Optional[str]]:
        """Form alanlarını, boş girişler `None` olacak şekilde döndürür (ad hariç)."""

        def cleaned(edit: QLineEdit) -> Optional[str]:
            text = edit.text().strip()
            return text or None

        return {
            "name": cleaned(self._name_edit) or "",
            "website": cleaned(self._website_edit),
            "email": cleaned(self._email_edit),
            "city": cleaned(self._city_edit),
        }


class CompanyDetailDialog(QDialog):
    """Çift tıklanan şirketin ayrıntı penceresi.

    Şirket bilgileri, tanıtım metni ve bulunan e-posta adresleri listesi gösterilir.
    Listeden bir adres seçilip "Bu adresi kullan" ile şirketin ana adresi yapılabilir;
    rol adresi olmayan bir adres seçilirse uyarı gösterilir (CLAUDE.md kural 10).
    Kendi kısa ömürlü oturumunu açar (GUI iş parçacığında çalışır).
    """

    def __init__(
        self, engine: Engine, company_id: int, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._company_id = company_id
        self._email_changed = False

        self.setWindowTitle("Şirket ayrıntısı")
        self.resize(480, 420)

        self._info_label = QLabel(self)
        self._info_label.setWordWrap(True)
        self._info_label.setTextFormat(Qt.RichText)

        self._about_view = QTextEdit(self)
        self._about_view.setReadOnly(True)

        self._email_list = QListWidget(self)

        use_button = QPushButton("Bu adresi kullan", self)
        use_button.clicked.connect(self._on_use_selected_email)

        close_button = QPushButton("Kapat", self)
        close_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self._info_label)
        layout.addWidget(QLabel("Tanıtım metni:"))
        layout.addWidget(self._about_view)
        layout.addWidget(QLabel("Bulunan adresler:"))
        layout.addWidget(self._email_list)
        layout.addWidget(use_button)
        layout.addWidget(close_button)

        self._load()

    @property
    def email_changed(self) -> bool:
        """Bu pencerede e-posta değiştirildiyse `True` -- çağıran sekme tabloyu yenilemeli."""
        return self._email_changed

    def _load(self) -> None:
        with get_session(self._engine) as session:
            company = session.get(Company, self._company_id)
            if company is None:
                self._info_label.setText("Şirket bulunamadı (silinmiş olabilir).")
                return

            # Değerler siteden/CSV'den gelir: RichText etikete kaçışsız basılırsa içlerindeki
            # HTML (<a>, <img>, <b> ...) işlenip görüntüyü bozabilir/yanıltabilir.
            self._info_label.setText(
                f"<b>{escape(company.name)}</b><br>"
                f"Alan adı: {escape(company.domain)}<br>"
                f"Şehir: {escape(company.city or '—')}<br>"
                f"E-posta: {escape(company.email or '—')}"
            )
            self._about_view.setPlainText(company.about_text or "")

            self._email_list.clear()
            for entry in company.found_emails:
                role_text = "rol adresi" if entry["is_role"] else "kişi adı gibi görünüyor"
                item = QListWidgetItem(f"{entry['email']} ({role_text}) — {entry['source_url']}")
                item.setData(Qt.UserRole, entry["email"])
                self._email_list.addItem(item)

    def _on_use_selected_email(self) -> None:
        item = self._email_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Adres seçilmedi", "Önce listeden bir adres seçin.")
            return
        email = item.data(Qt.UserRole)

        if not is_role_address(email):
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Kişi adresi")
            box.setText(WARN_NOT_ROLE_ADDRESS)
            yes_button = box.addButton("Evet, yine de kullan", QMessageBox.YesRole)
            box.addButton("Vazgeç", QMessageBox.NoRole)
            box.exec()
            if box.clickedButton() is not yes_button:
                return

        with get_session(self._engine) as session:
            company = session.get(Company, self._company_id)
            if company is None:
                return
            try:
                use_email(session, company, email)
            except ValueError as error:
                QMessageBox.critical(self, "Adres kullanılamadı", str(error))
                return
            session.commit()

        self._email_changed = True
        self._load()
