"""Uygulama simgesi: dosyalar var, ICO çok boyutlu, pencere/.exe/kurucu/paketleme buna bağlı."""
import struct
from pathlib import Path

from PySide6.QtGui import QIcon

from src.core import paths

ROOT = Path(__file__).resolve().parent.parent


def test_icon_files_exist_and_ico_is_multi_size():
    assert (ROOT / "assets" / "app_icon.svg").is_file()
    data = paths.app_icon_path().read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    assert count >= 6
    sizes = {struct.unpack("<B", data[6 + 16 * i : 7 + 16 * i])[0] or 256 for i in range(count)}
    assert {16, 32, 48, 256} <= sizes


def test_ico_loads_as_qicon(qtbot):
    assert not QIcon(str(paths.app_icon_path())).isNull()


def test_packaging_references_icon_and_ui_data():
    spec = (ROOT / "flybycv.spec").read_text(encoding="utf-8")
    assert "app_icon.ico" in spec
    assert 'collect_data_files("qt_material")' in spec
    assert 'collect_data_files("qtawesome")' in spec
    assert '"assets"' in spec
    iss = (ROOT / "installer" / "flybycv.iss").read_text(encoding="utf-8")
    assert r"SetupIconFile=..\assets\app_icon.ico" in iss
    ps1 = (ROOT / "scripts" / "build_exe.ps1").read_text(encoding="utf-8")
    for needle in (r"qt_material\themes\light_blue.xml", r"qtawesome\fonts", r"assets\app_icon.ico"):
        assert needle in ps1
