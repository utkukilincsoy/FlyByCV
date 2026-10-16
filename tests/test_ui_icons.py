"""src/ui/icons.py testleri: her mantıksal ad gerçek bir QtAwesome ikonuna çözülmeli."""
import pytest
from PySide6.QtWidgets import QPushButton

from src.ui import icons
from src.ui.icons import ICON_NAMES, get_icon, set_icon


@pytest.mark.parametrize("key", sorted(ICON_NAMES))
def test_every_icon_name_resolves(qtbot, key):
    assert not get_icon(key).isNull()


def test_set_icon_sets_button_icon(qtbot):
    button = QPushButton("Kaydet")
    qtbot.addWidget(button)
    set_icon(button, "save")
    assert not button.icon().isNull()


def test_get_icon_falls_back_to_empty_icon(qtbot, monkeypatch):
    monkeypatch.setitem(ICON_NAMES, "bozuk", "fa5s.yok-boyle-bir-ikon")
    assert get_icon("bozuk").isNull()
    assert icons.get_icon("olmayan-anahtar").isNull()
