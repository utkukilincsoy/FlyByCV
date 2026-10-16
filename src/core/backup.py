"""Veritabanı (`app.db`) + profil (`master_profile.json`) yedekleme ve geri yükleme.

Her yedek, veritabanının yanındaki `backups/` klasöründe TEK bir zip dosyasıdır:
`flybycv-yedek-YYYYMMDD-HHMMSS-<tür>.zip` (tür: otomatik / manuel / geri-yukleme-oncesi).
İçinde `app.db`, varsa `master_profile.json` ve küçük bir `manifest.json` bulunur.

Şifreler ve API anahtarları yedeğe GİRMEZ: zaten veritabanında değil, işletim sisteminin şifre
kasasında (keyring) duruyorlar (CLAUDE.md kural 6).

Kurallar:
- Açılış yedeği (`run_startup_backup`) günde en fazla BİR kez alınır; "Şimdi yedekle" ve geri
  yükleme öncesi güvenlik yedeği bu kurala tabi değildir. Hepsi aynı havuzdadır, en yeni
  `KEEP_BACKUPS` kadarı tutulur.
- Silme YALNIZCA `backups/` klasörünün doğrudan içindeki, ad desenine uyan normal dosyalara
  yapılır: klasör silinmez, alt klasöre inilmez, başka hiçbir dosyaya dokunulmaz.
- Geri yüklemede zip'ten yalnızca ADIYLA bilinen üyeler okunur (`extractall` yok); doğrulama
  geçmezse mevcut veriye hiç dokunulmaz.
- Loglara yalnızca dosya ADI yazılır, kullanıcının klasör yolu yazılmaz.
"""
import json
import logging
import os
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from src.core.data_migration import _copy_sqlite_database
from src.core.version import __version__
from src.cv_builder.profile_loader import MasterProfile

logger = logging.getLogger(__name__)

BACKUPS_DIR_NAME = "backups"
KEEP_BACKUPS = 7

KIND_AUTO = "otomatik"
KIND_MANUAL = "manuel"
KIND_PRE_RESTORE = "geri-yukleme-oncesi"
BACKUP_KINDS = (KIND_AUTO, KIND_MANUAL, KIND_PRE_RESTORE)

DB_MEMBER = "app.db"
PROFILE_MEMBER = "master_profile.json"
MANIFEST_MEMBER = "manifest.json"

#: Zip'ten okunacak tek bir üyenin en fazla boyutu (bozuk/kötü niyetli bir "zip bombası"na karşı).
MAX_MEMBER_BYTES = 500 * 1024 * 1024
#: Geri yüklenecek veritabanında bulunması gereken tablolar (bizim uygulamamızın veritabanı mı?).
REQUIRED_TABLES = frozenset({"company", "application", "setting"})

_NAME_PREFIX = "flybycv-yedek-"
_NAME_PATTERN = re.compile(
    #: Eski ad (`cv-autopilot-yedek-`) da tanınır: ad değişikliğinden önceki yedekler listelenir/geri yüklenir.
    r"^(?:flybycv|cv-autopilot)-yedek-(\d{8}-\d{6})-(otomatik|manuel|geri-yukleme-oncesi)(?:-(\d+))?\.zip$"
)
_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"
_TMP_SUFFIX = ".tmp"
_COPY_CHUNK_BYTES = 1024 * 1024


class BackupError(Exception):
    """Yedekleme/geri yükleme yapılamadı. Mesaj Türkçedir, kullanıcıya olduğu gibi gösterilir."""


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    created_at: datetime
    kind: str
    sequence: int  # aynı saniyede birden çok yedek: 1, 2, 3...


@dataclass(frozen=True)
class RestoreResult:
    safety_backup: Path  # geri yüklemeden hemen önce alınan, mevcut durumun yedeği
    profile_restored: bool  # yedekte profil vardı ve geri yazıldı


def backups_dir_for(db_path: Path) -> Path:
    """Yedek klasörü: veritabanının yanındaki `backups/` (gerçekte `data/backups/`)."""
    return db_path.parent / BACKUPS_DIR_NAME


# --- Listeleme ve temizlik --------------------------------------------------------------------


def _parse_name(path: Path) -> Optional[BackupInfo]:
    match = _NAME_PATTERN.match(path.name)
    if match is None:
        return None
    try:
        created_at = datetime.strptime(match.group(1), _TIMESTAMP_FORMAT)
    except ValueError:  # desene uyuyor ama geçersiz tarih (ör. 13. ay)
        return None
    return BackupInfo(path, created_at, match.group(2), int(match.group(3) or 1))


def _own_files(backups_dir: Path) -> list[Path]:
    """`backups_dir`'in DOĞRUDAN içindeki normal dosyalar (symlink ve klasörler hariç)."""
    if not backups_dir.is_dir():
        return []
    return [entry for entry in backups_dir.iterdir() if entry.is_file() and not entry.is_symlink()]


def list_backups(backups_dir: Path) -> list[BackupInfo]:
    """Uygulamanın kendi yedekleri, en yeniden en eskiye."""
    infos = [info for info in map(_parse_name, _own_files(backups_dir)) if info is not None]
    return sorted(infos, key=lambda info: (info.created_at, info.sequence), reverse=True)


def has_auto_backup_on(backups_dir: Path, day: date) -> bool:
    """O gün alınmış bir açılış (otomatik) yedeği var mı?"""
    return any(
        info.kind == KIND_AUTO and info.created_at.date() == day for info in list_backups(backups_dir)
    )


def _remove(path: Path) -> bool:
    try:
        path.unlink()
    except OSError as error:
        logger.warning(f"Eski yedek silinemedi: {path.name} ({type(error).__name__})")
        return False
    return True


def prune_backups(backups_dir: Path, keep: int = KEEP_BACKUPS) -> list[Path]:
    """En yeni `keep` yedek dışındakileri ve yarım kalmış `.tmp` yedek dosyalarını siler."""
    removed = [info.path for info in list_backups(backups_dir)[keep:] if _remove(info.path)]
    for entry in _own_files(backups_dir):
        if entry.name.endswith(_TMP_SUFFIX) and _NAME_PATTERN.match(entry.name[: -len(_TMP_SUFFIX)]):
            if _remove(entry):
                removed.append(entry)
    if removed:
        logger.info(f"{len(removed)} eski yedek silindi")
    return removed


# --- Yedek alma -------------------------------------------------------------------------------


def _unique_target(backups_dir: Path, now: datetime, kind: str) -> Path:
    stem = f"{_NAME_PREFIX}{now.strftime(_TIMESTAMP_FORMAT)}-{kind}"
    candidate = backups_dir / f"{stem}.zip"
    sequence = 2
    while candidate.exists():
        candidate = backups_dir / f"{stem}-{sequence}.zip"
        sequence += 1
    return candidate


def create_backup(
    db_path: Path,
    profile_path: Path,
    backups_dir: Path,
    *,
    kind: str = KIND_MANUAL,
    now: Optional[datetime] = None,
    prune: bool = True,
    keep: int = KEEP_BACKUPS,
) -> Path:
    """Veritabanı + profilin yedeğini alır, oluşan zip'in yolunu döner.

    Veritabanı sqlite3'ün çevrimiçi yedekleme API'siyle kopyalanır (`_copy_sqlite_database`):
    WAL dosyasındaki, henüz ana dosyaya yazılmamış kayıtlar da yedeğe girer. Zip önce `.tmp`
    adıyla yazılıp sonra yerine konur; yarım bir yedek asla geçerli bir yedek gibi görünmez.

    Raises:
        BackupError: Veritabanı yoksa ya da yedek yazılamadıysa.
    """
    if kind not in BACKUP_KINDS:
        raise ValueError(f"Bilinmeyen yedek türü: {kind}")
    if not db_path.is_file():
        raise BackupError("Yedeklenecek veritabanı bulunamadı.")

    now = now or datetime.now()
    backups_dir.mkdir(parents=True, exist_ok=True)
    target = _unique_target(backups_dir, now, kind)
    tmp_zip = target.with_name(target.name + _TMP_SUFFIX)
    manifest = {"app_version": __version__, "created_at": now.isoformat(timespec="seconds"), "kind": kind}

    try:
        with tempfile.TemporaryDirectory(prefix="flybycv-yedek-") as work_dir:
            db_copy = Path(work_dir) / DB_MEMBER
            _copy_sqlite_database(db_path, db_copy)
            with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(db_copy, DB_MEMBER)
                if profile_path.is_file():
                    archive.write(profile_path, PROFILE_MEMBER)
                archive.writestr(MANIFEST_MEMBER, json.dumps(manifest, ensure_ascii=False, indent=2))
        os.replace(tmp_zip, target)
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        tmp_zip.unlink(missing_ok=True)
        logger.warning(f"Yedek alınamadı ({type(error).__name__})")
        raise BackupError("Yedek alınamadı. Ayrıntı için log dosyasına bakın.") from error

    logger.info(f"Yedek alındı: {target.name}")
    if prune:
        prune_backups(backups_dir, keep)
    return target


def run_startup_backup(
    db_path: Path, profile_path: Path, backups_dir: Path, *, now: Optional[datetime] = None
) -> Optional[Path]:
    """Açılış yedeği: günde en fazla bir kez. Hiçbir durumda hata fırlatmaz.

    Veritabanı henüz yoksa (ilk açılış) ya da o gün zaten bir açılış yedeği alınmışsa atlanır.
    Yedek alınamazsa yalnızca loglanır: yedek yüzünden uygulama açılmaz hale gelmemeli.
    """
    now = now or datetime.now()
    try:
        if not db_path.is_file():
            logger.debug("Açılış yedeği atlandı: veritabanı henüz yok (ilk açılış)")
            return None
        if has_auto_backup_on(backups_dir, now.date()):
            logger.debug("Açılış yedeği atlandı: bugün zaten alınmış")
            return None
        return create_backup(db_path, profile_path, backups_dir, kind=KIND_AUTO, now=now)
    except Exception as error:  # yedek, açılışı asla engellememeli
        logger.warning(f"Açılış yedeği alınamadı ({type(error).__name__})")
        return None


# --- Geri yükleme -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _StagedBackup:
    db_file: Path
    profile_bytes: Optional[bytes]


def _copy_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, destination: Path) -> None:
    """Tek bir üyeyi, boyut sınırını akış sırasında da denetleyerek dosyaya yazar."""
    if info.file_size > MAX_MEMBER_BYTES:
        raise BackupError("Yedekteki dosya beklenmeyecek kadar büyük.")
    written = 0
    with archive.open(info) as source, open(destination, "wb") as target:
        while chunk := source.read(_COPY_CHUNK_BYTES):
            written += len(chunk)
            if written > MAX_MEMBER_BYTES:
                raise BackupError("Yedekteki dosya beklenmeyecek kadar büyük.")
            target.write(chunk)


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size > MAX_MEMBER_BYTES:
        raise BackupError("Yedekteki dosya beklenmeyecek kadar büyük.")
    with archive.open(info) as source:
        data = source.read(MAX_MEMBER_BYTES + 1)
    if len(data) > MAX_MEMBER_BYTES:
        raise BackupError("Yedekteki dosya beklenmeyecek kadar büyük.")
    return data


def _check_database(db_file: Path) -> None:
    """Çıkarılan dosya sağlam bir SQLite veritabanı mı ve bizim tablolarımızı içeriyor mu?"""
    try:
        connection = sqlite3.connect(str(db_file))
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        raise BackupError("Yedekteki veritabanı bozuk ya da bir SQLite veritabanı değil.") from error
    if not integrity or integrity[0] != "ok":
        raise BackupError("Yedekteki veritabanı bozuk (bütünlük denetimi geçmedi).")
    if not REQUIRED_TABLES <= tables:
        raise BackupError("Yedekteki veritabanı bu uygulamaya ait görünmüyor.")


def _check_profile(data: bytes) -> None:
    try:
        MasterProfile.model_validate(json.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, ValueError) as error:  # pydantic.ValidationError da ValueError
        raise BackupError("Yedekteki profil (master_profile.json) geçersiz.") from error


def _stage_backup(zip_path: Path, work_dir: Path) -> _StagedBackup:
    """Zip'i açar, YALNIZCA adıyla bilinen üyeleri geçici klasöre çıkarır ve doğrular."""
    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as error:
        raise BackupError("Seçilen dosya geçerli bir yedek değil (zip açılamadı).") from error

    db_file = work_dir / DB_MEMBER
    try:
        with archive:
            members = {info.filename: info for info in archive.infolist()}
            if DB_MEMBER not in members:
                raise BackupError("Seçilen yedekte veritabanı (app.db) yok.")
            _copy_member(archive, members[DB_MEMBER], db_file)
            profile_bytes = (
                _read_member(archive, members[PROFILE_MEMBER]) if PROFILE_MEMBER in members else None
            )
    except (zipfile.BadZipFile, OSError) as error:  # CRC hatası, yarım dosya...
        raise BackupError("Seçilen yedek okunamadı (dosya bozuk olabilir).") from error

    _check_database(db_file)
    if profile_bytes is not None:
        _check_profile(profile_bytes)
    return _StagedBackup(db_file, profile_bytes)


def _write_file_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".geri-yukleme" + _TMP_SUFFIX)
    try:
        tmp_path.write_bytes(data)
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def restore_backup(
    zip_path: Path,
    db_path: Path,
    profile_path: Path,
    backups_dir: Path,
    *,
    dispose_engine: Callable[[], None],
    now: Optional[datetime] = None,
    keep: int = KEEP_BACKUPS,
) -> RestoreResult:
    """Seçilen yedeği geri yükler.

    Sıra: (1) yedek doğrulanır — geçmezse hiçbir şeye dokunulmaz; (2) mevcut durumun güvenlik
    yedeği alınır (budamadan: seçilen eski yedek bu sırada silinmesin); (3) `dispose_engine()`
    ile veritabanı bağlantı havuzu kapatılır; (4) veritabanı sqlite3 yedekleme API'siyle canlı
    `app.db`'nin ÜZERİNE yazılır (tek adımda; yarıda kalırsa hedef değişmez); (5) yedekte profil
    varsa atomik olarak yazılır, yoksa mevcut profile dokunulmaz; (6) eski yedekler budanır.

    Çağıran, veritabanına yazan hiçbir arka plan işinin çalışmadığından emin olmalı ve sonrasında
    uygulamayı yeniden başlatmalıdır (açık sekmeler eski veriyi tutuyor olabilir).

    Raises:
        BackupError: Yedek geçersizse ya da geri yükleme yapılamadıysa.
    """
    with tempfile.TemporaryDirectory(prefix="flybycv-geri-yukleme-") as work_dir:
        staged = _stage_backup(zip_path, Path(work_dir))
        safety_backup = create_backup(
            db_path, profile_path, backups_dir, kind=KIND_PRE_RESTORE, now=now, prune=False
        )

        dispose_engine()
        try:
            _copy_sqlite_database(staged.db_file, db_path)
            if staged.profile_bytes is not None:
                _write_file_atomically(profile_path, staged.profile_bytes)
        except (OSError, sqlite3.Error) as error:
            logger.warning(f"Geri yükleme başarısız ({type(error).__name__})")
            raise BackupError(
                f"Geri yükleme yapılamadı. Geri yükleme öncesi yedeğiniz: {safety_backup.name}"
            ) from error

    logger.info(f"Yedek geri yüklendi: {zip_path.name} (öncesi: {safety_backup.name})")
    prune_backups(backups_dir, keep)
    return RestoreResult(safety_backup, staged.profile_bytes is not None)
