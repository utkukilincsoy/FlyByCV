"""assets/app_icon.svg'den çok boyutlu assets/app_icon.ico ve docs/icon_preview.png üretir.

Kullanım:  python scripts/make_icon.py
Yalnızca PySide6 (QtSvg) kullanır; ek paket gerekmez. ICO içine PNG biçiminde 16-256 px eklenir.
"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
SVG_PATH = ROOT / "assets" / "app_icon.svg"
ICO_PATH = ROOT / "assets" / "app_icon.ico"
PREVIEW_PATH = ROOT / "docs" / "icon_preview.png"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(renderer: QSvgRenderer, size: int) -> QImage:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def build_ico(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    return header + entries + blobs


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows konsolu cp1252
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    renderer = QSvgRenderer(str(SVG_PATH))
    if not renderer.isValid():
        print(f"SVG okunamadı: {SVG_PATH}")
        return 1
    ICO_PATH.write_bytes(build_ico([(s, png_bytes(render(renderer, s))) for s in ICO_SIZES]))
    PREVIEW_PATH.parent.mkdir(exist_ok=True)
    render(renderer, 256).save(str(PREVIEW_PATH), "PNG")
    print(f"Yazıldı: {ICO_PATH} ({ICO_PATH.stat().st_size} bayt), {PREVIEW_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
