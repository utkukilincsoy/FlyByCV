from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlmodel import select

from src.company_finder.csv_import import (
    REASON_BAD_DOMAIN,
    REASON_FREE_MAIL_NEEDS_WEBSITE,
    REASON_NO_CONTACT,
    REASON_NO_NAME,
    WARN_BAD_EMAIL,
    add_company,
    import_companies,
)
from src.company_finder.free_mail import is_free_mail_domain
from src.core.models import Company
from tests.conftest import build_company

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CSV = PROJECT_ROOT / "data" / "companies.example.csv"


def _write_csv(tmp_path: Path, text: str, encoding: str = "utf-8", name: str = "list.csv") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def _write_xlsx(tmp_path: Path, header: list[str], rows: list[list], name: str = "list.xlsx") -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    path = tmp_path / name
    workbook.save(path)
    return path


# --- okuma / kodlama / sütunlar --------------------------------------------------------------


def test_import_csv_comma_delimiter(session, tmp_path):
    csv_text = (
        "name,website,email,city,tags,about\n"
        "Örnek Yazılım,https://ornek.example.com,kariyer@ornek.example.com,İstanbul,backend,Açıklama\n"
        "Deneme Bulut,https://deneme.example.com,,Ankara,bulut-devops,Başka açıklama\n"
    )
    path = _write_csv(tmp_path, csv_text)

    report = import_companies(session, path)

    assert report.added_count == 2
    assert report.total_rows == 2
    session.commit()
    companies = session.exec(select(Company)).all()
    assert {c.domain for c in companies} == {"ornek.example.com", "deneme.example.com"}
    first = next(c for c in companies if c.domain == "ornek.example.com")
    assert first.source == "csv"
    assert first.city == "İstanbul"
    assert first.sector_tags == ["backend"]


def test_import_csv_semicolon_delimiter_with_comma_in_tags(session, tmp_path):
    csv_text = (
        "name;website;email;city;tags;about\n"
        "Acme;https://acme.example.com;;İstanbul;backend,web-gelistirme;Açıklama\n"
    )
    path = _write_csv(tmp_path, csv_text)

    report = import_companies(session, path)

    assert report.added_count == 1
    company = report.added[0]
    assert company.domain == "acme.example.com"
    session.commit()
    stored = session.exec(select(Company)).one()
    assert stored.sector_tags == ["backend", "web-gelistirme"]


def test_import_csv_cp1254(session, tmp_path):
    csv_text = "name;website;city\nŞişli Yazılım;https://sisli.example.com;Şişli\n"
    path = _write_csv(tmp_path, csv_text, encoding="cp1254")

    report = import_companies(session, path)

    assert report.added_count == 1
    session.commit()
    stored = session.exec(select(Company)).one()
    assert stored.name == "Şişli Yazılım"
    assert stored.city == "Şişli"


def test_import_csv_utf8_bom_header(session, tmp_path):
    csv_text = "name,website\nAcme,https://acme.example.com\n"
    path = _write_csv(tmp_path, csv_text, encoding="utf-8-sig")

    report = import_companies(session, path)

    assert report.added_count == 1


def test_import_csv_utf16_raises(tmp_path, session):
    path = tmp_path / "utf16.csv"
    path.write_bytes("name,website\nAcme,https://acme.example.com\n".encode("utf-16"))

    with pytest.raises(ValueError, match="UTF-16"):
        import_companies(session, path)


def test_import_flexible_column_names(session, tmp_path):
    csv_text = (
        "ŞİRKET ADI;Web;E-Posta;Şehir;Hakkında;Etiketler\n"
        "Örnek A.Ş.;acme.example.com;info@acme.example.com;İzmir;Bir açıklama;otomasyon\n"
    )
    path = _write_csv(tmp_path, csv_text)

    report = import_companies(session, path)

    assert report.added_count == 1
    session.commit()
    stored = session.exec(select(Company)).one()
    assert stored.name == "Örnek A.Ş."
    assert stored.domain == "acme.example.com"
    assert stored.sector_tags == ["otomasyon"]


def test_import_xlsx(session, tmp_path):
    header = ["name", "website", "city"]
    rows = [
        ["Acme", "https://acme.example.com", 34],  # sayısal şehir hücresi
        ["Deneme"],  # kısa satır (website yok, email yok -> atlanır)
        [],  # tamamen boş satır
        [],  # sondaki boş satırlar
    ]
    path = _write_xlsx(tmp_path, header, rows)

    report = import_companies(session, path)

    assert report.added_count == 1
    assert report.skipped[0].reason == REASON_NO_CONTACT
    added = report.added[0]
    assert added.domain == "acme.example.com"


def test_import_missing_name_column_raises(session, tmp_path):
    path = _write_csv(tmp_path, "website,email\nhttps://acme.example.com,\n")

    with pytest.raises(ValueError, match="şirket adı"):
        import_companies(session, path)


def test_import_unsupported_extension_raises(session, tmp_path):
    path = _write_csv(tmp_path, "name,website\n", name="list.txt")

    with pytest.raises(ValueError, match="Desteklenmeyen"):
        import_companies(session, path)


def test_import_header_only_file_returns_empty_report(session, tmp_path):
    path = _write_csv(tmp_path, "name,website\n")

    report = import_companies(session, path)

    assert report.added == []
    assert report.skipped == []
    assert report.total_rows == 0


# --- alan kuralları ----------------------------------------------------------------------------


def test_tags_normalized_folded_and_capped(session, tmp_path):
    csv_text = (
        "name,website,tags\n"
        "Acme,https://acme.example.com,"
        '"Yapay Zeka, Veritabanı, uçan-halı, backend, frontend, mobil"\n'
    )
    path = _write_csv(tmp_path, csv_text)

    report = import_companies(session, path)

    assert report.added_count == 1
    session.commit()
    stored = session.exec(select(Company)).one()
    assert "yapay-zeka" in stored.sector_tags
    assert "veritabani" in stored.sector_tags
    assert "ucan-hali" not in stored.sector_tags  # sözlükte yok
    assert len(stored.sector_tags) <= 3


def test_website_query_and_scheme_stripped_from_domain(session, tmp_path):
    path = _write_csv(tmp_path, "name,website\nAcme,www.acme.example.com/iletisim?utm=1\n")

    report = import_companies(session, path)

    assert report.added[0].domain == "acme.example.com"
    session.commit()
    stored = session.exec(select(Company)).one()
    assert stored.website == "https://www.acme.example.com/iletisim?utm=1"


def test_email_mailto_and_uppercase_i_cleaned(session, tmp_path):
    path = _write_csv(tmp_path, "name,email\nAcme,mailto:İK@Acme.example.COM\n")

    report = import_companies(session, path)

    assert report.added_count == 1
    session.commit()
    stored = session.exec(select(Company)).one()
    assert stored.email == "ik@acme.example.com"
    assert stored.domain == "acme.example.com"


def test_malformed_email_without_website_skipped(session, tmp_path):
    path = _write_csv(tmp_path, "name,email\nAcme,ali@\n")

    report = import_companies(session, path)

    assert report.added == []
    assert report.skipped[0].reason == REASON_NO_CONTACT


@pytest.mark.parametrize("junk_website", ["yok", "-", "Acme Ltd"])
def test_junk_website_skipped_not_stored(session, tmp_path, junk_website):
    path = _write_csv(tmp_path, f"name,website\nAcme,{junk_website}\n")

    report = import_companies(session, path)

    assert report.added == []
    assert report.skipped[0].reason == REASON_BAD_DOMAIN
    session.commit()
    assert session.exec(select(Company)).all() == []


def test_missing_name_row_skipped_others_added(session, tmp_path):
    csv_text = (
        "name,website\n"
        ",https://noname.example.com\n"
        "Acme,https://acme.example.com\n"
    )
    path = _write_csv(tmp_path, csv_text)

    report = import_companies(session, path)

    assert report.added_count == 1
    assert report.skipped[0].reason == REASON_NO_NAME


# --- ücretsiz mail -------------------------------------------------------------------------------


def test_free_mail_without_website_skipped(session, tmp_path):
    path = _write_csv(tmp_path, "name,email\nAcme,kariyer@gmail.com\n")

    report = import_companies(session, path)

    assert report.added == []
    assert report.skipped[0].reason == REASON_FREE_MAIL_NEEDS_WEBSITE


def test_free_mail_with_website_uses_website_domain(session, tmp_path):
    path = _write_csv(
        tmp_path, "name,website,email\nAcme,https://acme.example.com,kariyer@gmail.com\n"
    )

    report = import_companies(session, path)

    assert report.added_count == 1
    session.commit()
    stored = session.exec(select(Company)).one()
    assert stored.domain == "acme.example.com"
    assert stored.email == "kariyer@gmail.com"


@pytest.mark.parametrize(
    "domain, expected",
    [("gmail.com", True), ("GMAIL.COM", True), ("yahoo.com.tr", True), ("acme.com", False)],
)
def test_is_free_mail_domain(domain, expected):
    assert is_free_mail_domain(domain) is expected


# --- tekrar / dayanıklılık -----------------------------------------------------------------------


def test_duplicate_domain_in_db_skipped(session, tmp_path):
    session.add(build_company(name="Var Olan", domain="acme.example.com"))
    session.commit()

    path = _write_csv(tmp_path, "name,website\nAcme,https://acme.example.com\n")
    report = import_companies(session, path)

    assert report.added_count == 0
    assert "zaten kayıtlı" in report.skipped[0].reason


def test_duplicate_domain_within_file_skipped(session, tmp_path):
    csv_text = (
        "name,website\n"
        "Acme,https://www.acme.example.com\n"
        "Acme İkinci,acme.example.com/iletisim\n"
    )
    path = _write_csv(tmp_path, csv_text)

    report = import_companies(session, path)

    assert report.added_count == 1
    assert "dosyada tekrar" in report.skipped[0].reason


def test_bad_row_does_not_abort_import_and_commit_succeeds(session, tmp_path):
    csv_text = (
        "name,website\n"
        "İyi Şirket,https://iyi.example.com\n"
        ",https://bos-ad.example.com\n"
        "İyi Şirket İki,https://iyi-iki.example.com\n"
    )
    path = _write_csv(tmp_path, csv_text)

    report = import_companies(session, path)

    assert report.added_count == 2
    session.commit()  # patlamamalı
    assert len(session.exec(select(Company)).all()) == 2


def test_two_unresolvable_domain_rows_do_not_break_commit(session, tmp_path):
    csv_text = "name,website\nBirinci,yok\nİkinci,yok\n"
    path = _write_csv(tmp_path, csv_text)

    report = import_companies(session, path)

    assert report.added == []
    assert len(report.skipped) == 2
    session.commit()  # iki boş/aynı alan adı olsa da patlamamalı


def test_row_numbers_match_file_lines(session, tmp_path):
    csv_text = "name,website\n\nAcme,https://acme.example.com\n"
    path = _write_csv(tmp_path, csv_text)

    report = import_companies(session, path)

    assert report.added[0].row == 3  # başlık=1, boş satır=2 (tüketilir), veri=3


def test_import_does_not_commit(session, tmp_path):
    path = _write_csv(tmp_path, "name,website\nAcme,https://acme.example.com\n")

    import_companies(session, path)

    assert len(session.new) == 1
    session.rollback()
    assert session.exec(select(Company)).all() == []


# --- dry-run / elle ekleme / örnek dosya ----------------------------------------------------------


def test_dry_run_adds_nothing(session, tmp_path):
    path = _write_csv(tmp_path, "name,website\nAcme,https://acme.example.com\n")

    report = import_companies(session, path, dry_run=True)

    assert report.added_count == 1
    assert len(session.new) == 0
    session.commit()
    assert session.exec(select(Company)).all() == []


def test_add_company_manual_happy_path(session):
    company = add_company(session, name="Acme", website="acme.example.com")

    assert company.source == "manual"
    assert company.email_source is None
    assert company.domain == "acme.example.com"
    assert company.id is not None  # flush çağrıldı


def test_add_company_rejects_duplicate_domain(session):
    session.add(build_company(name="Var Olan", domain="acme.example.com"))
    session.commit()

    with pytest.raises(ValueError, match="zaten kayıtlı"):
        add_company(session, name="Acme", website="acme.example.com")


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"name": ""}, "ad boş"),
        ({"name": "Acme"}, "site veya e-posta gerekli"),
        ({"name": "Acme", "email": "kariyer@gmail.com"}, "web sitesi gerekli"),
    ],
)
def test_add_company_rejects_invalid_input(session, kwargs, match):
    with pytest.raises(ValueError, match=match):
        add_company(session, **kwargs)


def test_csv_email_gets_role_flag_too(session, tmp_path):
    """CSV'den gelen e-posta da email_is_role doldurmalı (CLAUDE.md 10. kural CSV'de de geçerli)."""
    csv_text = (
        "name,website,email\n"
        "Rol Olan,https://rol.example.com,kariyer@rol.example.com\n"
        "Kisi Olan,https://kisi.example.com,ahmet.yilmaz@kisi.example.com\n"
    )
    path = _write_csv(tmp_path, csv_text)

    report = import_companies(session, path)

    assert report.added_count == 2
    session.commit()
    by_domain = {c.domain: c for c in session.exec(select(Company)).all()}
    assert by_domain["rol.example.com"].email_is_role is True
    assert by_domain["kisi.example.com"].email_is_role is False


def test_example_csv_imports_cleanly(session):
    report = import_companies(session, EXAMPLE_CSV)

    assert report.skipped == []
    assert report.added_count > 0
