"""Sihirbazın mail hesabı sayfası (PLAN.md Adım 10.2i).

Ayarlar'ın "Mail hesabı" grubu (`settings_groups.mail_account_group.MailAccountGroup`) AYNEN kullanılır:
gönderen, sağlayıcı (+ "Özel" sunucu alanları), uygulama şifresi (Windows şifre kasasına, ekranda tutulmaz),
"Bağlantıyı test et". Sayfanın eklediği iki şey:
- Seçili sağlayıcı için adım adım uygulama şifresi rehberi (`presets/mail_providers.json` içindeki
  `password_guide_tr`; yoksa `password_hint_tr`). Menü adları zamanla değişir, metin ana kaynaktır.
- "İleri" yalnızca şifre kayıtlıysa VE o ekran durumuyla "Bağlantıyı test et" başarılı olduysa geçer
  (SMTP ve IMAP ikisi de; gelen cevapların okunması uygulamanın çekirdeği). Alanlar ya da şifre değişirse
  eski başarı geçersiz sayılır (`onboarding_data.mail_test_signature`). Test arka planda çalışır (kural 8),
  mail GÖNDERMEZ.
"""
import logging
from functools import partial
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QLabel, QScrollArea, QVBoxLayout, QWidget
from sqlalchemy.engine import Engine
from sqlmodel import Session

from src.core.db import get_session
from src.core.mail_providers import MailProviderPreset, load_mail_provider_presets
from src.core.secrets import get_mail_password
from src.sender.mail_connection import PROVIDER_CUSTOM
from src.ui.jobs import SmtpLoginTestJobResult, smtp_login_test_job
from src.ui.onboarding_data import (
    PAGE_MAIL,
    load_existing_profile,
    mail_test_signature,
    save_settings_fields,
    validate_mail_account,
)
from src.ui.onboarding_pages.base import OnboardingPage
from src.ui.settings_data import read_form
from src.ui.settings_groups.common import hint_label
from src.ui.settings_groups.mail_account_group import MailAccountGroup, show_smtp_test_result
from src.ui.wheel_guard import install_wheel_guard

logger = logging.getLogger(__name__)

TITLE = "Mail hesabınız"
INTRO_TEXT = (
    "Başvurular bu hesaptan gönderilir ve cevaplar bu hesabın gelen kutusundan okunur. Normal mail "
    "şifreniz DEĞİL, sağlayıcınızdan alacağınız bir \"uygulama şifresi\" kullanılır (yalnızca bu "
    "uygulamaya özel, istediğiniz an iptal edebileceğiniz bir şifre). Şifre yalnızca Windows şifre "
    "kasasında saklanır."
)
GUIDE_TITLE = "Uygulama şifresi nasıl alınır?"
GUIDE_DISCLAIMER = (
    "Sağlayıcıların ekranları ve menü adları zamanla değişebilir; adımlar yol göstermek içindir."
)
CUSTOM_GUIDE = (
    "Sunucunuzun SMTP/IMAP adreslerini ve uygulama şifresi gerekip gerekmediğini mail "
    "sağlayıcınızdan ya da e-posta yöneticinizden öğrenin."
)
NO_GUIDE_TEXT = "Bu sağlayıcı için ayrıntılı rehber yok."
TEST_JOB_TITLE = "Mail bağlantısı test ediliyor"


def guide_text(preset: Optional[MailProviderPreset]) -> str:
    """Sağlayıcının numaralı rehber metni; rehber yoksa kısa ipucu, o da yoksa genel not."""
    if preset is None:
        return NO_GUIDE_TEXT
    if preset.password_guide_tr:
        return "\n".join(f"{number}. {step}" for number, step in enumerate(preset.password_guide_tr, 1))
    return preset.password_hint_tr or NO_GUIDE_TEXT


class MailPage(OnboardingPage):
    page_id = PAGE_MAIL

    def __init__(
        self, engine: Engine, profile_path: Path, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(engine, profile_path, parent)
        self.setTitle(TITLE)
        self._mail_presets: dict[str, MailProviderPreset] = load_mail_provider_presets()
        #: Son BAŞARILI testin imzası (`mail_test_signature`); `None` = bu oturumda başarılı test yok.
        self._tested_signature: Optional[tuple] = None

        intro = QLabel(INTRO_TEXT, self)
        intro.setWordWrap(True)

        self.mail_group = MailAccountGroup(self._mail_presets, self)
        self.mail_group.mail_provider_combo.currentIndexChanged.connect(self._refresh_guide)
        self.mail_group.test_requested.connect(self._on_test_requested)

        self.guide_label = QLabel(self)
        self.guide_label.setWordWrap(True)
        self.guide_label.setTextInteractionFlags(Qt.TextSelectableByMouse)  # adresler kopyalanabilsin
        guide_layout = QVBoxLayout()
        guide_layout.addWidget(self.guide_label)
        guide_layout.addWidget(hint_label(GUIDE_DISCLAIMER, self))
        self.guide_box = QGroupBox(GUIDE_TITLE, self)
        self.guide_box.setLayout(guide_layout)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(intro)
        content_layout.addWidget(self.guide_box)
        content_layout.addWidget(self.mail_group)
        content_layout.addStretch(1)
        install_wheel_guard(content)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    # -- rehber ---------------------------------------------------------------------------

    def _refresh_guide(self, *_args) -> None:
        provider = self.mail_group.mail_provider_combo.currentData()
        if provider == PROVIDER_CUSTOM:
            self.guide_label.setText(CUSTOM_GUIDE)
        else:
            self.guide_label.setText(guide_text(self._mail_presets.get(provider)))

    # -- bağlantı testi -------------------------------------------------------------------

    def _current_signature(self) -> tuple:
        return mail_test_signature(self.mail_group.values(), bool(get_mail_password()))

    def connection_tested_ok(self) -> bool:
        return self._tested_signature is not None and self._tested_signature == self._current_signature()

    def _on_test_requested(self) -> None:
        if self.guard_running():
            return
        values = self.mail_group.values()
        signature = mail_test_signature(values, bool(get_mail_password()))
        self.start_job(
            TEST_JOB_TITLE,
            partial(
                smtp_login_test_job,
                self._profile_path,
                sender_email=values["sender_email"],
                sender_name=values["sender_name"],
                provider=values["mail_provider"],
                custom_smtp_host=values["custom_smtp_host"],
                custom_smtp_port=values["custom_smtp_port"],
                custom_smtp_security=values["custom_smtp_security"],
                custom_imap_host=values["custom_imap_host"],
                custom_imap_port=values["custom_imap_port"],
                presets=self._mail_presets,
            ),
            partial(self._on_test_done, signature),
        )

    def _on_test_done(self, signature: tuple, result: SmtpLoginTestJobResult) -> None:
        self._tested_signature = signature if result.ok and result.imap_ok else None
        show_smtp_test_result(self, result)

    # -- OnboardingPage -------------------------------------------------------------------

    def load(self) -> None:
        with get_session(self._engine) as session:
            form = read_form(session)
        self.mail_group.set_values(form)
        self.mail_group.refresh_password_status()
        if not self.mail_group.sender_email_edit.text().strip():
            self.mail_group.sender_email_edit.setText(self._profile_email())
        self._refresh_guide()

    def _profile_email(self) -> str:
        """Kişisel bilgiler sayfasında girilen e-posta; profil yok/bozuksa boş (sayfa engellemez)."""
        try:
            profile = load_existing_profile(self._profile_path)
        except ValueError:
            logger.exception("Sihirbaz profil dosyasını okuyamadı (gönderen önerisi atlandı)")
            return ""
        return profile.personal_info.email if profile is not None else ""

    def validation_errors(self) -> list[str]:
        return validate_mail_account(
            self.mail_group.values(), bool(get_mail_password()), self.connection_tested_ok()
        )

    def save(self, session: Session) -> None:
        values: dict[str, Any] = self.mail_group.values()
        save_settings_fields(session, values)
