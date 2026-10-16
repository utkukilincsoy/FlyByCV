"""src/ui/dialogs.py::CompanyDetailDialog: siteden/CSV'den gelen değerler HTML olarak işlenmemeli."""
from src.core.db import get_session
from src.core.models import Company
from src.ui.dialogs import CompanyDetailDialog


def test_company_values_are_escaped_in_rich_text_label(qtbot, engine):
    with get_session(engine) as session:
        company = Company(
            name='<b>Kalın</b><img src="https://ornek.com/x.png">',
            domain="ornek.com",
            city="<i>Ankara</i>",
            email="info@ornek.com",
            source="test",
        )
        session.add(company)
        session.commit()
        company_id = company.id

    dialog = CompanyDetailDialog(engine, company_id)
    qtbot.addWidget(dialog)
    label_text = dialog._info_label.text()

    assert "&lt;b&gt;Kalın&lt;/b&gt;" in label_text
    assert "&lt;img" in label_text
    assert "<img" not in label_text
    assert "&lt;i&gt;Ankara&lt;/i&gt;" in label_text
