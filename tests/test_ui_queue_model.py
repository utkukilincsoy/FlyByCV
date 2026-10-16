"""src/ui/queue_model.py testleri (qtbot)."""
from PySide6.QtCore import Qt

from src.core.models import STATUS_APPROVED, STATUS_QUEUED, STATUS_SENT
from src.ui.queue_data import COLUMN_TITLES, FILTER_ACTIVE, FILTER_ALL, ApplicationRow
from src.ui.queue_model import QueueStatusProxy, QueueTableModel


def _row(**overrides) -> ApplicationRow:
    defaults = dict(
        id=1,
        company_id=1,
        company_name="Acme A.Ş.",
        domain="acme.com",
        email="kariyer@acme.com",
        email_is_role=True,
        mx_valid=True,
        status=STATUS_QUEUED,
        tailored=False,
        matched_tags=(),
        cv_pdf_path=None,
        mail_subject="Staj Başvurusu",
        mail_body="Sayın İnsan Kaynakları Ekibi,",
        created_at=None,
        sent_at=None,
        error=None,
    )
    defaults.update(overrides)
    return ApplicationRow(**defaults)


def test_model_reports_correct_row_and_column_count(qtbot):
    model = QueueTableModel()
    model.set_rows([_row(id=1), _row(id=2, company_name="Beta")])

    assert model.rowCount() == 2
    assert model.columnCount() == len(COLUMN_TITLES)
    assert model.headerData(0, Qt.Horizontal) == COLUMN_TITLES[0]


def test_model_shows_company_name_and_status(qtbot):
    model = QueueTableModel()
    model.set_rows([_row(company_name="Acme A.Ş.", status=STATUS_APPROVED)])

    assert model.data(model.index(0, 0), Qt.DisplayRole) == "Acme A.Ş."
    assert model.data(model.index(0, 4), Qt.DisplayRole) == "onaylandı"


def test_set_rows_replaces_previous_content(qtbot):
    model = QueueTableModel()
    model.set_rows([_row(id=1)])
    assert model.rowCount() == 1

    model.set_rows([_row(id=1), _row(id=2), _row(id=3)])
    assert model.rowCount() == 3


def test_status_proxy_active_filter_excludes_sent(qtbot):
    model = QueueTableModel()
    model.set_rows(
        [
            _row(id=1, status=STATUS_QUEUED),
            _row(id=2, status=STATUS_APPROVED),
            _row(id=3, status=STATUS_SENT),
        ]
    )
    proxy = QueueStatusProxy()
    proxy.setSourceModel(model)
    proxy.set_status_filter(FILTER_ACTIVE)

    assert proxy.rowCount() == 2


def test_status_proxy_all_filter_shows_everything(qtbot):
    model = QueueTableModel()
    model.set_rows([_row(id=1, status=STATUS_QUEUED), _row(id=2, status=STATUS_SENT)])
    proxy = QueueStatusProxy()
    proxy.setSourceModel(model)
    proxy.set_status_filter(FILTER_ACTIVE)
    assert proxy.rowCount() == 1

    proxy.set_status_filter(FILTER_ALL)
    assert proxy.rowCount() == 2


def test_status_proxy_specific_status_filter(qtbot):
    model = QueueTableModel()
    model.set_rows([_row(id=1, status=STATUS_QUEUED), _row(id=2, status=STATUS_SENT)])
    proxy = QueueStatusProxy()
    proxy.setSourceModel(model)

    proxy.set_status_filter(STATUS_SENT)

    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 0)) == "Acme A.Ş."


def test_person_address_row_is_highlighted_but_role_and_unknown_are_not(qtbot):
    model = QueueTableModel()
    model.set_rows([
        _row(id=1, email="ahmet.yilmaz@acme.com", email_is_role=False),
        _row(id=2, email_is_role=True),
        _row(id=3, email_is_role=None),
    ])

    assert model.data(model.index(0, 0), Qt.BackgroundRole) is not None
    assert model.data(model.index(0, 4), Qt.ForegroundRole) is not None
    assert model.data(model.index(1, 0), Qt.BackgroundRole) is None
    assert model.data(model.index(2, 0), Qt.BackgroundRole) is None


def test_preview_panel_shows_person_warning_only_for_person_address(qtbot):
    from src.ui.queue_preview import QueuePreviewPanel

    panel = QueuePreviewPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.show_row(_row(email_is_role=False), identity_email="me@x.com", test_mode=True)
    assert panel._person_warning_label.isVisible()
    panel.show_row(_row(email_is_role=True), identity_email="me@x.com", test_mode=True)
    assert not panel._person_warning_label.isVisible()
