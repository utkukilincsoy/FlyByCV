"""Sihirbazın zorunlu "kendinize test maili" sayfası (PLAN.md Adım 10.2l).

Gerçek gönderici hesabından kullanıcının KENDİ adresine, gerçek başvurudaki mail şablonu ve CV ekiyle bir
deneme maili yollar (`src/sender/self_test.py`). Veritabanına şirket/başvuru kaydı AÇMAZ, hiçbir firmaya
gitmez. "Mail geldi, doğru görünüyor" kutusu yalnızca bu kurulum durumuyla bir test maili gönderildikten
sonra işaretlenebilir ve işaretlenmeden "İleri" geçmez. Gönderen hesabı, CV modu, şablon, profil ya da kendi
PDF'i sonradan değişirse (`onboarding_data.self_test_signature`) gönderim geçersiz sayılır: kutu kapanır ve
yeniden gönderim istenir. Gönderim arka planda çalışır (kural 8); art arda basmaya karşı
`HARD_MIN_DELAY_SECONDS` kadar bekleme vardır.
"""
import logging
import time
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from src.core import paths
from src.core.db import get_session
from src.core.secrets import get_mail_password
from src.sender.rate_limiter import HARD_MIN_DELAY_SECONDS
from src.ui.jobs import SelfTestJobResult, self_test_job
from src.ui.onboarding_data import (
    PAGE_SELF_TEST,
    file_stamp,
    load_existing_profile,
    self_test_cooldown_remaining,
    self_test_signature,
    validate_self_test,
)
from src.ui.onboarding_pages.base import OnboardingPage
from src.ui.settings_data import read_form
from src.ui.settings_groups.common import hint_label
from src.ui.wheel_guard import install_wheel_guard

logger = logging.getLogger(__name__)

TITLE = "Kendinize test maili"
INTRO_TEXT = (
    "Şimdi gerçek bir başvurunun nasıl görüneceğini kendi adresinize göndererek deneyelim. Mail, bağladığınız "
    "hesaptan SİZİN adresinize gider; mail metni ve CV eki gerçek başvurudakiyle aynıdır, konunun başına "
    "[KURULUM TESTİ] eklenir. Hiçbir firmaya mail gitmez ve veritabanına firma/başvuru kaydı açılmaz."
)
RECIPIENT_TEXT = "Alıcı (kendi adresiniz): {email}"
NO_RECIPIENT_TEXT = "Alıcı adresi bulunamadı: Mail hesabı sayfasında gönderen adresini girin."
SEND_TEXT = "Test mailini gönder"
RESEND_TEXT = "Tekrar gönder"
JOB_TITLE = "Test maili gönderiliyor"
STATUS_IDLE = "Henüz test maili gönderilmedi."
STATUS_SENT = "Gönderildi: {email} (ek: {attachment}). Gelen kutunuzu açıp maili kontrol edin."
STATUS_FAILED = "Gönderilemedi: {reason}"
FAILURE_TITLE = "Test maili gönderilemedi"
COOLDOWN_TITLE = "Biraz bekleyin"
COOLDOWN_TEXT = "Art arda gönderimi önlemek için {seconds} saniye sonra tekrar deneyebilirsiniz."
CHECK_TEXT = "Mail geldi, doğru görünüyor"
CHECK_HINT = "Bu kutu, test maili gönderildikten sonra işaretlenebilir."
HELP_TITLE = "Mail gelmediyse"
HELP_TEXT = (
    "• Birkaç dakika bekleyin; ilk gönderimler geç ulaşabilir.\n"
    "• SPAM / GEREKSİZ klasörüne bakın. Orada bulursanız \"Spam değil\" diyerek gelen kutusuna taşıyın: "
    "firmalara giden mailler de aynı hesaptan gideceği için, ulaşması önemlidir.\n"
    "• Gönderen adresinin doğru ve gelen kutusuna erişebildiğiniz bir adres olduğundan emin olun "
    "(Mail hesabı sayfasına \"< Geri\" ile dönebilirsiniz).\n"
    "• Hâlâ yoksa \"Tekrar gönder\" ile yeniden deneyin. Mail ulaşmadan ilerlemeyin: gerçek başvurular da "
    "ulaşmayabilir."
)
CHECK_PROBLEM_HINT = (
    "Mail geldi ama ek (CV) açılmıyor ya da metin yanlış görünüyorsa kutuyu işaretlemeyin; "
    "önceki sayfalarda CV veya şablonu düzeltip tekrar gönderin."
)


class SelfTestPage(OnboardingPage):
    page_id = PAGE_SELF_TEST

    def __init__(self, engine: Engine, profile_path: Path, parent: Optional[QWidget] = None) -> None:
        super().__init__(engine, profile_path, parent)
        self.setTitle(TITLE)
        #: Son BAŞARILI gönderimin imzası (`self_test_signature`); `None` = bu sayfada başarılı gönderim yok.
        self._sent_signature: Optional[tuple] = None
        #: Son gönderim denemesinin zamanı (bekleme süresi için); testte `_clock` sahtelenir.
        self._last_attempt_at: Optional[float] = None
        self._clock: Callable[[], float] = time.monotonic

        intro = QLabel(INTRO_TEXT, self)
        intro.setWordWrap(True)

        self.recipient_label = QLabel(self)
        self.recipient_label.setWordWrap(True)
        self.send_button = QPushButton(SEND_TEXT, self)
        self.send_button.clicked.connect(self._on_send_clicked)
        self.status_label = QLabel(STATUS_IDLE, self)
        self.status_label.setWordWrap(True)

        help_layout = QVBoxLayout()
        help_label = QLabel(HELP_TEXT, self)
        help_label.setWordWrap(True)
        help_layout.addWidget(help_label)
        help_box = QGroupBox(HELP_TITLE, self)
        help_box.setLayout(help_layout)

        self.confirm_check = QCheckBox(CHECK_TEXT, self)
        self.confirm_check.setEnabled(False)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(intro)
        content_layout.addWidget(self.recipient_label)
        content_layout.addWidget(self.send_button)
        content_layout.addWidget(self.status_label)
        content_layout.addWidget(help_box)
        content_layout.addWidget(self.confirm_check)
        content_layout.addWidget(hint_label(CHECK_HINT, self))
        content_layout.addWidget(hint_label(CHECK_PROBLEM_HINT, self))
        content_layout.addStretch(1)
        install_wheel_guard(content)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    # -- durum ----------------------------------------------------------------------------

    def _current_signature(self) -> tuple:
        with get_session(self._engine) as session:
            form = read_form(session)
        return self_test_signature(
            asdict(form),
            bool(get_mail_password()),
            file_stamp(self._profile_path),
            file_stamp(paths.own_cv_path()),
        )

    def test_sent(self) -> bool:
        """Bu kurulum durumuyla başarılı bir test maili gönderildi mi?"""
        return self._sent_signature is not None and self._sent_signature == self._current_signature()

    def _recipient_email(self) -> str:
        """Test mailinin gideceği adres: kayıtlı gönderen, yoksa profildeki e-posta (`resolve_identity` ile aynı)."""
        with get_session(self._engine) as session:
            sender = read_form(session).sender_email
        if sender:
            return sender
        try:
            profile = load_existing_profile(self._profile_path)
        except ValueError:
            logger.exception("Sihirbaz profil dosyasını okuyamadı (test maili alıcısı bulunamadı)")
            return ""
        return profile.personal_info.email if profile is not None else ""

    def _reset_confirmation(self) -> None:
        self.confirm_check.setChecked(False)
        self.confirm_check.setEnabled(False)

    def _refresh_state(self) -> None:
        email = self._recipient_email()
        self.recipient_label.setText(RECIPIENT_TEXT.format(email=email) if email else NO_RECIPIENT_TEXT)
        if self.test_sent():
            self.confirm_check.setEnabled(True)
            self.send_button.setText(RESEND_TEXT)
            return
        # Gönderim hiç yapılmadı ya da kurulum o zamandan beri değişti: eski onay GEÇERSİZ.
        if self._sent_signature is not None:
            self.status_label.setText(
                "Ayarlar değişti; önceki test maili artık geçerli sayılmıyor. Lütfen tekrar gönderin."
            )
        self._sent_signature = None
        self._reset_confirmation()
        self.send_button.setText(SEND_TEXT)

    # -- gönderim -------------------------------------------------------------------------

    def _on_send_clicked(self) -> None:
        if self.guard_running():
            return
        remaining = self_test_cooldown_remaining(
            self._last_attempt_at, self._clock(), HARD_MIN_DELAY_SECONDS
        )
        if remaining > 0:
            QMessageBox.information(
                self, COOLDOWN_TITLE, COOLDOWN_TEXT.format(seconds=int(remaining) + 1)
            )
            return
        signature = self._current_signature()
        self._reset_confirmation()
        self.start_job(
            JOB_TITLE,
            partial(self_test_job, self._engine, self._profile_path),
            partial(self._on_send_done, signature),
        )

    def _on_send_done(self, signature: tuple, result: SelfTestJobResult) -> None:
        if result.sent:
            self._last_attempt_at = self._clock()
            self._sent_signature = signature
            self.status_label.setText(
                STATUS_SENT.format(email=result.recipient, attachment=result.attachment_name)
            )
            self._refresh_state()
            return
        if result.error:  # SMTP'ye çıkıldı; bekleme başlar (reddedilen denemeler bekletmez)
            self._last_attempt_at = self._clock()
        self._sent_signature = None
        reason = result.refused_reason or result.error or "bilinmeyen hata"
        self.status_label.setText(STATUS_FAILED.format(reason=reason))
        self._refresh_state()
        QMessageBox.warning(self, FAILURE_TITLE, reason)

    # -- OnboardingPage -------------------------------------------------------------------

    def load(self) -> None:
        self._refresh_state()

    def validation_errors(self) -> list[str]:
        return validate_self_test(self.test_sent(), self.confirm_check.isChecked())
