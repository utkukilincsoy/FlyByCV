"""Cevaplar tablosu için Qt tablo modeli ve kategori süzgeci.

`src/ui/queue_model.py`nin ikizi: ORM nesnesi değil, `ReplyRow` kopyaları tutar.
"""
from typing import Optional, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSortFilterProxyModel, Qt

from src.ui.replies_data import COLUMN_TITLES, FILTER_ALL, ReplyRow, cell_text, matches_category, row_tooltip


class ReplyTableModel(QAbstractTableModel):
    """Cevap listesini gösteren salt okunur tablo modeli.

    ORM nesnesi değil, `ReplyRow` kopyaları tutar (bkz. `src/ui/replies_data.py`).
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._rows: list[ReplyRow] = []

    def set_rows(self, rows: Sequence[ReplyRow]) -> None:
        """Tablo içeriğini tamamen değiştirir."""
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, source_row: int) -> ReplyRow:
        """Verilen KAYNAK (proxy değil) satır indeksindeki `ReplyRow`'u döndürür."""
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
        return None


class ReplyCategoryProxy(QSortFilterProxyModel):
    """Kategori filtresine göre satırları süzen proxy (`queue_model.QueueStatusProxy`'nin ikizi).

    `invalidateFilter()`/`invalidateRowsFilter()` bu Qt sürümünde kullanımdan kaldırıldı
    (deprecated); yerine önerilen genel `invalidate()` kullanılıyor.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._category_filter = FILTER_ALL

    def set_category_filter(self, choice: str) -> None:
        self._category_filter = choice
        self.invalidate()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None:
            return True
        return matches_category(model.row_at(source_row), self._category_filter)
