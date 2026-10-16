"""Ad değişikliği göçü: eski `%APPDATA%\\CV Autopilot` verisini ilk açılışta `%APPDATA%\\FlybyCV`'ye KOPYALAR.

Kurallar:
  - Yalnızca paketlenmiş .exe'de ve ev ortam değişkeni (`FLYBYCV_HOME`/`CV_AUTOPILOT_HOME`) YOKKEN
    çalışır; geliştirmede veri zaten proje klasöründedir.
  - Eski klasör varsa VE yeni klasör yoksa (ya da içinde hiç dosya yoksa) çalışır. Yeni klasörde
    tek bir dosya bile varsa hiçbir şey yapılmaz: iki veri seti asla karıştırılmaz.
  - Eski klasör yalnızca okunur, silinmez ve değiştirilmez.
  - Kopya önce yan bir geçici klasöre yapılır, bitince tek adımda yeni adı alır: yarıda kesilen bir
    kopya yarım bir `FlybyCV` klasörü bırakmaz (bir sonraki açılışta baştan denenir).
  - `app.db` güvenli SQLite kopyasıyla alınır (WAL'daki satırlar kaybolmaz), sonra veritabanındaki
    `cv_pdf_path` yolları yeni `output/` klasörüne göre yeniden yazılır.

Logging kurulmadan ÖNCE çalışır (log dosyası yeni klasörü oluşturur, "yeni klasör yok" koşulunu
bozar); bu yüzden sonuç bir `StartupMigration` olarak döner ve `log_outcome` ile sonradan loglanır.
Bu modül PySide6 İÇERMEZ.
"""
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.core import paths
from src.core.data_migration import (
    DB_FILE_NAME,
    _SQLITE_SIDECAR_SUFFIXES,
    _copy_sqlite_database,
    _rewrite_cv_pdf_paths,
)

logger = logging.getLogger(__name__)

#: Kopyanın yapıldığı geçici klasörün adına eklenen son ek (yeni klasörün yanında durur).
STAGING_SUFFIX = ".tasiniyor"


@dataclass(frozen=True)
class RenameMigrationResult:
    copied_files: int
    rewritten_cv_paths: int


@dataclass(frozen=True)
class StartupMigration:
    """`run_startup_migration` sonucu: hiçbir şey yapılmadıysa ikisi de `None`."""

    result: Optional[RenameMigrationResult] = None
    error: Optional[BaseException] = None
    legacy_home: Optional[Path] = None
    new_home: Optional[Path] = None


def _has_any_file(folder: Path) -> bool:
    return folder.is_dir() and any(entry.is_file() for entry in folder.rglob("*"))


def should_migrate(legacy_home: Path, new_home: Path) -> bool:
    """Eski klasör var ve yeni klasörde henüz hiç dosya yok mu?"""
    return legacy_home.is_dir() and not _has_any_file(new_home)


def _copy_tree(source: Path, destination: Path) -> int:
    copied = 0
    for source_file in sorted(p for p in source.rglob("*") if p.is_file()):
        if source_file.name.endswith(_SQLITE_SIDECAR_SUFFIXES):
            continue  # `app.db` kopyası WAL'ı zaten içerir; tek başına anlamsız
        target = destination / source_file.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source_file.name == DB_FILE_NAME:
            _copy_sqlite_database(source_file, target)
        else:
            shutil.copy2(source_file, target)
        copied += 1
    return copied


def migrate_legacy_home(legacy_home: Path, new_home: Path) -> Optional[RenameMigrationResult]:
    """Koşullar sağlanıyorsa `legacy_home`'u `new_home`'a kopyalar; yapılacak iş yoksa `None`."""
    if not should_migrate(legacy_home, new_home):
        return None

    staging = new_home.with_name(new_home.name + STAGING_SUFFIX)
    if staging.exists():
        shutil.rmtree(staging)  # önceki yarıda kalmış denemenin artığı (yalnızca bizim klasörümüz)
    staging.mkdir(parents=True)
    try:
        copied = _copy_tree(legacy_home, staging)
        rewritten = _rewrite_cv_pdf_paths(
            staging / "data" / DB_FILE_NAME, legacy_home / "output", new_home / "output"
        )
        if new_home.exists():
            shutil.rmtree(new_home)  # `should_migrate` gereği içinde hiç dosya yok (boş klasörler)
        staging.rename(new_home)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return RenameMigrationResult(copied_files=copied, rewritten_cv_paths=rewritten)


def run_startup_migration() -> StartupMigration:
    """Açılışta, logging'den ÖNCE çağrılır. Hiçbir hatayı yukarı fırlatmaz: açılış engellenmez."""
    if not paths.is_frozen() or paths.home_override():
        return StartupMigration()
    legacy_home, new_home = paths.legacy_app_home(), paths.default_app_home()
    try:
        result = migrate_legacy_home(legacy_home, new_home)
    except Exception as error:  # noqa: BLE001 - göç hatası uygulamayı açılmaz hale getirmemeli
        return StartupMigration(error=error, legacy_home=legacy_home, new_home=new_home)
    return StartupMigration(result=result, legacy_home=legacy_home, new_home=new_home)


def log_outcome(outcome: StartupMigration) -> None:
    """`run_startup_migration` sonucunu (logging kurulduktan SONRA) loglar."""
    if outcome.error is not None:
        logger.error(
            "Eski veri klasörü (%s) yeni klasöre (%s) kopyalanamadı; eski klasör olduğu gibi duruyor.",
            outcome.legacy_home,
            outcome.new_home,
            exc_info=outcome.error,
        )
    elif outcome.result is not None:
        logger.info(
            "Eski veri klasörü kopyalandı: %s -> %s (%d dosya, %d CV yolu güncellendi). Eski klasör silinmedi.",
            outcome.legacy_home,
            outcome.new_home,
            outcome.result.copied_files,
            outcome.result.rewritten_cv_paths,
        )
