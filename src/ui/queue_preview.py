"""Onay Kuyruğu sekmesi için sağdaki önizleme paneli: alıcı, konu, gövde, etiket, CV PDF'i.

Veritabanına dokunmaz -- tüm metinler `src/ui/queue_data.py`'nin saf fonksiyonlarından gelir;
`show_row()`'a geçilen değerler çağıranın (`queue_tab.py`) açtığı kısa oturumdan üretilir.
"""
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.models import STATUS_SENDING
from src.ui.queue_data import (
    PERSON_ADDRESS_WARNING_TEXT,
    SENDING_WARNING_TEXT,
    ApplicationRow,
    format_recipient_line,
    format_tags_line,
    is_person_address,
)

logger = logging.getLogger(__name__)

# QtPdf, PySide6-Essentials paketlemesinde (PyInstaller) düşebilir; o durumda gömülü önizleme
# yerine "PDF'i aç" butonuyla varsayılan programda açmaya düşülür.
try:
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPdfWidgets import QPdfView

    PDF_VIEW_AVAILABLE = True
except ImportError:  # pragma: no cover - bu venv'de QtPdf mevcut, sadece paketleme yedeği
    PDF_VIEW_AVAILABLE = False

RECIPIENT_TEST_STYLE = "background-color: #F9C846; color: #000000; padding: 6px; font-weight: bold;"
RECIPIENT_REAL_STYLE = "background-color: #C62828; color: #FFFFFF; padding: 6px; font-weight: bold;"
SENDING_WARNING_STYLE = "background-color: #F9C846; color: #000000; padding: 6px; font-weight: bold;"

PDF_UNAVAILABLE_TEXT = (
    "Bu kurulumda gömülü PDF önizlemesi yok; \"PDF'i aç\" ile varsayılan programda açabilirsiniz."
)
PDF_MISSING_TEXT = "CV PDF'i bulunamadı (dosya diskte yok)."
NO_SELECTION_TEXT = "Önizlemek için tablodan bir satır seçin."


class QueuePreviewPanel(QWidget):
    """Seçili başvurunun alıcı/konu/gövde/etiket/PDF önizlemesini gösterir."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._sending_warning_label = QLabel(self)
        self._sending_warning_label.setWordWrap(True)
        self._sending_warning_label.setStyleSheet(SENDING_WARNING_STYLE)

        self._person_warning_label = QLabel(PERSON_ADDRESS_WARNING_TEXT, self)
        self._person_warning_label.setWordWrap(True)
        self._person_warning_label.setStyleSheet(SENDING_WARNING_STYLE)

        self._recipient_label = QLabel(self)
        self._recipient_label.setWordWrap(True)

        self._subject_label = QLabel(self)
        self._subject_label.setWordWrap(True)

        self._body_view = QPlainTextEdit(self)
        self._body_view.setReadOnly(True)

        self._tags_label = QLabel(self)
        self._tags_label.setWordWrap(True)

        self._open_pdf_button = QPushButton("PDF'i aç", self)
        self._open_pdf_button.clicked.connect(self._on_open_pdf)
        self._open_pdf_button.setEnabled(False)

        self._pdf_status_label = QLabel(self)
        self._pdf_status_label.setWordWrap(True)

        self._pdf_document: Optional["QPdfDocument"] = None
        self._pdf_view: Optional["QPdfView"] = None
        if PDF_VIEW_AVAILABLE:
            self._pdf_document = QPdfDocument(self)
            self._pdf_view = QPdfView(self)
            self._pdf_view.setDocument(self._pdf_document)
            self._pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            self._pdf_view.setPageMode(QPdfView.PageMode.MultiPage)

        pdf_row = QHBoxLayout()
        pdf_row.addWidget(QLabel("CV PDF:", self))
        pdf_row.addWidget(self._open_pdf_button)
        pdf_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self._sending_warning_label)
        layout.addWidget(self._person_warning_label)
        layout.addWidget(self._recipient_label)
        layout.addWidget(QLabel("Konu:", self))
        layout.addWidget(self._subject_label)
        layout.addWidget(QLabel("Mail gövdesi:", self))
        layout.addWidget(self._body_view, 1)
        layout.addWidget(QLabel("Eşleşen etiketler:", self))
        layout.addWidget(self._tags_label)
        layout.addLayout(pdf_row)
        layout.addWidget(self._pdf_status_label)
        if self._pdf_view is not None:
            layout.addWidget(self._pdf_view, 2)

        self._current_pdf_path: Optional[Path] = None
        self.clear()

    def clear(self) -> None:
        """Seçim yokken (veya seçim kaldırıldığında) gösterilecek boş durum."""
        self._sending_warning_label.hide()
        self._person_warning_label.hide()
        self._recipient_label.setText(NO_SELECTION_TEXT)
        self._recipient_label.setStyleSheet("")
        self._subject_label.setText("—")
        self._body_view.setPlainText("")
        self._tags_label.setText("—")
        self._open_pdf_button.setEnabled(False)
        self._pdf_status_label.setText("")
        self._current_pdf_path = None
        if self._pdf_document is not None:
            self._pdf_document.close()

    def show_row(self, row: ApplicationRow, *, identity_email: str, test_mode: bool) -> None:
        """Verilen satırın önizlemesini gösterir."""
        if row.status == STATUS_SENDING:
            self._sending_warning_label.setText(SENDING_WARNING_TEXT)
            self._sending_warning_label.show()
        else:
            self._sending_warning_label.hide()
        self._person_warning_label.setVisible(is_person_address(row))

        self._recipient_label.setText(
            format_recipient_line(row, identity_email=identity_email, test_mode=test_mode)
        )
        self._recipient_label.setStyleSheet(RECIPIENT_TEST_STYLE if test_mode else RECIPIENT_REAL_STYLE)

        self._subject_label.setText(row.mail_subject or "—")
        self._body_view.setPlainText(row.mail_body or "")
        self._tags_label.setText(format_tags_line(row))

        self._current_pdf_path = Path(row.cv_pdf_path) if row.cv_pdf_path else None
        self._load_pdf()

    def _load_pdf(self) -> None:
        path = self._current_pdf_path
        if path is None or not path.exists():
            self._open_pdf_button.setEnabled(False)
            self._pdf_status_label.setText(PDF_MISSING_TEXT)
            if self._pdf_document is not None:
                self._pdf_document.close()
            return

        self._open_pdf_button.setEnabled(True)
        if self._pdf_document is None:
            self._pdf_status_label.setText(PDF_UNAVAILABLE_TEXT)
            return

        self._pdf_status_label.setText("")
        self._pdf_document.load(str(path))

    def _on_open_pdf(self) -> None:
        if self._current_pdf_path is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current_pdf_path)))
