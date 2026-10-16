"""Kullanıcının kendi PDF CV'sini olduğu gibi kullanma modu (Faz 10 Adım 10.3a).

`cv_mode` ayarı `own_pdf` iken CV özelleştirme/PDF üretimi (`renderer.py`, Playwright) hiç
çalışmaz (`application_builder.build_application`): kullanıcının seçtiği dosya burada
`paths.own_cv_path()`e KOPYALANIR ve her başvuruda aynen o dosya kullanılır — kaynak dosya
kullanıcının bilgisayarında taşınsa/silinse bile uygulamanın kendi kopyası kalır.
"""
import logging
import shutil
from pathlib import Path
from typing import Optional

from src.core import paths

logger = logging.getLogger(__name__)

#: Her başvuruya eklenen dosyanın üst sınırı. Gmail eki 25 MB'ta reddeder; bir CV için 10 MB bol.
MAX_OWN_CV_BYTES = 10 * 1024 * 1024
#: Gerçek bir PDF dosyası bu baytlarla başlar (uzantısı değiştirilmiş başka bir dosyayı eler).
PDF_SIGNATURE = b"%PDF-"


def set_own_cv(source: Path) -> Path:
    """`source`'daki PDF'i `paths.own_cv_path()`e kopyalar ve hedef yolu döner.

    Raises:
        FileNotFoundError: `source` yoksa.
        ValueError: `source` `.pdf` uzantılı değilse, içeriği PDF değilse ya da çok büyükse.
    """
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Dosya bulunamadı: {source}")
    if source.suffix.lower() != ".pdf":
        raise ValueError("Yalnızca PDF dosyası seçilebilir.")
    size = source.stat().st_size
    if size > MAX_OWN_CV_BYTES:
        raise ValueError(
            f"PDF çok büyük ({size / (1024 * 1024):.1f} MB). "
            f"En fazla {MAX_OWN_CV_BYTES // (1024 * 1024)} MB olabilir."
        )
    with source.open("rb") as handle:
        if handle.read(len(PDF_SIGNATURE)) != PDF_SIGNATURE:
            raise ValueError("Bu dosya geçerli bir PDF değil (yalnızca uzantısı .pdf).")

    destination = paths.own_cv_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    logger.info(f"Kendi CV kopyalandı: {source.name}")  # tam yol kullanıcı adını içerir, loga yazılmaz
    return destination


def has_own_cv() -> bool:
    """Kopyalanmış bir "kendi CV" dosyası var mı."""
    return paths.own_cv_path().is_file()


def own_cv_status_text() -> str:
    """Ayarlar ekranında gösterilecek kısa Türkçe durum metni (dosyanın İÇERİĞİNİ değil,
    var olup olmadığını ve boyutunu söyler)."""
    path = paths.own_cv_path()
    if not path.is_file():
        return "Henüz bir PDF seçilmedi."
    size_kb = path.stat().st_size / 1024
    return f"Kayıtlı: {path.name} ({size_kb:.0f} KB)"


def remove_own_cv() -> bool:
    """Kayıtlı "kendi CV" dosyasını siler; yoksa `False` döner."""
    path = paths.own_cv_path()
    if not path.is_file():
        return False
    path.unlink()
    logger.info("Kendi CV silindi")
    return True


def own_cv_path_if_present() -> Optional[Path]:
    path = paths.own_cv_path()
    return path if path.is_file() else None
