"""\"Mail hesabı\" grubu: gönderen, sağlayıcı (+ \"Özel\" SMTP/IMAP alanları), uygulama şifresi, test.

Şifreyi kaydet/sil grubun içinde; ağa çıkan "Bağlantıyı test et" yalnızca `test_requested`
yayar (işi barındıran pencere arka planda çalıştırır, CLAUDE.md kural 8).
"""
from typing import Any, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QWidget,
)

from src.core.mail_providers import MailProviderPreset
from src.core.secrets import get_mail_password
from src.sender.mail_connection import PROVIDER_CUSTOM, SECURITIES, SECURITY_SSL, SECURITY_STARTTLS
from src.ui.jobs import SmtpLoginTestJobResult
from src.ui.settings_data import SettingsForm, format_secret_status
from src.ui.settings_groups.common import add_key_row, hint_label, select_index_by_data
from src.ui.settings_groups.secret_prompts import (
    delete_mail_password_with_notice,
    prompt_save_mail_password,
)

#: `custom_smtp_security` combo'sundaki Türkçe etiketler; ayar değeri her zaman
#: "starttls"/"ssl" kalır (bkz. `src/sender/mail_connection.py`).
SMTP_SECURITY_LABELS_TR = {
    SECURITY_STARTTLS: "STARTTLS (genelde 587)",
    SECURITY_SSL: "SSL/TLS (genelde 465)",
}

#: "Özel" sağlayıcı seçeneğinin combo'daki etiketi — `presets/mail_providers.json`de YOK
#: (bkz. `src/sender/mail_connection.PROVIDER_CUSTOM`), burada elle eklenir.
CUSTOM_PROVIDER_LABEL_TR = "Özel (host/port kendim gireceğim)"


class MailAccountGroup(QGroupBox):
    test_requested = Signal()

    def __init__(
        self, mail_presets: dict[str, MailProviderPreset], parent: Optional[QWidget] = None
    ) -> None:
        super().__init__("Mail hesabı", parent)
        self._mail_presets = mail_presets
        form = QFormLayout()

        self.sender_email_edit = QLineEdit(self)
        form.addRow("Gönderen e-posta", self.sender_email_edit)

        self.sender_name_edit = QLineEdit(self)
        form.addRow("Gönderen adı", self.sender_name_edit)

        self.mail_provider_combo = QComboBox(self)
        for provider_id, preset in self._mail_presets.items():
            self.mail_provider_combo.addItem(preset.name_tr, provider_id)
            if not preset.supported:
                index = self.mail_provider_combo.count() - 1
                item = self.mail_provider_combo.model().item(index)
                item.setEnabled(False)
                item.setToolTip(preset.unsupported_reason_tr or "Desteklenmiyor")
        self.mail_provider_combo.addItem(CUSTOM_PROVIDER_LABEL_TR, PROVIDER_CUSTOM)
        self.mail_provider_combo.currentIndexChanged.connect(self._on_mail_provider_changed)
        form.addRow("Mail sağlayıcısı", self.mail_provider_combo)

        self.mail_provider_hint_label = hint_label("", self)
        form.addRow(self.mail_provider_hint_label)

        # -- yalnızca "Özel" seçiliyken görünür (Adım 10.7a) -----------------------------
        self.custom_smtp_host_edit = QLineEdit(self)
        form.addRow("Özel SMTP sunucusu", self.custom_smtp_host_edit)

        # `setRange(0, ...)` + `setSpecialValueText`: 0, ayarlarda `None` (port hiç girilmemiş)
        # anlamına gelir -- `max_projects_spin`/`inbox_check_minutes_spin` ile aynı kalıp;
        # aksi halde ekrandaki "boş" bir port her zaman bir tamsayı göstermek zorunda kalır ve
        # `form() != _loaded_form` (bkz. `is_dirty()`) hiçbir şey değişmeden de True kalırdı.
        self.custom_smtp_port_spin = QSpinBox(self)
        self.custom_smtp_port_spin.setRange(0, 65535)
        self.custom_smtp_port_spin.setSpecialValueText("(boş)")
        form.addRow("Özel SMTP portu", self.custom_smtp_port_spin)

        self.custom_smtp_security_combo = QComboBox(self)
        for security in SECURITIES:
            self.custom_smtp_security_combo.addItem(SMTP_SECURITY_LABELS_TR[security], security)
        form.addRow("Özel SMTP güvenliği", self.custom_smtp_security_combo)

        self.custom_imap_host_edit = QLineEdit(self)
        form.addRow("Özel IMAP sunucusu", self.custom_imap_host_edit)

        self.custom_imap_port_spin = QSpinBox(self)
        self.custom_imap_port_spin.setRange(0, 65535)
        self.custom_imap_port_spin.setSpecialValueText("(boş)")
        form.addRow("Özel IMAP portu", self.custom_imap_port_spin)

        #: `QFormLayout.addRow` etiket+alan çiftini iki ayrı widget olarak ekler; ikisini
        #: birden gizleyebilmek için widget listesi tutuluyor (bkz. `_update_custom_fields_visible`).
        self._custom_field_widgets: list[QWidget] = [
            self.custom_smtp_host_edit,
            self.custom_smtp_port_spin,
            self.custom_smtp_security_combo,
            self.custom_imap_host_edit,
            self.custom_imap_port_spin,
        ]
        self._custom_field_labels: list[QWidget] = [
            form.labelForField(widget) for widget in self._custom_field_widgets
        ]

        self.password_status_label = add_key_row(
            form,
            self,
            "Uygulama şifresi",
            on_save=self.save_password,
            on_delete=self.delete_password,
            save_text="Şifreyi kaydet",
            delete_text="Şifreyi sil",
        )

        self.test_button = QPushButton("Bağlantıyı test et", self)
        self.test_button.clicked.connect(lambda: self.test_requested.emit())
        form.addRow(self.test_button)

        form.addRow(hint_label("Sadece bağlanıp giriş yapar, mail GÖNDERMEZ.", self))

        self.inbox_check_minutes_spin = QSpinBox(self)
        self.inbox_check_minutes_spin.setRange(0, 999)
        self.inbox_check_minutes_spin.setSuffix(" dk")
        self.inbox_check_minutes_spin.setSpecialValueText("kapalı")
        form.addRow("Gelen kutusunu otomatik kontrol et", self.inbox_check_minutes_spin)

        inbox_check_hint = hint_label(
            "Cevaplar sekmesi açıkken bu aralıkla kendiliğinden kontrol eder (salt okuma). "
            "0 = kapalı, yalnızca \"Şimdi kontrol et\" ile elle çalışır.",
            self,
        )
        form.addRow(inbox_check_hint)

        self._update_mail_provider_hint()
        self._update_custom_fields_visible()

        self.setLayout(form)

    def _on_mail_provider_changed(self, *_args) -> None:
        self._update_mail_provider_hint()
        self._update_custom_fields_visible()

    def _update_mail_provider_hint(self) -> None:
        provider = self.mail_provider_combo.currentData()
        if provider == PROVIDER_CUSTOM:
            self.mail_provider_hint_label.setText(
                "Sunucunuzun SMTP/IMAP ayarlarını sağlayıcınızdan (ör. şirket e-postası "
                "yöneticinizden) öğrenin. Uygulama şifresi gerekip gerekmediği de sağlayıcıya bağlı."
            )
            return
        preset = self._mail_presets.get(provider)
        self.mail_provider_hint_label.setText(preset.password_hint_tr if preset else "")

    def _update_custom_fields_visible(self) -> None:
        visible = self.mail_provider_combo.currentData() == PROVIDER_CUSTOM
        for widget in (*self._custom_field_widgets, *self._custom_field_labels):
            if widget is not None:
                widget.setVisible(visible)

    def refresh_password_status(self) -> None:
        self.password_status_label.setText(format_secret_status(bool(get_mail_password())))

    def save_password(self) -> None:
        if prompt_save_mail_password(self):
            self.password_status_label.setText(format_secret_status(True))

    def delete_password(self) -> None:
        delete_mail_password_with_notice(self, self.password_status_label)

    def values(self) -> dict[str, Any]:
        return {
            "sender_email": self.sender_email_edit.text().strip() or None,
            "sender_name": self.sender_name_edit.text().strip() or None,
            "mail_provider": self.mail_provider_combo.currentData(),
            "custom_smtp_host": self.custom_smtp_host_edit.text().strip() or None,
            "custom_smtp_port": self.custom_smtp_port_spin.value() or None,
            "custom_smtp_security": self.custom_smtp_security_combo.currentData(),
            "custom_imap_host": self.custom_imap_host_edit.text().strip() or None,
            "custom_imap_port": self.custom_imap_port_spin.value() or None,
            "inbox_check_minutes": self.inbox_check_minutes_spin.value(),
        }

    def set_values(self, form: SettingsForm) -> None:
        self.sender_email_edit.setText(form.sender_email or "")
        self.sender_name_edit.setText(form.sender_name or "")

        select_index_by_data(self.mail_provider_combo, form.mail_provider)
        self.custom_smtp_host_edit.setText(form.custom_smtp_host or "")
        self.custom_smtp_port_spin.setValue(form.custom_smtp_port or 0)
        select_index_by_data(self.custom_smtp_security_combo, form.custom_smtp_security)
        self.custom_imap_host_edit.setText(form.custom_imap_host or "")
        self.custom_imap_port_spin.setValue(form.custom_imap_port or 0)
        self._update_mail_provider_hint()
        self._update_custom_fields_visible()

        self.inbox_check_minutes_spin.setValue(form.inbox_check_minutes)


def show_smtp_test_result(parent: QWidget, result: SmtpLoginTestJobResult) -> None:
    """\"Bağlantıyı test et\" işinin sonucunu kullanıcıya gösterir."""
    if result.no_account:
        QMessageBox.warning(
            parent,
            "Uygulama şifresi kayıtlı değil",
            "Mail uygulama şifresi kayıtlı değil. Önce şifreyi kaydedin.",
        )
        return
    if result.error and result.email == "" and result.imap_error is None:
        # Profil okunamadığı için hesaba hiç bakılmadı -- SMTP/IMAP ayrımı yok, tek hata.
        QMessageBox.warning(parent, "Bağlantı testi başarısız", result.error)
        return

    smtp_line = (
        "SMTP (gönderim): bağlantı başarılı, mail gönderilmedi."
        if result.ok
        else f"SMTP (gönderim): başarısız — {result.error}"
    )
    imap_line = (
        "IMAP (gelen kutusu, salt okuma): bağlantı başarılı."
        if result.imap_ok
        else f"IMAP (gelen kutusu, salt okuma): başarısız — {result.imap_error}"
    )
    message = f"{result.email}\n\n{smtp_line}\n{imap_line}"

    if result.ok and result.imap_ok:
        QMessageBox.information(parent, "Bağlantı testi başarılı", message)
    else:
        QMessageBox.warning(parent, "Bağlantı testi", message)
