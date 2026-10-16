"""İlk açılışta eski (paketlenmeden önceki) `data/`+`output/` klasörlerinden
`%APPDATA%`'daki veri klasörüne kişisel veri taşıma (Faz 8, Adım 8.3).

Yalnızca KOPYALAR; kaynak klasörler asla silinmez ya da değiştirilmez. Hedefte, kaynaktaki
HERHANGİ bir dosyayla aynı göreli yolda bir dosya VARSA HİÇBİR ŞEY kopyalanmaz -- kısmen
taşınmış, iki farklı kaynağın karıştığı bir veri seti bırakmamak için taşımanın TAMAMI
iptal edilir (bkz. `migrate_legacy_data`).

Bu modül PySide6 İÇERMEZ (bkz. `src/ui/status_strip.py` docstring'indeki katman ayrımı).

Adım 10.9b'den beri açılış akışından ÇAĞRILMAZ: paketlenmiş .exe'nin ilk açılıştaki "Eski veriyi
taşı" penceresi (`migration_dialog.py`) kaldırıldı. Modül yine de duruyor çünkü
`settings_migration` ve `rename_migration` buradaki `_copy_sqlite_database`'i (güvenli SQLite
kopyası) ve `_rewrite_cv_pdf_paths`'i kullanıyor.
"""
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import inspect
from sqlmodel import Session, create_engine, select

from src.core.db import configure_sqlite_engine
from src.core.models import Application

#: Bir klasörün "gerçekten eski data/ klasörü mü" olduğunu doğrulamak ve "ilk açılış mı?"
#: tespiti için tek referans dosya.
PROFILE_FILE_NAME = "master_profile.json"

DB_FILE_NAME = "app.db"

#: SQLite WAL modunun (Faz 6.2 kararı) yardımcı dosyaları -- tek başlarına anlamsızlar (ana
#: dosyayla eşleşmeleri gerekir), bu yüzden genel dosya kopyalamasında ATLANIR. `app.db`nin
#: kendisi bunun yerine `_copy_sqlite_database` ile, WAL'daki commit edilmiş ama henüz ana
#: dosyaya "checkpoint" edilmemiş satırları da içerecek şekilde ayrıca kopyalanır.
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")

#: Ayarlıysa dialog hiç açılmadan doğrudan bu klasörden taşınır (testler/manuel deneme).
LEGACY_SOURCE_ENV_VAR = "CV_AUTOPILOT_LEGACY_DATA"


@dataclass(frozen=True)
class LegacySource:
    """Kullanıcının seçtiği eski konumdan türetilen, taşınacak iki klasör.

    `output_dir` var olmayabilir (hiç başvuru hazırlanmamışsa) -- `migrate_legacy_data`
    bunu sorun etmeden atlar.
    """

    data_dir: Path
    output_dir: Path


@dataclass(frozen=True)
class MigrationResult:
    """`migrate_legacy_data()`'nın sonucu -- kullanıcıya gösterilecek özetin kaynağı."""

    copied: list[Path] = field(default_factory=list)
    conflicts: list[Path] = field(default_factory=list)
    rewritten_cv_paths: int = 0

    @property
    def copied_count(self) -> int:
        return len(self.copied)

    @property
    def aborted_due_to_conflicts(self) -> bool:
        return bool(self.conflicts)


def is_first_launch(dest_data_dir: Path) -> bool:
    """Hedefte henüz profil yoksa (ilk açılış ya da taşıma hiç yapılmamış) `True`."""
    return not (dest_data_dir / PROFILE_FILE_NAME).exists()


def find_legacy_source(selected: Path) -> Optional[LegacySource]:
    """Kullanıcının seçtiği klasörü doğrular ve taşınacak `data/`+`output/` çiftini üretir.

    Kullanıcı doğrudan `data/` klasörünü seçmiş olabilir (o zaman `selected`in kendisinde
    `master_profile.json` vardır, kardeş `output/` bir üst klasördedir) ya da proje kökünü
    seçmiş olabilir (o zaman `selected/data` altında vardır, `output/` de `selected`in
    altındadır). İkisi de yoksa yanlış klasör seçilmiştir, `None` döner.
    """
    if (selected / PROFILE_FILE_NAME).exists():
        return LegacySource(data_dir=selected, output_dir=selected.parent / "output")
    nested = selected / "data"
    if (nested / PROFILE_FILE_NAME).exists():
        return LegacySource(data_dir=nested, output_dir=selected / "output")
    return None


def legacy_source_override() -> Optional[LegacySource]:
    """`CV_AUTOPILOT_LEGACY_DATA` ayarlıysa o yoldan bir `LegacySource` döner.

    Değer doğrudan `data/` klasörünü göstermelidir (testler/manuel deneme içindir);
    `output/` kardeş klasör olarak türetilir.
    """
    value = os.environ.get(LEGACY_SOURCE_ENV_VAR)
    if not value:
        return None
    data_dir = Path(value)
    return LegacySource(data_dir=data_dir, output_dir=data_dir.parent / "output")


def _iter_source_files(source_dir: Path) -> Iterator[Path]:
    if not source_dir.is_dir():
        return
    for path in sorted(p for p in source_dir.rglob("*") if p.is_file()):
        if path.name.endswith(_SQLITE_SIDECAR_SUFFIXES):
            continue
        yield path


def _plan_copies(pairs: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    """`(kaynak dosya, hedef dosya)` çiftlerinin düz listesi -- `data/` ve `output/` tek bir
    listede birleştirilir ki çakışma kontrolü (`migrate_legacy_data`) ikisini BİRLİKTE,
    atomik olarak değerlendirsin."""
    plan = []
    for source_dir, dest_dir in pairs:
        for source_file in _iter_source_files(source_dir):
            relative = source_file.relative_to(source_dir)
            plan.append((source_file, dest_dir / relative))
    return plan


def _copy_sqlite_database(source_db: Path, dest_db: Path) -> None:
    """`shutil.copy2` yerine `sqlite3`in çevrimiçi yedekleme API'sini kullanır.

    Veritabanı WAL modunda çalışıyor (Faz 6.2 kararı): commit edilmiş ama henüz ana
    `app.db` dosyasına "checkpoint" edilmemiş satırlar yalnızca `app.db-wal` dosyasında
    olabilir. Ham bir bayt kopyası (`shutil.copy2`) bu satırları SESSİZCE KAYBEDER.
    `sqlite3.Connection.backup()` kaynağı normal bir bağlantıyla açar (WAL'ı SQLite'ın
    kendisi okur, biz elle birleştirmeyiz) ve tutarlı, TEK dosyalık bir kopya üretir --
    hedefte ayrıca `-wal`/`-shm` dosyasına gerek kalmaz.
    """
    source_conn = sqlite3.connect(str(source_db))
    try:
        dest_conn = sqlite3.connect(str(dest_db))
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def _rewrite_cv_pdf_paths(dest_db: Path, old_output_dir: Path, new_output_dir: Path) -> int:
    """Taşınan veritabanındaki `Application.cv_pdf_path` sütunundaki eski `output/`
    yollarını yeni `output_dir()`e yeniden yazar.

    PDF dosyasının kendisi zaten aynı göreli yolla yeni `output/` altına kopyalandı
    (`migrate_legacy_data`'nın `_plan_copies` çıktısı ikisini birlikte kapsıyor); burada
    yalnızca veritabanındaki METİN yolu güncellenir -- aksi halde önizleme/yeniden gönderim
    hâlâ eski (taşınmadan önceki) konuma bakmaya devam ederdi.

    `application` tablosu yoksa (ör. çok eski/hiç `init_db()` görmemiş bir `app.db`)
    sessizce `0` döner -- bu gerçek bir kurulumda olmaz (`main.py` her açılışta `init_db()`
    çağırır) ama dosyalar zaten kopyalandıktan SONRA burada patlayıp kullanıcıyı yarım kalmış
    gibi görünen bir hata ekranıyla karşılamak yerine, en azından dosya taşıma tamamlanmalı.
    """
    if not dest_db.exists():
        return 0

    engine = create_engine(f"sqlite:///{dest_db}")
    configure_sqlite_engine(engine)
    if not inspect(engine).has_table(Application.__tablename__):
        engine.dispose()
        return 0
    old_prefix = str(old_output_dir)
    updated = 0
    try:
        with Session(engine) as session:
            applications = session.exec(select(Application)).all()
            for application in applications:
                if application.cv_pdf_path and application.cv_pdf_path.startswith(old_prefix):
                    relative = Path(application.cv_pdf_path).relative_to(old_output_dir)
                    application.cv_pdf_path = str(new_output_dir / relative)
                    session.add(application)
                    updated += 1
            if updated:
                session.commit()
    finally:
        engine.dispose()
    return updated


def migrate_legacy_data(
    source: LegacySource, dest_data_dir: Path, dest_output_dir: Path
) -> MigrationResult:
    """`source.data_dir` ve `source.output_dir`i (varsa) sırasıyla `dest_data_dir`/
    `dest_output_dir`e KOPYALAR, sonra `Application.cv_pdf_path`teki eski `output/`
    referanslarını yeni konuma yeniden yazar.

    Hedefte, kaynaktaki HERHANGİ bir dosyayla aynı göreli yolda bir dosya VARSA HİÇBİR ŞEY
    kopyalanmaz (`MigrationResult.conflicts` doldurulur, `copied` boş kalır) -- tek tek
    dosya atlayıp devam etmek iki farklı kaynaktan gelen kayıtları aynı veritabanında/
    profilde karıştırabilirdi, bu yüzden taşımanın TAMAMI iptal edilir.

    Kaynak klasörler hiç değiştirilmez/silinmez -- yalnızca okunur.
    """
    pairs = [(source.data_dir, dest_data_dir), (source.output_dir, dest_output_dir)]
    plan = _plan_copies(pairs)

    conflicts = [dest for _, dest in plan if dest.exists()]
    if conflicts:
        return MigrationResult(copied=[], conflicts=conflicts)

    copied: list[Path] = []
    for source_file, dest_file in plan:
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        if source_file.name == DB_FILE_NAME:
            _copy_sqlite_database(source_file, dest_file)
        else:
            shutil.copy2(source_file, dest_file)
        copied.append(dest_file)

    rewritten = _rewrite_cv_pdf_paths(dest_data_dir / DB_FILE_NAME, source.output_dir, dest_output_dir)

    return MigrationResult(copied=copied, conflicts=[], rewritten_cv_paths=rewritten)
