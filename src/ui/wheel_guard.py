"""Fare tekerleği koruyucusu: spinbox/combobox yalnızca ODAKTAYKEN tekerleği kabul eder.

Sorun: kaydırılabilir bir sayfada (Ayarlar) imleç bir `QSpinBox`/`QComboBox` üzerinden geçerken
tekerlek alanın DEĞERİNİ değiştirir (aylık sınır 900 → 898 oldu). Çözüm: bu alanlara tıklanmadıkça
tekerlek olayı alan tarafından yutulmaz, `ignore()` edilip üst widget'a (kaydırma alanına)
iletilir; sayfa kaydırılmaya devam eder.

İki ayrıntı önemli:
  * Qt'nin bu widget'lardaki varsayılan odak politikası `WheelFocus`'tur — tekerlek alana odak
    verir ve ardından değeri değiştirir. Bu yüzden politika `StrongFocus` (yalnızca tıklama/Tab)
    yapılır.
  * Düzenlenebilir alanlarda odak alanın kendisinde değil içindeki `QLineEdit`'tedir; bu yüzden
    "odakta mı" kontrolü alt widget'ları da sayar.
"""
from typing import Optional

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QWidget,
)


def _has_focus_inside(widget: QWidget) -> bool:
    focused = QApplication.focusWidget()
    return focused is not None and (focused is widget or widget.isAncestorOf(focused))


def _enclosing_scroll_area(widget: QWidget) -> Optional[QAbstractScrollArea]:
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


class _WheelGuard(QObject):
    """Odakta olmayan alanın tekerlek olayını yutar ve en yakın kaydırma alanına iletir.

    Olay Qt'nin kendi "ignore ile üst widget'a yay" mekanizmasına BIRAKILMAZ: o yayılım yalnızca
    işletim sisteminden gelen (spontaneous) olaylarda çalışır, kodla gönderilen olayda (testler)
    çalışmaz. Kaydırma alanının viewport'una açıkça iletmek iki durumda da aynı sonucu verir.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            event.type() != QEvent.Type.Wheel
            or not isinstance(watched, QWidget)
            or _has_focus_inside(watched)
        ):
            return False
        scroll_area = _enclosing_scroll_area(watched)
        if scroll_area is None:
            event.ignore()  # kaydırma alanı yok: olağan yayılım üst widget'a taşısın
            return True
        viewport = scroll_area.viewport()
        assert isinstance(event, QWheelEvent)
        forwarded = QWheelEvent(
            viewport.mapFromGlobal(event.globalPosition()),
            event.globalPosition(),
            event.pixelDelta(),
            event.angleDelta(),
            event.buttons(),
            event.modifiers(),
            event.phase(),
            event.inverted(),
            event.source(),
        )
        QApplication.sendEvent(viewport, forwarded)
        event.accept()  # alan olayı görmesin (değer değişmez) ve başka yere de yayılmasın
        return True


def install_wheel_guard(root: QWidget) -> None:
    """`root` altındaki tüm spinbox (QTimeEdit dahil) ve combobox'lara koruyucuyu takar.

    Alanlar oluşturulduktan SONRA bir kez çağrılır; sonradan eklenen alanlar için yeniden çağrılabilir.
    """
    guard = _WheelGuard(root)
    for widget_type in (QAbstractSpinBox, QComboBox):
        for widget in root.findChildren(widget_type):
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            widget.installEventFilter(guard)
