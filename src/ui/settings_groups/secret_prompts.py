"""API anahtarı / mail şifresi sor-kaydet-sil diyalogları (CLAUDE.md kural 6).

Hiçbir widget sırrı TUTMAZ: metin `QInputDialog`'dan doğrudan `src/core/secrets.py`'ye (keyring)
gider; ekranda yalnızca "kayıtlı ✓ / kayıtlı değil" görünür. Bu fonksiyonlar ne bir grubu ne bir
sekmeyi bilir -- durum etiketini tazelemek çağıranın işi.
"""
from typing import Callable, Optional

from keyring.errors import KeyringError
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox, QWidget

from src.core.secrets import (
    delete_api_key,
    delete_mail_password,
    get_mail_password,
    set_api_key,
    set_mail_password,
)
from src.ui.settings_data import format_secret_status


def prompt_save_api_key(parent: QWidget, provider: str, title: Optional[str] = None) -> bool:
    """Anahtarı sorar ve kaydeder. Kaydedildiyse `True` (çağıran durumu tazeler)."""
    text, ok = QInputDialog.getText(
        parent, title or f"{provider} API anahtarı", "Anahtar:", QLineEdit.Password
    )
    if not ok:
        return False
    try:
        set_api_key(provider, text)
    except ValueError as error:
        QMessageBox.warning(parent, "Anahtar kaydedilemedi", str(error))
        return False
    except KeyringError as error:
        QMessageBox.warning(parent, "Anahtar kaydedilemedi", f"Şifre kasasına yazılamadı: {error}")
        return False
    return True


def delete_api_key_with_notice(
    parent: QWidget, provider: str, refresh: Callable[[], None], label: Optional[str] = None
) -> None:
    """Anahtarı siler, `refresh()` ile durumu tazeler, kayıt yoktuysa bilgi verir."""
    deleted = delete_api_key(provider)
    refresh()
    if not deleted:
        QMessageBox.information(
            parent, "Kayıt yok", f"{label or provider} için kayıtlı bir anahtar yoktu."
        )


def prompt_save_mail_password(parent: QWidget) -> bool:
    text, ok = QInputDialog.getText(
        parent, "Mail uygulama şifresi", "Uygulama şifresi:", QLineEdit.Password
    )
    if not ok:
        return False
    try:
        set_mail_password(text)
    except ValueError as error:
        QMessageBox.warning(parent, "Şifre kaydedilemedi", str(error))
        return False
    except KeyringError as error:
        QMessageBox.warning(parent, "Şifre kaydedilemedi", f"Şifre kasasına yazılamadı: {error}")
        return False
    return True


def delete_mail_password_with_notice(parent: QWidget, status_label) -> None:
    deleted = delete_mail_password()
    status_label.setText(format_secret_status(bool(get_mail_password())))
    if not deleted:
        QMessageBox.information(parent, "Kayıt yok", "Kayıtlı bir mail uygulama şifresi yoktu.")
