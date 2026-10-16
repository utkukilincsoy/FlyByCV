import io
import logging
import sys
from pathlib import Path

import pytest
from keyring.errors import KeyringError

from src.core import paths, selftest
from src.core.secrets import SERVICE_NAME, SMTP_ACCOUNT, service_name
from src.llm.providers import API_KEY_PROVIDERS

SECRET = "sk-ant-gizli-anahtar-123"


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str):
        return self.store.get((service, username))

    def get_keyring(self):
        return self


class _BrokenKeyring:
    def get_password(self, service: str, username: str):
        raise KeyringError("kasa açılamadı")


@pytest.fixture
def fake_keyring(monkeypatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    monkeypatch.setattr(selftest, "keyring", fake)
    return fake


# ---- kasa hesap durumu (var/yok) ------------------------------------------------


def test_checked_accounts_covers_llm_providers_and_smtp():
    """Denetlenen hesap listesi tüm LLM sağlayıcılarını ve Gmail şifresini kapsamalı."""
    assert set(selftest.CHECKED_ACCOUNTS) == {*API_KEY_PROVIDERS, SMTP_ACCOUNT}


def test_check_account_var_when_present(fake_keyring, monkeypatch):
    monkeypatch.delenv("FLYBYCV_HOME", raising=False)  # varsayılan kurulum kaydı aranır
    fake_keyring.store[(SERVICE_NAME, "claude")] = SECRET
    assert selftest.check_account("claude") == selftest.VAR


def test_check_account_reads_the_custom_home_record_not_the_default_one(
    fake_keyring, monkeypatch, tmp_path
):
    """`FLYBYCV_HOME` ile açılan ayrı kurulumun öz-testi gerçek kurulumun kaydını GÖRMEZ."""
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "test-kurulum"))
    fake_keyring.store[(SERVICE_NAME, "claude")] = SECRET  # gerçek (varsayılan) kurulumun kaydı
    assert selftest.check_account("claude") == selftest.YOK

    fake_keyring.store[(service_name(), "claude")] = SECRET
    assert selftest.check_account("claude") == selftest.VAR


def test_check_account_yok_when_missing(fake_keyring):
    assert selftest.check_account("gemini") == selftest.YOK


def test_check_account_erisilemedi_when_vault_broken(monkeypatch):
    monkeypatch.setattr(selftest, "keyring", _BrokenKeyring())
    assert selftest.check_account("groq") == selftest.ERISILEMEDI


# ---- şablon klasörü / cv.html -----------------------------------------------------


def test_templates_check_ok_when_cv_html_exists(monkeypatch, tmp_path):
    (tmp_path / "cv.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(paths, "templates_dir", lambda: tmp_path)

    ok, detail = selftest._check_templates()

    assert ok is True
    assert "cv.html" in detail


def test_templates_check_fails_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "templates_dir", lambda: tmp_path)

    ok, detail = selftest._check_templates()

    assert ok is False
    assert "bulunamadı" in detail


# ---- aktif kasa arka ucu -----------------------------------------------------------


class _NamedBackend:
    pass


class WinVaultKeyring(_NamedBackend):
    pass


class SomeOtherBackend(_NamedBackend):
    pass


def _keyring_returning(backend, monkeypatch):
    fake = type("Fake", (), {"get_keyring": staticmethod(lambda: backend)})
    monkeypatch.setattr(selftest, "keyring", fake)


def test_backend_check_ok_in_development_regardless_of_name(monkeypatch):
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    _keyring_returning(SomeOtherBackend(), monkeypatch)

    ok, detail = selftest._check_keyring_backend()

    assert ok is True
    assert detail == "SomeOtherBackend"


def test_backend_check_ok_when_frozen_with_win_vault(monkeypatch):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    _keyring_returning(WinVaultKeyring(), monkeypatch)

    ok, detail = selftest._check_keyring_backend()

    assert ok is True
    assert detail == "WinVaultKeyring"


def test_backend_check_fails_when_frozen_with_wrong_backend(monkeypatch):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    _keyring_returning(SomeOtherBackend(), monkeypatch)

    ok, detail = selftest._check_keyring_backend()

    assert ok is False
    assert "SomeOtherBackend" in detail
    assert "WinVaultKeyring" in detail


# ---- PySide6.QtPdf ------------------------------------------------------------------


def test_qtpdf_check_ok_when_importable():
    ok, detail = selftest._check_qtpdf()

    assert ok is True
    assert detail == "içe aktarılabiliyor"


def test_qtpdf_check_fails_when_not_importable(monkeypatch):
    monkeypatch.setitem(sys.modules, "PySide6.QtPdf", None)

    ok, detail = selftest._check_qtpdf()

    assert ok is False
    assert "içe aktarılamadı" in detail


# ---- Playwright + Chromium PDF üretimi ----------------------------------------------


class _FakePage:
    def __init__(self) -> None:
        self.html_set: list[str] = []

    def set_content(self, html: str, wait_until: str = "load") -> None:
        self.html_set.append(html)

    def pdf(self, path: str, **kwargs) -> None:
        Path(path).write_bytes(b"%PDF-fake-selftest-content")


class _FakeBrowser:
    def __init__(self) -> None:
        self.closed = False

    def new_page(self) -> _FakePage:
        return _FakePage()

    def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self._browser = browser

    def launch(self) -> _FakeBrowser:
        return self._browser


class _FakePlaywrightContext:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.chromium = _FakeChromium(browser)


class _FakeSyncPlaywright:
    def __init__(self, browser: _FakeBrowser) -> None:
        self._browser = browser

    def __enter__(self) -> _FakePlaywrightContext:
        return _FakePlaywrightContext(self._browser)

    def __exit__(self, *args) -> bool:
        return False


def test_playwright_check_ok_when_pdf_produced(monkeypatch):
    browser = _FakeBrowser()
    monkeypatch.setattr(selftest, "sync_playwright", lambda: _FakeSyncPlaywright(browser))

    ok, detail = selftest._check_playwright_pdf()

    assert ok is True
    assert "bayt" in detail
    assert browser.closed is True


def test_playwright_check_fails_when_launch_raises(monkeypatch):
    def _raising_sync_playwright():
        raise RuntimeError("chromium bulunamadı")

    monkeypatch.setattr(selftest, "sync_playwright", _raising_sync_playwright)

    ok, detail = selftest._check_playwright_pdf()

    assert ok is False
    assert detail == "RuntimeError"


def test_playwright_check_fails_when_pdf_is_empty(monkeypatch):
    class _EmptyPdfPage(_FakePage):
        def pdf(self, path: str, **kwargs) -> None:
            Path(path).touch()

    class _EmptyPdfBrowser(_FakeBrowser):
        def new_page(self) -> _EmptyPdfPage:
            return _EmptyPdfPage()

    browser = _EmptyPdfBrowser()
    monkeypatch.setattr(selftest, "sync_playwright", lambda: _FakeSyncPlaywright(browser))

    ok, detail = selftest._check_playwright_pdf()

    assert ok is False
    assert "boş" in detail


# ---- _print_safe(): kodlayamayan konsollarda çökmemeli -------------------------------


def test_print_safe_falls_back_on_unicode_encode_error(monkeypatch):
    """cp1252 gibi Türkçe karakter içermeyen bir konsolda çökmek yerine `?` ile yazmalı."""
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)

    selftest._print_safe("Şablon klasörü: TAMAM")
    stream.flush()

    written = buffer.getvalue().decode("cp1252")
    assert "?" in written


# ---- run(): birleşik rapor + çıkış kodu ----------------------------------------------


def _ok_checks() -> tuple:
    """`_CHECKS` ile aynı şekilde (etiket, fonksiyon) ama hepsi TAMAM dönen sahte denetimler."""
    return tuple((label, lambda: (True, "ok")) for label, _ in selftest._CHECKS)


def _patch_all_checks_ok(monkeypatch) -> None:
    monkeypatch.setattr(selftest, "_CHECKS", _ok_checks())


def test_run_returns_zero_when_all_checks_pass(fake_keyring, monkeypatch, capsys):
    _patch_all_checks_ok(monkeypatch)

    exit_code = selftest.run()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert f"{selftest.TAMAM} (ok)" in output
    assert selftest.HATA not in output


def test_run_returns_one_when_any_check_fails(fake_keyring, monkeypatch, capsys):
    _patch_all_checks_ok(monkeypatch)
    monkeypatch.setattr(
        selftest,
        "_CHECKS",
        (
            ("Şablon klasörü / cv.html", lambda: (True, "ok")),
            ("Kasa arka ucu", lambda: (False, "beklenmeyen arka uç")),
            ("PySide6.QtPdf", lambda: (True, "ok")),
            ("Playwright + Chromium PDF üretimi", lambda: (True, "ok")),
        ),
    )

    exit_code = selftest.run()
    output = capsys.readouterr().out

    assert exit_code == 1
    assert f"{selftest.HATA} (beklenmeyen arka uç)" in output


def test_run_reports_every_account(fake_keyring, monkeypatch, capsys):
    _patch_all_checks_ok(monkeypatch)

    selftest.run()
    output = capsys.readouterr().out

    for account in selftest.CHECKED_ACCOUNTS:
        assert account in output


def test_run_writes_report_to_log(fake_keyring, monkeypatch, caplog):
    _patch_all_checks_ok(monkeypatch)

    with caplog.at_level(logging.INFO):
        selftest.run()

    assert "Öz-test raporu" in caplog.text
    assert "Denetimler" in caplog.text


def test_run_never_prints_or_logs_the_secret_itself(fake_keyring, monkeypatch, capsys, caplog):
    _patch_all_checks_ok(monkeypatch)
    fake_keyring.store[(SERVICE_NAME, "claude")] = SECRET
    fake_keyring.store[(SERVICE_NAME, SMTP_ACCOUNT)] = "gizli-mail-sifresi"

    with caplog.at_level(logging.DEBUG):
        selftest.run()
    output = capsys.readouterr().out

    assert SECRET not in output
    assert "gizli-mail-sifresi" not in output
    assert SECRET not in caplog.text
    assert "gizli-mail-sifresi" not in caplog.text
