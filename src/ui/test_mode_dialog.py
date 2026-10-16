"""Test modu geçiş penceresi: "Gerçek gönderime geç" / "Test moduna dön" (ince Qt kabuğu).

Kural `src/ui/test_mode_data.py`'de (Qt YOK). Burada yalnızca: KVKK onayı yoksa bilgi kutusu, varsa
"Anladım" kutulu onay penceresi ("Başlat" düğmesi kutu işaretlenmeden kapalıdır) ve ayarın yazılması.
Ayarlar sekmesi ve sihirbaz bitiş sayfası AYNI `switch_test_mode()`'u kullanır.
"""
import logging
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.engine import Engine

from src.core.db import get_session
from src.ui.test_mode_data import (
    GO_LIVE_ACK_TEXT,
    GO_LIVE_CANCEL_BUTTON_TEXT,
    GO_LIVE_CONFIRM_BUTTON_TEXT,
    GO_LIVE_DIALOG_TEXT,
    GO_LIVE_DIALOG_TITLE,
    NO_CONSENT_TEXT,
    NO_CONSENT_TITLE,
    GoLiveRefused,
    enable_real_sending,
    go_live_blocker,
    return_to_test_mode,
)

logger = logging.getLogger(__name__)


class GoLiveDialog(QDialog):
    """Sade Türkçe uyarı; "Anladım" işaretlenmeden "Başlat" düğmesi kapalıdır."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(GO_LIVE_DIALOG_TITLE)
        self.setModal(True)

        text_label = QLabel(GO_LIVE_DIALOG_TEXT, self)
        text_label.setWordWrap(True)

        self.ack_checkbox = QCheckBox(GO_LIVE_ACK_TEXT, self)
        self.ack_checkbox.toggled.connect(self._on_ack_toggled)

        self.confirm_button = QPushButton(GO_LIVE_CONFIRM_BUTTON_TEXT, self)
        self.confirm_button.setEnabled(False)
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.clicked.connect(self.accept)

        self.cancel_button = QPushButton(GO_LIVE_CANCEL_BUTTON_TEXT, self)
        self.cancel_button.setDefault(True)  # Enter'a yanlışlıkla basılırsa güvenli seçenek
        self.cancel_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.confirm_button)

        layout = QVBoxLayout(self)
        layout.addWidget(text_label)
        layout.addWidget(self.ack_checkbox)
        layout.addLayout(buttons)

    def _on_ack_toggled(self, checked: bool) -> None:
        self.confirm_button.setEnabled(checked)


def ask_go_live(parent: QWidget, engine: Engine) -> bool:
    """KVKK onayını denetler, kullanıcıya sorar, onaylarsa gerçek gönderimi açar. Açıldı mı döner."""
    with get_session(engine) as session:
        blocker = go_live_blocker(session)
    if blocker is not None:
        QMessageBox.information(parent, NO_CONSENT_TITLE, blocker)
        return False

    if GoLiveDialog(parent).exec() != QDialog.Accepted:
        return False

    try:
        with get_session(engine) as session:
            enable_real_sending(session)
            session.commit()
    except GoLiveRefused as refusal:  # pencere açıkken durum değişmiş olamaz ama kapı yine denetlenir
        QMessageBox.information(parent, NO_CONSENT_TITLE, str(refusal))
        return False
    return True


def switch_test_mode(parent: QWidget, engine: Engine, want_test_mode: bool) -> bool:
    """Test modunu istenen duruma getirir; ayar DEĞİŞTİ mi döner (iptal/engel durumunda False).

    Test moduna dönüş anında ve onaysızdır; gerçek gönderime geçiş `ask_go_live` kapısından geçer.
    """
    if want_test_mode:
        with get_session(engine) as session:
            return_to_test_mode(session)
            session.commit()
        return True
    return ask_go_live(parent, engine)
