"""src/ui/status_strip.py testleri: hem Qt'siz saf fonksiyonlar hem StatusStrip widget'ı."""
from PySide6.QtWidgets import QMessageBox

from src.core.settings import (
    KEY_SEND_MODE,
    KEY_SENDING_PAUSED,
    KEY_TEST_MODE,
    MODE_AUTO,
    get_setting,
    set_setting,
)
from src.ui.status_strip import (
    PAUSED_OFF_TEXT,
    PAUSED_ON_TEXT,
    STOP_BUTTON_ACTIVATE_TEXT,
    STOP_BUTTON_DEACTIVATE_TEXT,
    TEST_MODE_OFF_STYLE,
    TEST_MODE_OFF_TEXT,
    TEST_MODE_ON_STYLE,
    TEST_MODE_ON_TEXT,
    StatusInfo,
    StatusStrip,
    format_paused_text,
    format_send_mode_text,
    format_stop_button_style,
    format_stop_button_text,
    format_test_mode_style,
    format_test_mode_text,
    read_status,
)


def test_read_status_returns_defaults_on_empty_db(session):
    info = read_status(session)
    assert info == StatusInfo(test_mode=True, send_mode="manual", sending_paused=False)


def test_test_mode_text_and_style_differ_between_on_and_off():
    on = StatusInfo(test_mode=True, send_mode="manual", sending_paused=False)
    off = StatusInfo(test_mode=False, send_mode="manual", sending_paused=False)

    assert format_test_mode_text(on) == TEST_MODE_ON_TEXT
    assert format_test_mode_text(off) == TEST_MODE_OFF_TEXT == "GERÇEK GÖNDERİM AÇIK"
    assert format_test_mode_style(on) == TEST_MODE_ON_STYLE
    assert format_test_mode_style(off) == TEST_MODE_OFF_STYLE
    assert format_test_mode_style(on) != format_test_mode_style(off)


def test_send_mode_text_uses_turkish_label():
    info = StatusInfo(test_mode=True, send_mode=MODE_AUTO, sending_paused=False)
    assert format_send_mode_text(info) == "Gönderim modu: otomatik"


def test_paused_text_differs_between_on_and_off():
    paused = StatusInfo(test_mode=True, send_mode="manual", sending_paused=True)
    not_paused = StatusInfo(test_mode=True, send_mode="manual", sending_paused=False)

    assert format_paused_text(paused) == PAUSED_ON_TEXT
    assert format_paused_text(not_paused) == PAUSED_OFF_TEXT


def test_strip_shows_test_mode_on_by_default(qtbot, engine):
    strip = StatusStrip(engine)
    qtbot.addWidget(strip)

    assert strip._test_mode_label.text() == TEST_MODE_ON_TEXT


def test_strip_refresh_reflects_test_mode_turned_off(qtbot, engine, session):
    strip = StatusStrip(engine)
    qtbot.addWidget(strip)

    set_setting(session, KEY_TEST_MODE, False)
    session.commit()
    strip.refresh()

    assert strip._test_mode_label.text() == "GERÇEK GÖNDERİM AÇIK"


def test_strip_refresh_reflects_sending_paused(qtbot, engine, session):
    strip = StatusStrip(engine)
    qtbot.addWidget(strip)

    set_setting(session, KEY_SENDING_PAUSED, True)
    session.commit()
    strip.refresh()

    assert strip._paused_label.text() == PAUSED_ON_TEXT


def test_strip_refresh_reflects_send_mode_change(qtbot, engine, session):
    strip = StatusStrip(engine)
    qtbot.addWidget(strip)

    set_setting(session, KEY_SEND_MODE, MODE_AUTO)
    session.commit()
    strip.refresh()

    assert strip._send_mode_label.text() == "Gönderim modu: otomatik"


# ---------------------------------------------------------------------------
# ACİL DURDUR butonu (5.2b)
# ---------------------------------------------------------------------------


def test_stop_button_text_and_style_differ_between_on_and_off():
    off = StatusInfo(test_mode=True, send_mode="manual", sending_paused=False)
    on = StatusInfo(test_mode=True, send_mode="manual", sending_paused=True)

    assert format_stop_button_text(off) == STOP_BUTTON_ACTIVATE_TEXT
    assert format_stop_button_text(on) == STOP_BUTTON_DEACTIVATE_TEXT
    assert format_stop_button_style(off) != format_stop_button_style(on)


def test_strip_shows_activate_stop_button_by_default(qtbot, engine):
    strip = StatusStrip(engine)
    qtbot.addWidget(strip)

    assert strip._stop_button.text() == STOP_BUTTON_ACTIVATE_TEXT


def test_stop_button_click_activates_without_confirmation(qtbot, engine, session):
    """Açma bir güvenlik eylemi -- onay İSTEMEZ, anında yazılır ve sinyal yayılır."""
    strip = StatusStrip(engine)
    qtbot.addWidget(strip)

    with qtbot.waitSignal(strip.emergency_stop_activated, timeout=1000):
        strip._stop_button.click()

    assert get_setting(session, KEY_SENDING_PAUSED) is True
    assert strip._stop_button.text() == STOP_BUTTON_DEACTIVATE_TEXT


def test_stop_button_click_deactivate_respects_no_confirmation(qtbot, engine, session, monkeypatch):
    set_setting(session, KEY_SENDING_PAUSED, True)
    session.commit()
    strip = StatusStrip(engine)
    qtbot.addWidget(strip)

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.No)
    strip._stop_button.click()

    assert get_setting(session, KEY_SENDING_PAUSED) is True  # değişmedi
    assert strip._stop_button.text() == STOP_BUTTON_DEACTIVATE_TEXT


def test_stop_button_click_deactivate_confirmed_clears_pause(qtbot, engine, session, monkeypatch):
    set_setting(session, KEY_SENDING_PAUSED, True)
    session.commit()
    strip = StatusStrip(engine)
    qtbot.addWidget(strip)

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    strip._stop_button.click()

    assert get_setting(session, KEY_SENDING_PAUSED) is False
    assert strip._stop_button.text() == STOP_BUTTON_ACTIVATE_TEXT
