"""src/ui/replies_model.py testleri (qtbot)."""
from datetime import datetime

from PySide6.QtCore import Qt

from src.core.models import REPLY_BOUNCE, REPLY_INTERVIEW, REPLY_PENDING
from src.ui.replies_data import COLUMN_TITLES, FILTER_ALL, ReplyRow
from src.ui.replies_model import ReplyCategoryProxy, ReplyTableModel


def _row(**overrides) -> ReplyRow:
    defaults = dict(
        id=1,
        application_id=1,
        company_name="Acme A.Ş.",
        domain="acme.com",
        from_addr="ik@acme.com",
        subject="Re: Başvurunuz",
        received_at=datetime(2026, 3, 5, 10, 0),
        category=REPLY_PENDING,
        classified_by="keyword",
        auto_category=REPLY_PENDING,
        body_preview="Merhaba,",
        matched_by="domain",
    )
    defaults.update(overrides)
    return ReplyRow(**defaults)


def test_model_reports_correct_row_and_column_count(qtbot):
    model = ReplyTableModel()
    model.set_rows([_row(id=1), _row(id=2, company_name="Beta")])

    assert model.rowCount() == 2
    assert model.columnCount() == len(COLUMN_TITLES)
    assert model.headerData(0, Qt.Horizontal) == COLUMN_TITLES[0]


def test_model_shows_company_name_and_category(qtbot):
    model = ReplyTableModel()
    model.set_rows([_row(company_name="Acme A.Ş.", category=REPLY_INTERVIEW)])

    assert model.data(model.index(0, 0), Qt.DisplayRole) == "Acme A.Ş."
    assert model.data(model.index(0, 3), Qt.DisplayRole) == "Mülakat/Olumlu"


def test_set_rows_replaces_previous_content(qtbot):
    model = ReplyTableModel()
    model.set_rows([_row(id=1)])
    assert model.rowCount() == 1

    model.set_rows([_row(id=1), _row(id=2), _row(id=3)])
    assert model.rowCount() == 3


def test_category_proxy_all_filter_shows_everything(qtbot):
    model = ReplyTableModel()
    model.set_rows([_row(id=1, category=REPLY_PENDING), _row(id=2, category=REPLY_BOUNCE)])
    proxy = ReplyCategoryProxy()
    proxy.setSourceModel(model)

    proxy.set_category_filter(FILTER_ALL)

    assert proxy.rowCount() == 2


def test_category_proxy_specific_category_filter(qtbot):
    model = ReplyTableModel()
    model.set_rows([_row(id=1, category=REPLY_PENDING), _row(id=2, category=REPLY_BOUNCE)])
    proxy = ReplyCategoryProxy()
    proxy.setSourceModel(model)

    proxy.set_category_filter(REPLY_BOUNCE)

    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 0)) == "Acme A.Ş."
