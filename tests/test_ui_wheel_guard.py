"""src/ui/wheel_guard.py testleri: tekerlek yalnızca odaktaki spinbox/combobox'ı değiştirir,
aksi halde sayfa kaydırılır (qtbot, gerçek olay gönderimiyle)."""
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from src.core import secrets
from src.ui.settings_tab import SettingsTab
from src.ui.wheel_guard import install_wheel_guard

from tests.test_ui_settings_tab import _FakeKeyring


def _wheel(widget: QWidget, delta: int = -120) -> None:
    """Gerçek bir tekerlek olayını `widget`'a gönderir (Qt olay yayılımı dahil)."""
    position = QPointF(widget.width() / 2, widget.height() / 2)
    event = QWheelEvent(
        position,
        widget.mapToGlobal(position),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)


@pytest.fixture
def page(qtbot):
    """Kaydırma alanı içinde bir spinbox, combobox ve QTimeEdit; içerik alandan uzun."""
    content = QWidget()
    layout = QVBoxLayout(content)
    anchor = QPushButton("odak")  # pencere açılınca odağı bu alsın, test alanları değil
    layout.addWidget(anchor)
    spin = QSpinBox()
    spin.setRange(0, 2000)
    spin.setValue(900)
    combo = QComboBox()
    combo.addItems(["a", "b", "c"])
    time_edit = QTimeEdit()
    layout.addWidget(spin)
    layout.addWidget(combo)
    layout.addWidget(time_edit)
    layout.addWidget(QLabel("x"))
    layout.addSpacing(2000)
    install_wheel_guard(content)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(content)
    scroll.resize(300, 200)
    qtbot.addWidget(scroll)
    scroll.show()
    qtbot.waitExposed(scroll)
    anchor.setFocus()
    qtbot.waitUntil(anchor.hasFocus)
    return scroll, spin, combo, time_edit


def test_wheel_over_unfocused_fields_scrolls_page_and_keeps_values(page):
    scroll, spin, combo, time_edit = page
    time_before = time_edit.time()

    for widget in (spin, combo, time_edit):
        _wheel(widget)

    assert spin.value() == 900
    assert combo.currentIndex() == 0
    assert time_edit.time() == time_before
    assert scroll.verticalScrollBar().value() > 0  # sayfa kaydı


def test_wheel_changes_value_when_field_has_focus(page, qtbot):
    _scroll, spin, _combo, _time = page
    spin.setFocus()
    qtbot.waitUntil(spin.hasFocus)

    _wheel(spin, delta=120)

    assert spin.value() == 901


def test_focused_combo_accepts_wheel(page, qtbot):
    _scroll, _spin, combo, _time = page
    combo.setFocus()
    qtbot.waitUntil(combo.hasFocus)

    _wheel(combo, delta=-120)

    assert combo.currentIndex() == 1


def test_wheel_does_not_give_focus(page):
    _scroll, spin, combo, time_edit = page
    for widget in (spin, combo, time_edit):
        assert widget.focusPolicy() == Qt.StrongFocus
        _wheel(widget)
        assert not widget.hasFocus()


def test_editable_spinbox_focus_inside_line_edit_counts_as_focused(page, qtbot):
    """Odak spinbox'ın içindeki QLineEdit'teyse (yazarken) tekerlek yine çalışır."""
    _scroll, spin, _combo, _time = page
    line_edit = spin.lineEdit()
    line_edit.setFocus()
    qtbot.waitUntil(line_edit.hasFocus)

    _wheel(spin, delta=120)

    assert spin.value() == 901


def test_settings_tab_every_spin_and_combo_is_guarded(qtbot, engine, tmp_path, monkeypatch):
    monkeypatch.setattr(secrets, "keyring", _FakeKeyring())
    tab = SettingsTab(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(tab)
    tab.resize(700, 400)
    tab.show()
    qtbot.waitExposed(tab)

    fields = tab.findChildren(QAbstractSpinBox) + tab.findChildren(QComboBox)
    assert len(fields) > 15  # ayar sekmesinde gerçekten çok sayıda alan var
    before = tab.form()

    for field in fields:
        assert field.focusPolicy() == Qt.StrongFocus
        _wheel(field)

    assert tab.form() == before  # hiçbir değer tekerlekle değişmedi
    assert not tab.is_dirty()
