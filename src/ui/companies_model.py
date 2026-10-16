"""Şirketler tablosu için Qt tablo modeli ve arama süzgeci."""
from typing import Optional, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QSortFilterProxyModel, Qt

from src.ui.company_data import COLUMN_TITLES, CompanyRow, cell_text, matches_search, role_tooltip


class CompanyTableModel(QAbstractTableModel):
    """Şirket listesini gösteren salt okunur tablo modeli.

    ORM nesnesi değil, `CompanyRow` kopyaları tutar (bkz. `src/ui/company_data.py`).
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._rows: list[CompanyRow] = []

    def set_rows(self, rows: Sequence[CompanyRow]) -> None:
        """Tablo içeriğini tamamen değiştirir."""
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, source_row: int) -> CompanyRow:
        """Verilen KAYNAK (proxy değil) satır indeksindeki `CompanyRow`'u döndürür."""
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
            return role_tooltip(row) or None
        return None


class CompanySearchProxy(QSortFilterProxyModel):
    """Ad/alan adı/e-posta içinde Türkçe'ye duyarsız arama yapan süzgeç.

    Yeniden sorgu yerine bir proxy seçildi: her tuş vuruşunda oturum açmaz, seçimi
    korur ve SQLite `LIKE`'ın yapamadığı Türkçe büyük/küçük harf katlamasını
    (`fold_turkish`) uygular.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._query = ""

    def set_search_text(self, text: str) -> None:
        self._query = text
        # `invalidateFilter()`/`invalidateRowsFilter()` bu Qt sürümünde kullanımdan
        # kaldırıldı (deprecated); yerine önerilen genel `invalidate()` kullanılıyor.
        self.invalidate()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None:
            return True
        return matches_search(model.row_at(source_row), self._query)
