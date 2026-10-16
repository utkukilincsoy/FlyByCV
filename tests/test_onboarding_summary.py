"""src/ui/onboarding_summary.py testleri (Qt YOK -- PLAN.md Adım 10.2m)."""
import pytest

from src.core import paths, secrets
from src.core.models import Setting
from src.core.settings import (
    CV_MODE_ATS,
    CV_MODE_OWN_PDF,
    KEY_APPLICATION_TARGET,
    KEY_AUTOPILOT_CITY,
    KEY_CV_MODE,
    KEY_MAIL_PROVIDER,
    KEY_MAIL_TEMPLATE_SUBJECT,
    KEY_PROFESSION,
    KEY_SENDER_EMAIL,
    KEY_TEST_MODE,
    KEY_USE_OSM,
    set_setting,
)
from src.cv_builder.mail_template import TARGET_INTERNSHIP
from src.cv_builder.profile_editor import write_profile
from src.ui import onboarding_summary as summary
from tests.conftest import build_sample_profile


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets, "keyring", _FakeKeyring())
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "home"))


@pytest.fixture
def profile_path(tmp_path):
    path = tmp_path / "master_profile.json"
    write_profile(path, build_sample_profile())
    return path


def _as_dict(result: summary.FinishSummary) -> dict[str, str]:
    return {line.label: line.value for line in result.lines}


def _configure(session) -> None:
    set_setting(session, KEY_APPLICATION_TARGET, TARGET_INTERNSHIP)
    set_setting(session, KEY_PROFESSION, "yazilim")
    set_setting(session, KEY_AUTOPILOT_CITY, "06")
    set_setting(session, KEY_USE_OSM, True)
    set_setting(session, KEY_CV_MODE, CV_MODE_ATS)
    set_setting(session, KEY_SENDER_EMAIL, "ben@gmail.com")
    set_setting(session, KEY_MAIL_PROVIDER, "gmail")
    set_setting(session, KEY_MAIL_TEMPLATE_SUBJECT, "{sirket_adi} – Staj Başvurusu")
    session.commit()


def test_fully_configured_install_is_summarised(session, profile_path):
    _configure(session)
    secrets.set_mail_password("abcd efgh ijkl mnop")

    lines = _as_dict(summary.build_finish_summary(session, profile_path))

    assert lines["Ad soyad"] == "Test Kişi"
    assert lines["E-posta"] == "test@example.com"
    assert lines["Aranan"] == "staj"
    assert lines["Meslek"] == "Yazılım / Bilgisayar Mühendisliği"
    assert lines["Şehir"] == "Ankara"
    assert lines["CV"] == "ATS CV oluştur (mevcut sistem)"
    assert lines["Mail hesabı"] == "ben@gmail.com (Gmail; uygulama şifresi kayıtlı)"
    assert "OpenStreetMap" in lines["Firma kaynakları"]
    assert lines["Mail konusu"] == "{sirket_adi} – Staj Başvurusu"


def test_summary_never_contains_the_secret(session, profile_path):
    _configure(session)
    secrets.set_mail_password("abcd efgh ijkl mnop")

    result = summary.build_finish_summary(session, profile_path)

    assert "abcd" not in " ".join(f"{line.label} {line.value}" for line in result.lines)


def test_own_pdf_mode_shows_the_stored_file_status(session, profile_path):
    _configure(session)
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)
    session.commit()

    assert "Henüz bir PDF seçilmedi" in _as_dict(summary.build_finish_summary(session, profile_path))["CV"]

    paths.own_cv_path().parent.mkdir(parents=True, exist_ok=True)
    paths.own_cv_path().write_bytes(b"%PDF-1.4 x")
    cv_line = _as_dict(summary.build_finish_summary(session, profile_path))["CV"]
    assert cv_line.startswith("Kendi PDF'imi olduğu gibi ekle") and "own_cv.pdf" in cv_line


def test_missing_password_is_reported(session, profile_path):
    _configure(session)

    lines = _as_dict(summary.build_finish_summary(session, profile_path))

    assert "uygulama şifresi kayıtlı DEĞİL" in lines["Mail hesabı"]


def test_fresh_install_shows_not_set_markers_and_does_not_crash(session, tmp_path):
    lines = _as_dict(summary.build_finish_summary(session, tmp_path / "yok.json"))

    assert lines["Kişisel bilgiler"] == summary.NOT_SET
    assert lines["Aranan"] == summary.NOT_SET
    assert lines["Meslek"] == summary.NOT_SET
    assert lines["Şehir"] == summary.NOT_SET
    assert lines["Firma kaynakları"] == summary.NO_SOURCES_TEXT


def test_broken_profile_is_reported_and_left_untouched(session, tmp_path):
    broken = tmp_path / "bozuk.json"
    broken.write_text("{bozuk", encoding="utf-8")

    lines = _as_dict(summary.build_finish_summary(session, broken))

    assert lines["Kişisel bilgiler"] == summary.PROFILE_UNREADABLE
    assert broken.read_text(encoding="utf-8") == "{bozuk"


def test_custom_provider_and_custom_profession(session, profile_path):
    _configure(session)
    set_setting(session, KEY_MAIL_PROVIDER, "custom")
    set_setting(session, KEY_PROFESSION, "diger")
    set_setting(session, "custom_profession_name", "Aşçı")
    set_setting(session, "custom_profession_keywords", ["restoran"])
    session.commit()

    lines = _as_dict(summary.build_finish_summary(session, profile_path))

    assert "Özel sunucu" in lines["Mail hesabı"]
    assert lines["Meslek"] == "Aşçı"
    assert lines["Firma kaynakları"] == summary.NO_SOURCES_TEXT  # "Diğer": OSM etiketi yok, teknokent yok


def test_test_mode_is_on_by_default_and_the_summary_does_not_write_it(session, profile_path):
    result = summary.build_finish_summary(session, profile_path)

    assert result.test_mode is True
    assert result.test_mode_text == summary.TEST_MODE_ON_TEXT
    assert session.get(Setting, KEY_TEST_MODE) is None  # özet hiçbir ayar satırı yazmadı


def test_test_mode_off_is_reported_honestly(session, profile_path):
    set_setting(session, KEY_TEST_MODE, False)
    session.commit()

    result = summary.build_finish_summary(session, profile_path)

    assert result.test_mode is False
    assert result.test_mode_text == summary.TEST_MODE_OFF_TEXT
    assert "GERÇEK GÖNDERİM" in result.test_mode_text
