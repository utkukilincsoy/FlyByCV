"""src/ui/dashboard_tab.py için küçük bir duman (smoke) testi (qtbot)."""
from PySide6.QtCore import Qt

from src.core.db import get_session
from src.core.models import STATUS_SENT
from src.ui.dashboard_data import EMPTY_STATE_TEXT
from src.ui.dashboard_tab import DashboardTab
from tests.conftest import build_application_row


def _make_tab(qtbot, engine) -> DashboardTab:
    tab = DashboardTab(engine)
    qtbot.addWidget(tab)
    tab.show()  # isVisible() denetimleri için -- widget hiç gösterilmeden hep False döner
    return tab


def test_dashboard_tab_shows_empty_state_message_when_no_data(qtbot, engine):
    tab = _make_tab(qtbot, engine)

    assert tab._empty_label.isVisible()
    assert tab._empty_label.text() == EMPTY_STATE_TEXT
    assert not tab._companies_box.isVisible()


def test_dashboard_tab_shows_stats_after_reload(qtbot, engine, pdf_path):
    with get_session(engine) as session:
        build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path, status=STATUS_SENT)
        session.commit()

    tab = _make_tab(qtbot, engine)

    assert not tab._empty_label.isVisible()
    assert tab._companies_box.isVisible()
    assert "Toplam şirket: 1" in tab._companies_label.text()
    assert "gönderildi: 1" in tab._status_label.text()


def test_dashboard_tab_reload_button_refreshes_stats(qtbot, engine, pdf_path):
    tab = _make_tab(qtbot, engine)
    assert tab._empty_label.isVisible()

    with get_session(engine) as session:
        build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
        session.commit()

    qtbot.mouseClick(tab._refresh_button, Qt.LeftButton)

    assert not tab._empty_label.isVisible()
