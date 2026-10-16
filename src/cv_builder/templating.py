"""Şablonlar için tek Jinja2 ortamı.

Ayrı bir modül: `renderer` modül seviyesinde Playwright'ı yüklüyor, mail metni
üretmek için tarayıcıya ihtiyaç yok.
"""
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core import paths


def environment() -> Environment:
    """Şablon ortamını döndürür.

    Şablon klasörü `src.core.paths.templates_dir()`'dan, HER ÇAĞRIDA okunur (modül
    yüklenirken DEĞİL): paketlenmiş (.exe) hâlde bu, PyInstaller'ın açtığı `sys._MEIPASS`
    klasörüdür ve `environment()` ilk çağrıldığında (uygulama başladıktan sonra) hazır olur.

    Otomatik kaçış (autoescape) yalnızca .html şablonlarında açıktır; düz metin
    mail şablonlarında (.txt.j2) kapalı olmalı, yoksa "Ak & Ay" -> "Ak &amp; Ay" olur.
    """
    return Environment(
        loader=FileSystemLoader(paths.templates_dir()),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
