"""Pencerenin en üstünde her zaman görünen durum şeridi: test modu, gönderim modu, acil durdur.

Ayarlar veritabanından okunur (`src/core/settings.py`). Şerit birkaç saniyede bir kendi kısa
ömürlü oturumunu açıp ayarları tazeler — çünkü `sending_paused` başka bir pencereden
(`scripts/set_setting.py`) ya da gönderim işçisinin kendisi tarafından (art arda 3 hatadan
sonra, bkz. `src/sender/worker.py`) değiştirilebilir; kullanıcının tıklamasını beklemek yetmez.

`rate_limiter.can_send_now().reason` burada KULLANILMAZ: o metin bir CLI komutu içeriyor
("python scripts/set_setting.py sending_paused false"), arayüzde anlamsız kalır. Şerit kendi
Türkçe metnini yazar.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget
from sqlalchemy.engine import Engine
from sqlmodel import Session

from src.core.db import get_session
from src.core.settings import (
    KEY_PAUSE_REASON,
    KEY_SEND_MODE,
    KEY_SENDING_PAUSED,
    KEY_TEST_MODE,
    SEND_MODE_LABELS_TR,
    get_setting,
    set_setting,
)

logger = logging.getLogger(__name__)

#: Ayarların ne sıklıkla yeniden okunacağı (milisaniye).
REFRESH_INTERVAL_MS = 3000

TEST_MODE_ON_TEXT = "TEST MODU — mailler sadece size gidiyor"
TEST_MODE_OFF_TEXT = "GERÇEK GÖNDERİM AÇIK"
TEST_MODE_ON_STYLE = "background-color: #F9C846; color: #000000; padding: 6px; font-weight: bold;"
TEST_MODE_OFF_STYLE = "background-color: #C62828; color: #FFFFFF; padding: 6px; font-weight: bold;"

PAUSED_ON_TEXT = "ACİL DURDUR AÇIK — hiçbir mail gönderilmiyor"
PAUSED_OFF_TEXT = "Acil durdur: kapalı"
PAUSED_ON_STYLE = "color: #C62828; font-weight: bold;"
PAUSED_OFF_STYLE = ""

STOP_BUTTON_ACTIVATE_TEXT = "ACİL DURDUR"
STOP_BUTTON_DEACTIVATE_TEXT = "Acil durdurmayı kaldır"
#: Kapalıyken (tıklanınca açacak) kırmızı -- dikkat çeksin; açıkken (tıklanınca kaldıracak)
#: nötr -- zaten kırmızı `_paused_label` yeterince görünür durumda.
STOP_BUTTON_ACTIVATE_STYLE = "background-color: #C62828; color: #FFFFFF; font-weight: bold; padding: 4px 10px;"
STOP_BUTTON_DEACTIVATE_STYLE = "padding: 4px 10px;"
STOP_BUTTON_CONFIRM_TEXT = (
    "Acil durdurmayı kaldırmak istediğinize emin misiniz? Kaldırılırsa gönderim (manuel/"
    "otomatik) tekrar mümkün olur."
)


@dataclass(frozen=True)
class StatusInfo:
    """Durum şeridinin göstereceği üç ayarın anlık kopyası."""

    test_mode: bool
    send_mode: str
    sending_paused: bool


def read_status(session: Session) -> StatusInfo:
    """Üç ayarı veritabanından okur."""
    return StatusInfo(
        test_mode=bool(get_setting(session, KEY_TEST_MODE)),
        send_mode=get_setting(session, KEY_SEND_MODE),
        sending_paused=bool(get_setting(session, KEY_SENDING_PAUSED)),
    )


# Not: bu yardımcıların adı bilinçli olarak "test_" ile BAŞLAMIYOR (format_test_mode_text,
# test_mode_text değil) -- aksi halde başka bir dosyaya `from ... import test_mode_text`
# ile aktarılınca pytest onu bir test fonksiyonu sanıp topluyor ("fixture 'info' not found").


def format_test_mode_text(info: StatusInfo) -> str:
    return TEST_MODE_ON_TEXT if info.test_mode else TEST_MODE_OFF_TEXT


def format_test_mode_style(info: StatusInfo) -> str:
    return TEST_MODE_ON_STYLE if info.test_mode else TEST_MODE_OFF_STYLE


def format_send_mode_text(info: StatusInfo) -> str:
    label = SEND_MODE_LABELS_TR.get(info.send_mode, info.send_mode)
    return f"Gönderim modu: {label}"


def format_paused_text(info: StatusInfo) -> str:
    return PAUSED_ON_TEXT if info.sending_paused else PAUSED_OFF_TEXT


def format_paused_style(info: StatusInfo) -> str:
    return PAUSED_ON_STYLE if info.sending_paused else PAUSED_OFF_STYLE


def format_stop_button_text(info: StatusInfo) -> str:
    return STOP_BUTTON_DEACTIVATE_TEXT if info.sending_paused else STOP_BUTTON_ACTIVATE_TEXT


def format_stop_button_style(info: StatusInfo) -> str:
    return STOP_BUTTON_DEACTIVATE_STYLE if info.sending_paused else STOP_BUTTON_ACTIVATE_STYLE


class StatusStrip(QWidget):
    """Pencerenin en üstünde, her zaman görünür durum şeridi.

    `emergency_stop_activated` sinyali, yalnızca `sending_paused=True` başarıyla YAZILDIKTAN
    sonra yayılır -- `MainWindow` bunu dinleyip çalışan bir gönderim turu varsa durdurur
    (`src/ui/queue_tab.py::on_emergency_stop`); şerit kuyruk sekmesini hiç tanımaz.
    """

    emergency_stop_activated = Signal()

    def __init__(
        self,
        engine: Engine,
        parent: Optional[QWidget] = None,
        *,
        interval_ms: int = REFRESH_INTERVAL_MS,
    ) -> None:
        super().__init__(parent)
        self._engine = engine

        self._test_mode_label = QLabel(self)
        self._send_mode_label = QLabel(self)
        self._paused_label = QLabel(self)
        self._stop_button = QPushButton(self)
        self._stop_button.clicked.connect(self._on_stop_button_clicked)

        layout = QHBoxLayout(self)
        layout.addWidget(self._test_mode_label, 1)
        layout.addWidget(self._send_mode_label)
        layout.addWidget(self._paused_label)
        layout.addWidget(self._stop_button)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(interval_ms)

        self.refresh()

    def refresh(self) -> None:
        """Kendi kısa ömürlü oturumunu açar, üç ayarı okur, etiketleri/butonu günceller.

        Buton metni de burada güncellenir: worker'ın kendi duraklatması (art arda 3 hata,
        `src/sender/worker.py`) ya da başka bir terminalden yapılan değişiklik de böylece
        birkaç saniye içinde butona yansır.
        """
        try:
            with get_session(self._engine) as session:
                info = read_status(session)
        except Exception:
            logger.warning("Durum şeridi ayarları okunamadı", exc_info=True)
            return

        self._test_mode_label.setText(format_test_mode_text(info))
        self._test_mode_label.setStyleSheet(format_test_mode_style(info))
        self._send_mode_label.setText(format_send_mode_text(info))
        self._paused_label.setText(format_paused_text(info))
        self._paused_label.setStyleSheet(format_paused_style(info))
        self._stop_button.setText(format_stop_button_text(info))
        self._stop_button.setStyleSheet(format_stop_button_style(info))

    def _on_stop_button_clicked(self) -> None:
        try:
            with get_session(self._engine) as session:
                info = read_status(session)
        except Exception:
            logger.warning("Durum şeridi ayarları okunamadı", exc_info=True)
            return

        if info.sending_paused:
            # Kaldırma onay ister (varsayılan Hayır): gönderim tekrar mümkün olacak.
            answer = QMessageBox.question(
                self,
                "Acil durdurmayı kaldır",
                STOP_BUTTON_CONFIRM_TEXT,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            new_value = False
        else:
            # Açma onay İSTEMEZ: bu bir güvenlik butonu, anında çalışmalı.
            new_value = True

        with get_session(self._engine) as session:
            set_setting(session, KEY_SENDING_PAUSED, new_value)
            # `pause_reason` kimin durdurduğunu ayırt eder (bkz. `src/sender/safety_brake.py`):
            # manuel buton her zaman kazanır (bu dal yalnızca ŞU AN duraklatılmamışken erişilir,
            # bkz. yukarıdaki if/else), kaldırırken de sebep temizlenir.
            set_setting(session, KEY_PAUSE_REASON, "kullanıcı ACİL DURDUR butonuna bastı" if new_value else None)
            session.commit()

        self.refresh()
        if new_value:
            self.emergency_stop_activated.emit()
