"""src/company_finder/naming.py: sondaki nokta yalnızca kısaltmalarda korunur (PLAN.md 10.6h)."""
import pytest

from src.company_finder.naming import derive_company_name


def _title(text: str) -> str:
    return f"<html><head><title>{text}</title></head><body></body></html>"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Xyz Yazılım Ltd. Şti.", "Xyz Yazılım Ltd. Şti."),
        ("Anasayfa | Xyz Yazılım Ltd. Şti.", "Xyz Yazılım Ltd. Şti."),
        ("Xyz Yazılım Ltd.", "Xyz Yazılım Ltd."),
        ("Xyz Yazılım A.Ş.", "Xyz Yazılım A.Ş."),
        ("Xyz San. ve Tic. Ltd. Şti. - Hoş Geldiniz", "Xyz San. ve Tic. Ltd. Şti."),
        ("Xyz Teknoloji Inc.", "Xyz Teknoloji Inc."),
        ("Xyz Ltd. Şti.!", "Xyz Ltd. Şti."),
    ],
)
def test_trailing_period_kept_after_abbreviation(title, expected):
    assert derive_company_name(_title(title), "xyz.com") == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Xyz Mimarlık.", "Xyz Mimarlık"),
        ("Xyz Mimarlık...", "Xyz Mimarlık"),
        ("Xyz Mimarlık - ", "Xyz Mimarlık"),
        ("Alfa Ltd | Bir Slogan", "Alfa Ltd"),  # kısaltma ama sonunda nokta yok: aynen kalır
    ],
)
def test_trailing_period_still_dropped_when_not_abbreviation(title, expected):
    assert derive_company_name(_title(title), "xyz.com") == expected
