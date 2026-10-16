"""src/ui/theme.py ve Ayarlar'daki "Görünüm" grubu testleri."""
import pytest
from PySide6.QtWidgets import QApplication

from src.core.db import get_session
from src.core.settings import KEY_UI_THEME, get_setting
from src.ui.settings_groups.appearance_group import AppearanceGroup
from src.ui.theme import THEME_DARK, THEME_LIGHT, apply_theme, normalize_theme


@pytest.fixture(autouse=True)
def _reset_stylesheet(qtbot):
    yield
    QApplication.instance().setStyleSheet("")


def test_normalize_theme_falls_back_to_dark():
    assert normalize_theme("light") == THEME_LIGHT
    assert normalize_theme("dark") == THEME_DARK
    assert normalize_theme("neon") == THEME_DARK
    assert normalize_theme(None) == THEME_DARK


@pytest.mark.parametrize("name", [THEME_LIGHT, THEME_DARK])
def test_apply_theme_sets_a_stylesheet(qtbot, name):
    assert apply_theme(QApplication.instance(), name) == name
    assert QApplication.instance().styleSheet()


def test_apply_theme_survives_qt_material_failure(qtbot, monkeypatch):
    import qt_material

    def boom(*args, **kwargs):
        raise RuntimeError("yok")

    monkeypatch.setattr(qt_material, "apply_stylesheet", boom)
    assert apply_theme(QApplication.instance(), THEME_DARK) == THEME_DARK


def test_unsaved_setting_defaults_to_dark(qtbot, engine):
    with get_session(engine) as session:
        assert get_setting(session, KEY_UI_THEME) == THEME_DARK
    group = AppearanceGroup(engine)
    qtbot.addWidget(group)
    assert group.theme_combo.currentData() == THEME_DARK


def test_user_choice_of_light_is_kept(qtbot, engine):
    group = AppearanceGroup(engine)
    qtbot.addWidget(group)

    group.theme_combo.setCurrentIndex(group.theme_combo.findData(THEME_LIGHT))

    with get_session(engine) as session:
        assert get_setting(session, KEY_UI_THEME) == THEME_LIGHT
    assert QApplication.instance().styleSheet()
    reopened = AppearanceGroup(engine)
    qtbot.addWidget(reopened)
    assert reopened.theme_combo.currentData() == THEME_LIGHT
