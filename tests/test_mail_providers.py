"""`src/core/mail_providers.py` testleri (PLAN.md Adım 10.7) — ağa hiç çıkmaz."""
import json

from src.core.mail_providers import (
    load_mail_provider_presets,
    resolve_connection_for_provider,
    resolve_mail_connection,
)
from src.core.settings import (
    KEY_CUSTOM_IMAP_HOST,
    KEY_CUSTOM_IMAP_PORT,
    KEY_CUSTOM_SMTP_HOST,
    KEY_CUSTOM_SMTP_PORT,
    KEY_CUSTOM_SMTP_SECURITY,
    KEY_MAIL_PROVIDER,
    set_setting,
)
from src.sender.mail_connection import GMAIL_CONNECTION, PROVIDER_CUSTOM, SECURITY_SSL


def _write_presets(tmp_path, providers):
    path = tmp_path / "mail_providers.json"
    path.write_text(json.dumps({"providers": providers}), encoding="utf-8")
    return path


# --- load_mail_provider_presets ------------------------------------------------------------


def test_load_presets_reads_valid_file(tmp_path):
    path = _write_presets(
        tmp_path,
        [
            {
                "id": "gmail",
                "name_tr": "Gmail",
                "supported": True,
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
                "smtp_security": "starttls",
                "imap_host": "imap.gmail.com",
                "imap_port": 993,
            },
            {
                "id": "yandex",
                "name_tr": "Yandex",
                "supported": True,
                "smtp_host": "smtp.yandex.com",
                "smtp_port": 465,
                "smtp_security": "ssl",
                "imap_host": "imap.yandex.com",
                "imap_port": 993,
            },
        ],
    )
    presets = load_mail_provider_presets(path)
    assert set(presets) == {"gmail", "yandex"}
    assert presets["yandex"].smtp_security == SECURITY_SSL


def test_load_presets_falls_back_to_gmail_when_file_missing(tmp_path):
    presets = load_mail_provider_presets(tmp_path / "yok.json")
    assert set(presets) == {"gmail"}
    assert presets["gmail"].supported is True


def test_load_presets_skips_invalid_entry_but_keeps_others(tmp_path):
    path = _write_presets(
        tmp_path,
        [
            {
                "id": "gmail",
                "name_tr": "Gmail",
                "supported": True,
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
                "smtp_security": "starttls",
                "imap_host": "imap.gmail.com",
                "imap_port": 993,
            },
            {
                "id": "bozuk",
                "name_tr": "Bozuk",
                "supported": True,
                "smtp_host": "x",
                "smtp_port": 99999,  # aralık dışı -> reddedilir
                "smtp_security": "starttls",
                "imap_host": "x",
                "imap_port": 993,
            },
        ],
    )
    presets = load_mail_provider_presets(path)
    assert set(presets) == {"gmail"}


def test_load_presets_falls_back_when_gmail_missing_from_file(tmp_path):
    path = _write_presets(
        tmp_path,
        [
            {
                "id": "yandex",
                "name_tr": "Yandex",
                "supported": True,
                "smtp_host": "smtp.yandex.com",
                "smtp_port": 465,
                "smtp_security": "ssl",
                "imap_host": "imap.yandex.com",
                "imap_port": 993,
            }
        ],
    )
    presets = load_mail_provider_presets(path)
    assert "gmail" in presets  # yedeğe düşüldü
    assert "yandex" in presets


def test_outlook_preset_is_marked_unsupported():
    presets = load_mail_provider_presets()
    assert "outlook" in presets
    assert presets["outlook"].supported is False
    assert presets["outlook"].unsupported_reason_tr


def test_gmail_and_yandex_have_a_step_by_step_password_guide():
    presets = load_mail_provider_presets()
    for provider in ("gmail", "yandex"):
        guide = presets[provider].password_guide_tr
        assert guide and len(guide) >= 4
        assert all(step.strip() and not step.lstrip()[0].isdigit() for step in guide)  # numarayı arayüz ekler
    assert presets["outlook"].password_guide_tr is None  # desteklenmeyen sağlayıcıda rehber yok


# --- resolve_connection_for_provider --------------------------------------------------------


def test_resolve_connection_for_known_supported_provider(tmp_path):
    presets = load_mail_provider_presets(
        _write_presets(
            tmp_path,
            [
                {
                    "id": "yandex",
                    "name_tr": "Yandex",
                    "supported": True,
                    "smtp_host": "smtp.yandex.com",
                    "smtp_port": 465,
                    "smtp_security": "ssl",
                    "imap_host": "imap.yandex.com",
                    "imap_port": 993,
                }
            ],
        )
    )
    connection = resolve_connection_for_provider("yandex", presets)
    assert connection.smtp_host == "smtp.yandex.com"
    assert connection.smtp_security == SECURITY_SSL
    assert connection.imap_port == 993


def test_resolve_connection_for_custom_provider_with_full_fields():
    connection = resolve_connection_for_provider(
        PROVIDER_CUSTOM,
        {},
        custom_smtp_host="mail.sirket.com",
        custom_smtp_port=587,
        custom_smtp_security="starttls",
        custom_imap_host="imap.sirket.com",
        custom_imap_port=993,
    )
    assert connection.provider == PROVIDER_CUSTOM
    assert connection.smtp_host == "mail.sirket.com"
    assert connection.imap_host == "imap.sirket.com"


def test_resolve_connection_for_custom_provider_with_missing_fields_falls_back_to_gmail():
    connection = resolve_connection_for_provider(PROVIDER_CUSTOM, {}, custom_smtp_host="mail.sirket.com")
    assert connection == GMAIL_CONNECTION


def test_resolve_connection_for_unsupported_provider_falls_back_to_gmail(tmp_path):
    presets = load_mail_provider_presets()  # gerçek dosyada outlook supported=False
    connection = resolve_connection_for_provider("outlook", presets)
    assert connection == GMAIL_CONNECTION


def test_resolve_connection_for_unknown_provider_falls_back_to_gmail():
    connection = resolve_connection_for_provider("bilinmeyen", {})
    assert connection == GMAIL_CONNECTION


# --- resolve_mail_connection (session üzerinden ayarları okur) -----------------------------


def test_resolve_mail_connection_defaults_to_gmail_for_existing_user(session):
    """Hiç `mail_provider` yazılmamış bir kayıt (Faz 10 öncesi kullanıcı) Gmail'de kalmalı."""
    connection = resolve_mail_connection(session)
    assert connection == GMAIL_CONNECTION


def test_resolve_mail_connection_reads_custom_settings(session):
    set_setting(session, KEY_MAIL_PROVIDER, PROVIDER_CUSTOM)
    set_setting(session, KEY_CUSTOM_SMTP_HOST, "mail.sirket.com")
    set_setting(session, KEY_CUSTOM_SMTP_PORT, 25)
    set_setting(session, KEY_CUSTOM_SMTP_SECURITY, "starttls")
    set_setting(session, KEY_CUSTOM_IMAP_HOST, "imap.sirket.com")
    set_setting(session, KEY_CUSTOM_IMAP_PORT, 143)
    session.commit()

    connection = resolve_mail_connection(session)
    assert connection.smtp_host == "mail.sirket.com"
    assert connection.smtp_port == 25
    assert connection.imap_host == "imap.sirket.com"
