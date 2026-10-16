"""Onay Kuyruğu tablosu için Qt tablo modeli ve durum süzgeci.

`src/ui/companies_model.py`'nin ikizi: ORM nesnesi değil, `ApplicationRow` kopyaları tutar.
"""
from typing import Optional, Sequence

from PySide6.QtGui import QBrush, QColor
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSortFilterProxyModel, Qt

from src.ui.queue_data import (
    COLUMN_TITLES,
    FILTER_ACTIVE,
    ApplicationRow,
    cell_text,
    is_person_address,
    matches_status,
    row_tooltip,
)


#: Kişi adresi satırı: açık turuncu zemin + koyu yazı (tema rengine bağımlı okunurluk sorunu olmasın).
PERSON_ROW_BACKGROUND = QBrush(QColor("#F9C846"))
PERSON_ROW_FOREGROUND = QBrush(QColor("#000000"))


class QueueTableModel(QAbstractTableModel):
    """Kuyruk listesini gösteren salt okunur tablo modeli.

    ORM nesnesi değil, `ApplicationRow` kopyaları tutar (bkz. `src/ui/queue_data.py`).
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._rows: list[ApplicationRow] = []

    def set_rows(self, rows: Sequence[ApplicationRow]) -> None:
        """Tablo içeriğini tamamen değiştirir."""
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, source_row: int) -> ApplicationRow:
        """Verilen KAYNAK (proxy değil) satır indeksindeki `ApplicationRow`'u döndürür."""
        return self._rows[source_row]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008 - Qt imzası
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(COLUMN_TITLES)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole
    ):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMN_TITLES[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == Qt.DisplayRole:
            return cell_text(row, index.column())
        if role == Qt.ToolTipRole:
            return row_tooltip(row) or None
        if role == Qt.BackgroundRole and is_person_address(row):
            return PERSON_ROW_BACKGROUND
        if role == Qt.ForegroundRole and is_person_address(row):
            return PERSON_ROW_FOREGROUND
        return None


class QueueStatusProxy(QSortFilterProxyModel):
    """Durum filtresine göre satırları süzen proxy (`companies_model.CompanySearchProxy`'nin ikizi).

    `invalidateFilter()`/`invalidateRowsFilter()` bu Qt sürümünde kullanımdan kaldırıldı
    (deprecated); yerine önerilen genel `invalidate()` kullanılıyor.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._status_filter = FILTER_ACTIVE

    def set_status_filter(self, choice: str) -> None:
        self._status_filter = choice
        self.invalidate()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None:
            return True
        return matches_status(model.row_at(source_row), self._status_filter)
