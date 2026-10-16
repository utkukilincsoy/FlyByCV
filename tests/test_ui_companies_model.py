"""src/ui/companies_model.py testleri (qtbot)."""
from PySide6.QtCore import Qt

from src.ui.companies_model import CompanySearchProxy, CompanyTableModel
from src.ui.company_data import COLUMN_TITLES, CompanyRow


def _row(**overrides) -> CompanyRow:
    defaults = dict(
        id=1,
        name="Acme A.Ş.",
        domain="acme.com",
        website=None,
        city=None,
        email="kariyer@acme.com",
        email_source=None,
        email_is_role=False,
        mx_valid=None,
        sector_tags=(),
        source="manual",
        about_text=None,
        found_emails=(),
        application_status=None,
        created_at=None,
    )
    defaults.update(overrides)
    return CompanyRow(**defaults)


def test_model_reports_correct_row_and_column_count(qtbot):
    model = CompanyTableModel()
    model.set_rows([_row(id=1), _row(id=2, name="Beta")])

    assert model.rowCount() == 2
    assert model.columnCount() == len(COLUMN_TITLES)
    assert model.headerData(0, Qt.Horizontal) == COLUMN_TITLES[0]


def test_model_shows_email_and_role_symbol(qtbot):
    model = CompanyTableModel()
    model.set_rows([_row(email="kariyer@acme.com", email_is_role=False)])

    assert model.data(model.index(0, 2), Qt.DisplayRole) == "kariyer@acme.com"
    assert model.data(model.index(0, 3), Qt.DisplayRole) == "⚠"


def test_set_rows_replaces_previous_content(qtbot):
    model = CompanyTableModel()
    model.set_rows([_row(id=1)])
    assert model.rowCount() == 1

    model.set_rows([_row(id=1), _row(id=2), _row(id=3)])
    assert model.rowCount() == 3


def test_search_proxy_filters_by_name_case_and_turkish_insensitive(qtbot):
    model = CompanyTableModel()
    model.set_rows(
        [
            _row(id=1, name="Acme A.Ş.", domain="acme.com", email="a@acme.com"),
            _row(id=2, name="Beta Ltd.", domain="beta.com", email="b@beta.com"),
            _row(id=3, name="Gamma", domain="gamma.com", email="c@gamma.com"),
        ]
    )
    proxy = CompanySearchProxy()
    proxy.setSourceModel(model)

    proxy.set_search_text("ACME")

    assert proxy.rowCount() == 1
    assert proxy.data(proxy.index(0, 0)) == "Acme A.Ş."

    proxy.set_search_text("")
    assert proxy.rowCount() == 3


def test_search_proxy_matches_domain_and_email(qtbot):
    model = CompanyTableModel()
    model.set_rows(
        [
            _row(id=1, name="Acme", domain="acme.com", email="kariyer@acme.com"),
            _row(id=2, name="Beta", domain="beta.com", email="info@beta.com"),
        ]
    )
    proxy = CompanySearchProxy()
    proxy.setSourceModel(model)

    proxy.set_search_text("beta.com")
    assert proxy.rowCount() == 1

    proxy.set_search_text("kariyer@acme.com")
    assert proxy.rowCount() == 1
