import logging
import re
import sys

import pytest
from keyring.errors import KeyringError, PasswordDeleteError

from src.core import paths, secrets
from src.core.secrets import (
    LEGACY_SERVICE_NAME,
    SERVICE_NAME,
    SMTP_ACCOUNT,
    configure_backend,
    delete_api_key,
    delete_mail_password,
    get_api_key,
    get_mail_password,
    migrate_legacy_secrets,
    set_api_key,
    set_mail_password,
)

SECRET = "sk-ant-gizli-anahtar-123"


class _FakeKeyring:
    """Bellek içi sahte şifre kasası; gerçek kasaya dokunmadan test etmek için."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str):
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.store:
            raise PasswordDeleteError("kayıt yok")
        del self.store[(service, username)]


class _BrokenKeyring:
    """Kasanın hiç açılamadığı durumu taklit eder (kurulu arka uç yok, kasa kilitli...)."""

    def get_password(self, service: str, username: str):
        raise KeyringError("kasa açılamadı")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise KeyringError("kasa açılamadı")

    def delete_password(self, service: str, username: str) -> None:
        raise KeyringError("kasa açılamadı")


@pytest.fixture(autouse=True)
def default_home(monkeypatch):
    """Geliştiricinin kabuğunda `FLYBYCV_HOME` kalmış olsa bile testler varsayılan kurulumla başlar."""
    monkeypatch.delenv("FLYBYCV_HOME", raising=False)


@pytest.fixture
def fake_keyring(monkeypatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets, "keyring", fake)
    return fake


@pytest.fixture
def broken_keyring(monkeypatch) -> None:
    monkeypatch.setattr(secrets, "keyring", _BrokenKeyring())


def test_set_then_get_roundtrip(fake_keyring):
    """Kaydedilen anahtar aynı sağlayıcı adıyla geri okunmalı."""
    set_api_key("claude", SECRET)
    assert get_api_key("claude") == SECRET


def test_key_is_stored_under_service_name(fake_keyring):
    """Kayıt "flybycv" servisi altında, kullanıcı adı olarak sağlayıcı adıyla durmalı."""
    set_api_key("groq", SECRET)
    assert fake_keyring.store == {(SERVICE_NAME, "groq"): SECRET}


def test_get_returns_none_when_missing(fake_keyring):
    """Kayıtlı anahtar yoksa hata değil None dönmeli."""
    assert get_api_key("gemini") is None


def test_set_strips_whitespace(fake_keyring):
    """Kopyala-yapıştırdan gelen satır sonu ve boşluklar temizlenmeli."""
    set_api_key("claude", f"  {SECRET}\n")
    assert get_api_key("claude") == SECRET


@pytest.mark.parametrize("key", ["", "   ", "\n"])
def test_empty_key_raises(fake_keyring, key):
    """Boş anahtar sessizce kaydedilmemeli."""
    with pytest.raises(ValueError, match="boş olamaz"):
        set_api_key("claude", key)
    assert fake_keyring.store == {}


def test_delete_existing_key(fake_keyring):
    """Var olan anahtar silinince True dönmeli ve kasada kalmamalı."""
    set_api_key("claude", SECRET)
    assert delete_api_key("claude") is True
    assert get_api_key("claude") is None


def test_delete_missing_key_returns_false(fake_keyring):
    """Olmayan anahtarı silmek hata fırlatmamalı, False dönmeli."""
    assert delete_api_key("claude") is False


def test_get_returns_none_when_keyring_broken(broken_keyring, caplog):
    """Kasa açılamazsa uygulama çökmemeli: None dönüp uyarı loglanmalı (CLAUDE.md kural 3)."""
    with caplog.at_level(logging.WARNING):
        assert get_api_key("claude") is None
    assert "Şifre kasası açılamadı" in caplog.text


def test_delete_returns_false_when_keyring_broken(broken_keyring):
    """Kasa açılamazsa silme hata fırlatmamalı."""
    assert delete_api_key("claude") is False


def test_set_raises_when_keyring_broken(broken_keyring):
    """Yazma hatası sessizce yutulmamalı: kullanıcı anahtarın kaydedilmediğini bilmeli."""
    with pytest.raises(KeyringError):
        set_api_key("claude", SECRET)


def test_secret_never_reaches_logs(fake_keyring, caplog):
    """Anahtarın kendisi hiçbir log kaydında geçmemeli (CLAUDE.md kural 6)."""
    with caplog.at_level(logging.DEBUG):
        set_api_key("claude", SECRET)
        get_api_key("claude")
        get_api_key("gemini")
        delete_api_key("claude")

    assert SECRET not in caplog.text


def test_mail_password_roundtrip(fake_keyring):
    """Gmail şifresi 'smtp' hesabı altında kaydedilip aynen geri okunmalı."""
    set_mail_password("abcdefghijklmnop")
    assert get_mail_password() == "abcdefghijklmnop"
    assert fake_keyring.store == {(SERVICE_NAME, SMTP_ACCOUNT): "abcdefghijklmnop"}


def test_mail_password_strips_inner_spaces(fake_keyring):
    """Google'ın 4'erli gösterdiği şifredeki ARADAKİ boşluklar da temizlenmeli."""
    set_mail_password("abcd efgh ijkl mnop")
    assert get_mail_password() == "abcdefghijklmnop"


def test_mail_password_delete(fake_keyring):
    """Kaydedilen şifre silinince kasada kalmamalı."""
    set_mail_password("abcdefghijklmnop")
    assert delete_mail_password() is True
    assert get_mail_password() is None


def test_mail_password_secret_never_logged(fake_keyring, caplog):
    """Gmail şifresi hiçbir log kaydında geçmemeli (CLAUDE.md kural 6)."""
    with caplog.at_level(logging.DEBUG):
        set_mail_password("abcd efgh ijkl mnop")
        get_mail_password()
        delete_mail_password()

    assert "abcdefghijklmnop" not in caplog.text
    assert "abcd efgh ijkl mnop" not in caplog.text


# ---------------------------------------------------------------------------
# Ev dizinine göre ayrı kasa kaydı (FLYBYCV_HOME)
# ---------------------------------------------------------------------------


def test_default_install_uses_the_new_service_name(fake_keyring):
    """`FLYBYCV_HOME` yokken servis adı "flybycv", eski adınki "cv-autopilot"."""
    assert secrets.service_name() == SERVICE_NAME == "flybycv"
    assert secrets.legacy_service_name() == LEGACY_SERVICE_NAME == "cv-autopilot"


def test_legacy_records_are_copied_to_the_new_service_and_kept(fake_keyring):
    """Eski adla (cv-autopilot) yazılmış kayıtlar yeni servise kopyalanır, eskiler SİLİNMEZ."""
    fake_keyring.store[("cv-autopilot", "claude")] = SECRET
    fake_keyring.store[("cv-autopilot", "smtp")] = "abcdefghijklmnop"

    copied = migrate_legacy_secrets()

    assert set(copied) == {"claude", "smtp"}
    assert get_api_key("claude") == SECRET
    assert get_mail_password() == "abcdefghijklmnop"
    assert fake_keyring.store[("cv-autopilot", "claude")] == SECRET
    assert fake_keyring.store[("cv-autopilot", "smtp")] == "abcdefghijklmnop"


def test_legacy_copy_never_overwrites_a_new_record(fake_keyring):
    fake_keyring.store[("cv-autopilot", "claude")] = "eski-anahtar"
    fake_keyring.store[("flybycv", "claude")] = "yeni-anahtar"

    assert migrate_legacy_secrets() == ()
    assert get_api_key("claude") == "yeni-anahtar"


def test_legacy_copy_is_idempotent_and_ignores_unknown_accounts(fake_keyring):
    fake_keyring.store[("cv-autopilot", "groq")] = SECRET
    fake_keyring.store[("cv-autopilot", "baska-hesap")] = "x"  # bizim listemizde olmayan hesap

    assert migrate_legacy_secrets() == ("groq",)
    assert migrate_legacy_secrets() == ()
    assert ("flybycv", "baska-hesap") not in fake_keyring.store


def test_legacy_copy_survives_an_unreachable_vault(broken_keyring):
    assert migrate_legacy_secrets() == ()


def test_custom_home_copies_only_its_own_legacy_records(fake_keyring, monkeypatch, tmp_path):
    """Ayrı kurulum (`flybycv-<özet>`) yalnızca kendi eski kaydını (`cv-autopilot-<özet>`) alır."""
    fake_keyring.store[("cv-autopilot", "claude")] = "gercek-kurulum"
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "test-kurulum"))
    digest = secrets.service_name().removeprefix("flybycv-")
    assert secrets.legacy_service_name() == f"cv-autopilot-{digest}"
    fake_keyring.store[(f"cv-autopilot-{digest}", "claude")] = "test-kurulumu"

    migrate_legacy_secrets()

    assert get_api_key("claude") == "test-kurulumu"
    assert ("flybycv", "claude") not in fake_keyring.store


def test_legacy_secret_values_are_never_logged(fake_keyring, caplog):
    fake_keyring.store[("cv-autopilot", "claude")] = SECRET
    with caplog.at_level(logging.DEBUG):
        migrate_legacy_secrets()

    assert "claude" in caplog.text
    assert SECRET not in caplog.text


def test_custom_home_uses_its_own_service_name(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "test-kurulum"))

    name = secrets.service_name()

    assert name != SERVICE_NAME
    assert name.startswith(f"{SERVICE_NAME}-")
    assert re.fullmatch(r"[0-9a-f]{12}", name.removeprefix(f"{SERVICE_NAME}-"))


def test_custom_home_does_not_see_or_touch_the_default_install_records(
    fake_keyring, monkeypatch, tmp_path
):
    """Gerçek kurulumun kaydı ayrı kurulumdan görünmez; ayrı kurulum yazınca/silince ona dokunmaz."""
    set_api_key("claude", SECRET)
    set_mail_password("gercek-sifre-1234")

    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "test-kurulum"))
    assert get_api_key("claude") is None
    assert get_mail_password() is None

    set_api_key("claude", "sk-ant-test-kurulumu")
    set_mail_password("test-sifre-5678")
    assert delete_api_key("claude") is True

    monkeypatch.delenv("FLYBYCV_HOME")
    assert get_api_key("claude") == SECRET  # üzerine yazılmadı, silinmedi
    assert get_mail_password() == "gercek-sifre-1234"


def test_two_custom_homes_do_not_share_records(fake_keyring, monkeypatch, tmp_path):
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "a"))
    set_api_key("groq", "anahtar-a")
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "b"))
    assert get_api_key("groq") is None
    set_api_key("groq", "anahtar-b")

    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "a"))
    assert get_api_key("groq") == "anahtar-a"
    assert len(fake_keyring.store) == 2


def test_same_custom_home_written_differently_shares_one_record(monkeypatch, tmp_path):
    """Aynı klasörün farklı yazımı (`..` ile dolaşan yol) AYNI kurulumdur."""
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "ev"))
    first = secrets.service_name()
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "baska" / ".." / "ev"))

    assert secrets.service_name() == first


def test_home_pointing_at_the_default_location_is_the_default_install(monkeypatch):
    """`FLYBYCV_HOME`'u varsayılan klasörün kendisine ayarlamak ayrı kurulum DEĞİLDİR."""
    monkeypatch.setenv("FLYBYCV_HOME", str(paths.default_app_home()))

    assert secrets.service_name() == SERVICE_NAME


def test_service_name_does_not_expose_the_raw_path(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "ozel-klasor"))

    assert "ozel-klasor" not in secrets.service_name()


class _RecordingKeyring:
    """`set_keyring` çağrısını kaydeder; gerçek küresel kasa arka ucuna dokunmaz."""

    def __init__(self) -> None:
        self.backends: list = []

    def set_keyring(self, backend) -> None:
        self.backends.append(backend)


def test_configure_backend_noop_in_development(monkeypatch):
    """Geliştirmede (`is_frozen()` False) arka uç DEĞİŞTİRİLMEMELİ — otomatik keşif yeterli."""
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    recording = _RecordingKeyring()
    monkeypatch.setattr(secrets, "keyring", recording)

    configure_backend()

    assert recording.backends == []


def test_configure_backend_sets_windows_vault_when_frozen(monkeypatch):
    """Paketlenmişken (Windows) kasa arka ucu elle `WinVaultKeyring` olarak seçilmeli."""
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "platform", "win32")
    recording = _RecordingKeyring()
    monkeypatch.setattr(secrets, "keyring", recording)

    configure_backend()

    assert len(recording.backends) == 1
    assert type(recording.backends[0]).__name__ == "WinVaultKeyring"


def test_configure_backend_noop_on_non_windows_when_frozen(monkeypatch):
    """Paketlenmiş ama Windows dışı bir platformda (varsayımsal) arka uç değiştirilmemeli."""
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    recording = _RecordingKeyring()
    monkeypatch.setattr(secrets, "keyring", recording)

    configure_backend()

    assert recording.backends == []


def test_configure_backend_handles_import_error(monkeypatch, caplog):
    """Windows kasa modülü içe aktarılamazsa çökmemeli, sadece uyarı loglanmalı."""
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "keyring.backends.Windows", None)
    recording = _RecordingKeyring()
    monkeypatch.setattr(secrets, "keyring", recording)

    with caplog.at_level(logging.WARNING):
        configure_backend()

    assert recording.backends == []
    assert "içe aktarılamadı" in caplog.text
