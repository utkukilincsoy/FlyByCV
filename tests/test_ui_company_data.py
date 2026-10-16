"""src/ui/company_data.py testleri (Qt YOK -- saf Python, widget gerektirmez)."""
import dataclasses

import pytest

from src.core.db import get_session
from src.core.models import STATUS_QUEUED
from src.ui.company_data import (
    NO_APPLICATION_LABEL,
    ROLE_WARNING_TOOLTIP,
    STATUS_LABELS_TR,
    CompanyRow,
    cell_text,
    ids_missing_email,
    ids_ready_for_application,
    load_company_rows,
    matches_search,
    role_tooltip,
    use_email,
)
from tests.conftest import build_application_row, build_company


# ---------------------------------------------------------------------------
# load_company_rows / to_company_row
# ---------------------------------------------------------------------------


def test_load_company_rows_without_application_has_no_status(session):
    session.add(build_company(domain="beta.com", name="Beta A.Ş."))
    session.commit()

    rows = load_company_rows(session)

    assert len(rows) == 1
    assert rows[0].application_status is None


def test_load_company_rows_with_application_shows_status(session, pdf_path):
    build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path, status=STATUS_QUEUED)
    session.commit()

    rows = load_company_rows(session)

    assert len(rows) == 1
    assert rows[0].application_status == STATUS_QUEUED


def test_company_row_is_a_plain_copy_after_session_closes(engine):
    """ORM sızıntısı regresyon testi: oturum kapansa da kopya düz Python değerleri olmalı."""
    with get_session(engine) as session:
        session.add(build_company(domain="gamma.com", name="Gamma"))
        session.commit()
        rows = load_company_rows(session)

    row = rows[0]
    as_dict = dataclasses.asdict(row)  # ORM nesnesiyle patlardı (oturuma bağlı, expired olur)
    assert as_dict["domain"] == "gamma.com"
    assert as_dict["name"] == "Gamma"


# ---------------------------------------------------------------------------
# cell_text / role_tooltip
# ---------------------------------------------------------------------------


def _row(**overrides) -> CompanyRow:
    defaults = dict(
        id=1,
        name="Şirket A.Ş.",
        domain="test.com",
        website=None,
        city=None,
        email=None,
        email_source=None,
        email_is_role=None,
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


def test_cell_text_email_missing_shows_dash():
    assert cell_text(_row(email=None), 2) == "—"
    assert cell_text(_row(email="kariyer@test.com"), 2) == "kariyer@test.com"


def test_cell_text_role_symbols():
    assert cell_text(_row(email_is_role=True), 3) == "✓"
    assert cell_text(_row(email_is_role=False), 3) == "⚠"
    assert cell_text(_row(email_is_role=None), 3) == "—"


def test_cell_text_mx_symbols():
    assert cell_text(_row(mx_valid=True), 4) == "✓"
    assert cell_text(_row(mx_valid=False), 4) == "✗"
    assert cell_text(_row(mx_valid=None), 4) == "?"


def test_cell_text_tags_joined_with_comma():
    assert cell_text(_row(sector_tags=("backend", "yapay-zeka")), 5) == "backend, yapay-zeka"


def test_cell_text_application_status():
    assert cell_text(_row(application_status=None), 7) == NO_APPLICATION_LABEL
    assert cell_text(_row(application_status=STATUS_QUEUED), 7) == STATUS_LABELS_TR[STATUS_QUEUED]


def test_role_tooltip_warns_only_for_non_role_address():
    assert role_tooltip(_row(email_is_role=False)) == ROLE_WARNING_TOOLTIP
    assert role_tooltip(_row(email_is_role=True)) == ""
    assert role_tooltip(_row(email_is_role=None)) == ""


# ---------------------------------------------------------------------------
# matches_search
# ---------------------------------------------------------------------------


def test_matches_search_is_case_and_turkish_insensitive():
    row = _row(name="Şirket A.Ş.", domain="test.com", email="info@test.com")

    assert matches_search(row, "sirket")
    assert matches_search(row, "SIRKET")
    assert matches_search(row, "Şirket")
    assert not matches_search(row, "xyz")
    assert matches_search(row, "")  # boş sorgu her satırla eşleşir


def test_matches_search_hits_domain_and_email_only():
    row = _row(name="Acme", domain="acme.com", email="kariyer@acme.com")

    assert matches_search(row, "acme.com")
    assert matches_search(row, "kariyer@acme.com")


# ---------------------------------------------------------------------------
# ids_missing_email / ids_ready_for_application
# ---------------------------------------------------------------------------


def test_ids_missing_email_finds_companies_without_email_or_about_text(session):
    complete = build_company(domain="complete.com", email="a@complete.com", about_text="dolu")
    missing = build_company(domain="missing.com", email=None, about_text="dolu")
    session.add(complete)
    session.add(missing)
    session.commit()

    ids = ids_missing_email(session)

    assert missing.id in ids
    assert complete.id not in ids


def test_ids_ready_for_application_excludes_companies_with_application(session, pdf_path):
    ready = build_company(domain="ready.com", email="a@ready.com")
    session.add(ready)
    session.commit()

    _, already_applied = build_application_row(session, domain="hazir.com", cv_pdf_path=pdf_path)
    session.commit()

    ids = ids_ready_for_application(session)

    assert ready.id in ids
    assert already_applied.id not in ids


def test_ids_ready_for_application_excludes_companies_without_email(session):
    no_email = build_company(domain="noemail.com", email=None)
    session.add(no_email)
    session.commit()

    assert no_email.id not in ids_ready_for_application(session)


# ---------------------------------------------------------------------------
# use_email
# ---------------------------------------------------------------------------


def test_use_email_selects_role_address(session):
    company = build_company(
        domain="acme.com",
        found_emails=[
            {"email": "kariyer@acme.com", "source_url": "https://acme.com/iletisim", "is_role": True},
            {"email": "ahmet.yilmaz@acme.com", "source_url": "https://acme.com/hakkinda", "is_role": False},
        ],
    )
    session.add(company)
    session.flush()

    use_email(session, company, "kariyer@acme.com")

    assert company.email == "kariyer@acme.com"
    assert company.email_is_role is True
    assert company.email_source == "https://acme.com/iletisim"


def test_use_email_selects_personal_address(session):
    company = build_company(
        domain="acme.com",
        found_emails=[
            {"email": "ahmet.yilmaz@acme.com", "source_url": "https://acme.com/hakkinda", "is_role": False},
        ],
    )
    session.add(company)
    session.flush()

    use_email(session, company, "ahmet.yilmaz@acme.com")

    assert company.email == "ahmet.yilmaz@acme.com"
    assert company.email_is_role is False
    assert company.email_source == "https://acme.com/hakkinda"


def test_use_email_unknown_address_raises_value_error(session):
    company = build_company(domain="acme.com", found_emails=[])
    session.add(company)
    session.flush()

    with pytest.raises(ValueError):
        use_email(session, company, "yok@acme.com")


def test_use_email_does_not_commit(session, monkeypatch):
    company = build_company(
        domain="acme.com",
        found_emails=[{"email": "kariyer@acme.com", "source_url": "https://acme.com", "is_role": True}],
    )
    session.add(company)
    session.flush()

    commit_calls: list[int] = []
    monkeypatch.setattr(session, "commit", lambda: commit_calls.append(1))

    use_email(session, company, "kariyer@acme.com")

    assert commit_calls == []
