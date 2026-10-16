"""Cevaplar sekmesi için sağdaki önizleme paneli: gönderen/konu/kategori/gövde + elle düzeltme.

Veritabanına dokunmaz -- tüm metinler `src/ui/replies_data.py`'nin saf fonksiyonlarından gelir
(bkz. `src/ui/queue_preview.py` ile aynı kalıp). Elle düzeltme (kategori combosu + Kaydet butonu)
`category_corrected` sinyaliyle dışarı bildirilir; kaydetmek (`replies_data.set_reply_category`)
çağıranın (`replies_tab.py`) işi -- burada veritabanı oturumu hiç AÇILMAZ.
"""
import logging
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.inbox.categories import REPLY_LABELS_TR
from src.ui.replies_data import (
    ReplyRow,
    format_classification_line,
    format_matched_by_line,
    format_received_line,
)

logger = logging.getLogger(__name__)

NO_SELECTION_TEXT = "Önizlemek için tablodan bir satır seçin."
BLOCKED_TEXT = "⛔ Bu alan adı ENGELLİ: bu şirkete hiçbir yoldan (keşif, CSV, kuyruk, gönderim) e-posta gönderilmez."
NOT_BLOCKED_TEXT = "Bu alan adı engelli değil."


class ReplyPreviewPanel(QWidget):
    """Seçili cevabın gönderen/konu/kategori/gövde önizlemesini gösterir + elle düzeltme."""

    #: (reply_id, seçilen yeni kategori) -- kaydetmek çağıranın işi.
    category_corrected = Signal(int, str)
    #: reply_id -- cevabın şirketini engelle / engeli kaldır (Adım 10.8b); kaydetmek çağıranın işi.
    block_requested = Signal(int)
    unblock_requested = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._company_label = QLabel(self)
        self._company_label.setWordWrap(True)

        self._from_label = QLabel(self)
        self._from_label.setWordWrap(True)

        self._subject_label = QLabel(self)
        self._subject_label.setWordWrap(True)

        self._received_label = QLabel(self)
        self._matched_by_label = QLabel(self)

        self._classification_label = QLabel(self)
        self._classification_label.setWordWrap(True)

        self._body_view = QPlainTextEdit(self)
        self._body_view.setReadOnly(True)

        self._category_combo = QComboBox(self)
        for category, label in REPLY_LABELS_TR.items():
            self._category_combo.addItem(label, category)

        self._save_category_button = QPushButton("Kategoriyi kaydet", self)
        self._save_category_button.clicked.connect(self._on_save_category)

        correction_row = QHBoxLayout()
        correction_row.addWidget(QLabel("Kategori:", self))
        correction_row.addWidget(self._category_combo)
        correction_row.addWidget(self._save_category_button)
        correction_row.addStretch(1)

        self._blocked_label = QLabel(self)
        self._blocked_label.setWordWrap(True)

        self._block_button = QPushButton("Bu alan adını engelle", self)
        self._block_button.clicked.connect(self._on_block)
        self._unblock_button = QPushButton("Engeli kaldır", self)
        self._unblock_button.clicked.connect(self._on_unblock)

        block_row = QHBoxLayout()
        block_row.addWidget(self._block_button)
        block_row.addWidget(self._unblock_button)
        block_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self._company_label)
        layout.addWidget(self._from_label)
        layout.addWidget(QLabel("Konu:", self))
        layout.addWidget(self._subject_label)
        layout.addWidget(self._received_label)
        layout.addWidget(self._matched_by_label)
        layout.addWidget(self._classification_label)
        layout.addLayout(correction_row)
        layout.addWidget(self._blocked_label)
        layout.addLayout(block_row)
        layout.addWidget(QLabel("Mail gövdesi (ilk ~1000 karakter):", self))
        layout.addWidget(self._body_view, 1)

        self._current_reply_id: Optional[int] = None
        self.clear()

    def clear(self) -> None:
        """Seçim yokken (veya seçim kaldırıldığında) gösterilecek boş durum."""
        self._current_reply_id = None
        self._company_label.setText(NO_SELECTION_TEXT)
        self._from_label.setText("")
        self._subject_label.setText("—")
        self._received_label.setText("")
        self._matched_by_label.setText("")
        self._classification_label.setText("")
        self._body_view.setPlainText("")
        self._category_combo.setEnabled(False)
        self._save_category_button.setEnabled(False)
        self._blocked_label.setText("")
        self._block_button.setEnabled(False)
        self._unblock_button.setEnabled(False)

    def show_row(self, row: ReplyRow) -> None:
        """Verilen cevabın önizlemesini gösterir."""
        self._current_reply_id = row.id
        self._company_label.setText(f"Şirket: {row.company_name} ({row.domain})")
        self._from_label.setText(f"Gönderen: {row.from_addr}")
        self._subject_label.setText(row.subject or "—")
        self._received_label.setText(format_received_line(row))
        self._matched_by_label.setText(format_matched_by_line(row))
        self._classification_label.setText(format_classification_line(row))
        self._body_view.setPlainText(row.body_preview or "(önizleme yok)")

        self._category_combo.setEnabled(True)
        self._save_category_button.setEnabled(True)
        index = self._category_combo.findData(row.category)
        self._category_combo.setCurrentIndex(max(index, 0))

        self._blocked_label.setText(BLOCKED_TEXT if row.blocked else NOT_BLOCKED_TEXT)
        self._block_button.setEnabled(not row.blocked)
        self._unblock_button.setEnabled(row.blocked)

    def _on_block(self) -> None:
        if self._current_reply_id is not None:
            self.block_requested.emit(self._current_reply_id)

    def _on_unblock(self) -> None:
        if self._current_reply_id is not None:
            self.unblock_requested.emit(self._current_reply_id)

    def _on_save_category(self) -> None:
        if self._current_reply_id is None:
            return
        category = self._category_combo.currentData()
        self.category_corrected.emit(self._current_reply_id, category)
