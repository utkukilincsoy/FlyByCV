"""Dashboard sekmesi: şirket/başvuru durumu/gelen cevap sayılarının özeti.

Salt okuma bir sekme -- açılışta ve "Yenile"ye basınca kendi kısa ömürlü oturumuyla
(`src/ui/dashboard_data.py::load_dashboard_stats`) veritabanını okur, hiçbir şeye yazmaz. Ağa
çıkmadığı için (kural 8 kapsamı dışı) arka plan işçisi gerekmiyor -- diğer sekmelerin
`JobWorker`'ından farklı olarak.
"""
from typing import Optional

from PySide6.QtWidgets import QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine

from src.core.db import get_session
from src.ui.dashboard_data import (
    EMPTY_STATE_TEXT,
    format_company_lines,
    format_reply_lines,
    format_status_lines,
    load_dashboard_stats,
)


def _section(title: str, parent: QWidget) -> tuple[QGroupBox, QLabel]:
    box = QGroupBox(title, parent)
    label = QLabel(box)
    label.setWordWrap(True)
    layout = QVBoxLayout(box)
    layout.addWidget(label)
    return box, label


class DashboardTab(QWidget):
    """Dashboard sekmesi: üst tarafta "Yenile", altında üç kutu ya da boş durum mesajı."""

    def __init__(self, engine: Engine, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._engine = engine

        self._refresh_button = QPushButton("Yenile", self)
        self._refresh_button.clicked.connect(self.reload)

        self._empty_label = QLabel(EMPTY_STATE_TEXT, self)
        self._empty_label.setWordWrap(True)

        self._companies_box, self._companies_label = _section("Şirketler", self)
        self._status_box, self._status_label = _section("Başvuru durumu", self)
        self._reply_box, self._reply_label = _section("Gelen cevaplar", self)

        layout = QVBoxLayout(self)
        layout.addWidget(self._refresh_button)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._companies_box)
        layout.addWidget(self._status_box)
        layout.addWidget(self._reply_box)
        layout.addStretch(1)

        self.reload()

    def reload(self) -> None:
        """İstatistikleri veritabanından yeniden okur (kısa ömürlü oturum)."""
        with get_session(self._engine) as session:
            stats = load_dashboard_stats(session)

        self._empty_label.setVisible(not stats.has_data)
        for box in (self._companies_box, self._status_box, self._reply_box):
            box.setVisible(stats.has_data)
        if not stats.has_data:
            return

        self._companies_label.setText("\n".join(format_company_lines(stats)))
        self._status_label.setText("\n".join(format_status_lines(stats)))
        self._reply_label.setText("\n".join(format_reply_lines(stats)))
