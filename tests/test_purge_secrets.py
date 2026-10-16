"""secrets.purge_all_secrets + `--purge-secrets` bayrağı (Adım 10.9e). Gerçek şifre kasasına DOKUNMAZ:
`secrets.keyring` her testte otomatik olarak bellek içi sahteyle değiştirilir."""
import pytest
from keyring.errors import KeyringError, PasswordDeleteError

import main as main_module
from src.autopilot.runner import GOOGLE_PLACES_API_KEY_NAME as RUNNER_PLACES_NAME
from src.company_finder.source_selector import GOOGLE_PLACES_API_KEY_NAME as SELECTOR_PLACES_NAME
from src.core import paths, purge_secrets, secrets
from src.core.secrets import (
    ALL_SECRET_ACCOUNTS,
    GOOGLE_PLACES_ACCOUNT,
    LEGACY_SERVICE_NAME,
    SERVICE_NAME,
    SMTP_ACCOUNT,
    PurgeReport,
    purge_all_secrets,
    service_name,
)
from src.llm.providers import API_KEY_PROVIDERS

SECRET = "sk-ant-gizli-anahtar-123"


class _FakeKeyring:
    def __init__(self, unreachable: tuple[str, ...] = ()) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.unreachable = unreachable

    def delete_password(self, service: str, username: str) -> None:
        if username in self.unreachable:
            raise KeyringError("kasa açılamadı")
        if (service, username) not in self.store:
            raise PasswordDeleteError("kayıt yok")
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch) -> _FakeKeyring:
    """Her test sahte kasayla çalışır; gerçek Windows Kimlik Bilgisi Yöneticisi'ne gidilemez."""
    monkeypatch.delenv("FLYBYCV_HOME", raising=False)
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets, "keyring", fake)
    return fake


def test_account_list_covers_every_secret_the_app_writes():
    """LLM sağlayıcıları + Google Places + Gmail şifresi; biri unutulursa "verilerimi de sil" onu bırakırdı."""
    assert set(ALL_SECRET_ACCOUNTS) == {*API_KEY_PROVIDERS, GOOGLE_PLACES_ACCOUNT, SMTP_ACCOUNT}
    assert len(set(ALL_SECRET_ACCOUNTS)) == len(ALL_SECRET_ACCOUNTS)
    # Google Places adı üç yerde taşınıyor; ayrışırlarsa anahtar kasada kalır.
    assert GOOGLE_PLACES_ACCOUNT == SELECTOR_PLACES_NAME == RUNNER_PLACES_NAME


def test_purge_deletes_every_account_of_this_installation(fake_keyring):
    for account in ALL_SECRET_ACCOUNTS:
        fake_keyring.store[(SERVICE_NAME, account)] = SECRET

    report = purge_all_secrets()

    assert fake_keyring.store == {}
    assert set(report.deleted) == set(ALL_SECRET_ACCOUNTS)
    assert report.absent == () and report.failed == ()
    assert report.ok is True


def test_purge_also_deletes_the_records_left_under_the_old_app_name(fake_keyring):
    """Ad değişikliğinden önceki kayıtlar (`cv-autopilot`) da silinir: "verilerimi sil" eskileri bırakmaz."""
    fake_keyring.store[(LEGACY_SERVICE_NAME, "claude")] = SECRET
    fake_keyring.store[(LEGACY_SERVICE_NAME, SMTP_ACCOUNT)] = "eski-sifre"
    fake_keyring.store[(SERVICE_NAME, SMTP_ACCOUNT)] = "yeni-sifre"

    report = purge_all_secrets()

    assert fake_keyring.store == {}
    assert set(report.deleted) == {"claude", SMTP_ACCOUNT}
    assert report.ok is True


def test_purge_with_nothing_stored_is_a_success(fake_keyring):
    report = purge_all_secrets()

    assert report.deleted == () and report.failed == ()
    assert set(report.absent) == set(ALL_SECRET_ACCOUNTS)
    assert report.ok is True


def test_purge_leaves_other_services_and_unrelated_accounts_alone(fake_keyring):
    fake_keyring.store[(SERVICE_NAME, "claude")] = SECRET
    fake_keyring.store[(SERVICE_NAME, "baska-hesap")] = "x"  # bizim listemizde olmayan hesap
    fake_keyring.store[("baska-uygulama", "claude")] = "y"  # başka uygulama aynı kullanıcı adıyla
    fake_keyring.store[(f"{SERVICE_NAME}-aaaaaaaaaaaa", "claude")] = "z"  # ayrı FLYBYCV_HOME kurulumu

    purge_all_secrets()

    assert fake_keyring.store == {
        (SERVICE_NAME, "baska-hesap"): "x",
        ("baska-uygulama", "claude"): "y",
        (f"{SERVICE_NAME}-aaaaaaaaaaaa", "claude"): "z",
    }


def test_purge_only_touches_the_custom_home_service_when_one_is_set(
    fake_keyring, monkeypatch, tmp_path
):
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "test-kurulum"))
    custom_service = service_name()
    assert custom_service != SERVICE_NAME
    fake_keyring.store[(custom_service, "claude")] = SECRET
    fake_keyring.store[(SERVICE_NAME, "claude")] = SECRET  # gerçek (varsayılan) kurulumun kaydı

    report = purge_all_secrets()

    assert report.deleted == ("claude",)
    assert fake_keyring.store == {(SERVICE_NAME, "claude"): SECRET}


def test_purge_keeps_going_after_an_unreachable_account_and_reports_it(fake_keyring):
    fake_keyring.unreachable = ("gemini",)
    fake_keyring.store[(SERVICE_NAME, "gemini")] = SECRET
    fake_keyring.store[(SERVICE_NAME, SMTP_ACCOUNT)] = SECRET

    report = purge_all_secrets()

    assert report.failed == ("gemini",)
    assert report.ok is False
    assert SMTP_ACCOUNT in report.deleted  # sonrasındaki hesap yine de silindi
    assert (SERVICE_NAME, SMTP_ACCOUNT) not in fake_keyring.store


def test_report_and_logs_contain_account_names_but_never_a_secret(fake_keyring, caplog, capsys):
    import logging

    fake_keyring.store[(SERVICE_NAME, "claude")] = SECRET
    fake_keyring.store[(SERVICE_NAME, SMTP_ACCOUNT)] = "uygulama-sifresi-1234"

    with caplog.at_level(logging.DEBUG):
        exit_code = purge_secrets.run()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "claude" in out and purge_secrets.DELETED in out
    for leaked in (SECRET, "uygulama-sifresi-1234"):
        assert leaked not in out
        assert leaked not in caplog.text


def test_run_exits_1_when_some_account_could_not_be_deleted(fake_keyring, capsys):
    fake_keyring.unreachable = (SMTP_ACCOUNT,)

    assert purge_secrets.run() == 1
    assert purge_secrets.FAILED in capsys.readouterr().out


def test_format_report_lists_all_three_states():
    text = purge_secrets.format_report(
        PurgeReport(deleted=("claude",), absent=("groq",), failed=("smtp",))
    )
    assert f"claude: {purge_secrets.DELETED}" in text
    assert f"groq: {purge_secrets.ABSENT}" in text
    assert f"smtp: {purge_secrets.FAILED}" in text


# ---- main.py bayrağı --------------------------------------------------------------------------


def _stub_startup(monkeypatch) -> None:
    """`main()`'in bayraktan önceki hazırlıkları (log dosyası, Playwright yolu, kasa arka ucu) etkisiz."""
    monkeypatch.setattr(main_module, "configure_logging", lambda: None)
    monkeypatch.setattr(paths, "configure_playwright_browsers_path", lambda: None)
    monkeypatch.setattr(secrets, "configure_backend", lambda: None)
    monkeypatch.setattr(secrets, "migrate_legacy_secrets", lambda: ())


def test_purge_flag_runs_the_purge_without_opening_the_ui(monkeypatch):
    _stub_startup(monkeypatch)
    monkeypatch.setattr(main_module.sys, "argv", ["main.py", "--purge-secrets"])
    monkeypatch.setattr(purge_secrets, "run", lambda: 7)

    def no_ui(*args, **kwargs):
        raise AssertionError("arayüz/veritabanı açılmamalı")

    monkeypatch.setattr(main_module, "QApplication", no_ui)
    monkeypatch.setattr(main_module, "get_engine", no_ui)

    assert main_module.main() == 7


def test_purge_does_not_run_without_the_explicit_flag(monkeypatch):
    _stub_startup(monkeypatch)
    monkeypatch.setattr(main_module.sys, "argv", ["main.py", "--purge", "--secrets", "--selftest-x"])
    monkeypatch.setattr(purge_secrets, "run", lambda: pytest.fail("bayrak verilmeden silinemez"))

    class Stop(Exception):
        pass

    def stop(*args, **kwargs):
        raise Stop

    monkeypatch.setattr(main_module, "QApplication", stop)  # normal açılışa geçtiğini kanıtlar

    with pytest.raises(Stop):
        main_module.main()
