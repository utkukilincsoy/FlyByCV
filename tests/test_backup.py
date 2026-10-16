"""core/backup: yedek alma, saklama (son 7), günde bir açılış yedeği, geri yükleme.

Hepsi `tmp_path` altında, dosya tabanlı (WAL modunda) gerçek bir SQLite veritabanıyla çalışır;
gerçek `data/` klasörüne dokunulmaz.
"""
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import create_engine, select

from src.core import backup
from src.core.backup import (
    DB_MEMBER,
    KEEP_BACKUPS,
    KIND_AUTO,
    KIND_MANUAL,
    KIND_PRE_RESTORE,
    MANIFEST_MEMBER,
    PROFILE_MEMBER,
    BackupError,
    backups_dir_for,
    create_backup,
    has_auto_backup_on,
    list_backups,
    prune_backups,
    restore_backup,
    run_startup_backup,
)
from src.core.db import configure_sqlite_engine, get_session, init_db
from src.core.models import Company
from tests.conftest import build_company, build_sample_profile

NOW = datetime(2026, 10, 9, 11, 5, 0)


@pytest.fixture
def home(tmp_path: Path) -> dict[str, Path]:
    data = tmp_path / "data"
    data.mkdir()
    return {
        "db": data / "app.db",
        "profile": data / "master_profile.json",
        "backups": data / "backups",
    }


@pytest.fixture
def file_engine(home):
    engine = create_engine(f"sqlite:///{home['db']}", connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)  # WAL modu: gerçek uygulamayla aynı
    init_db(engine=engine)
    yield engine
    engine.dispose()


def _add_company(engine, domain: str) -> None:
    with get_session(engine) as session:
        session.add(build_company(domain=domain, name=domain))
        session.commit()


def _domains(engine) -> list[str]:
    with get_session(engine) as session:
        return sorted(company.domain for company in session.exec(select(Company)).all())


def _write_profile(path: Path, name: str) -> None:
    profile = build_sample_profile()
    profile.personal_info.name = name
    path.write_text(profile.model_dump_json(), encoding="utf-8")


def _domains_in_zip(zip_path: Path, tmp_path: Path) -> list[str]:
    extracted = tmp_path / f"okunan-{zip_path.stem}.db"
    with zipfile.ZipFile(zip_path) as archive:
        extracted.write_bytes(archive.read(DB_MEMBER))
    connection = sqlite3.connect(str(extracted))
    try:
        return sorted(row[0] for row in connection.execute("SELECT domain FROM company"))
    finally:
        connection.close()


def _backup(home, now: datetime, kind: str = KIND_MANUAL, **kwargs) -> Path:
    return create_backup(home["db"], home["profile"], home["backups"], kind=kind, now=now, **kwargs)


# --- Yedek alma -------------------------------------------------------------------------------


def test_backups_dir_is_next_to_database(home):
    assert backups_dir_for(home["db"]) == home["backups"]


def test_backup_contains_wal_rows_profile_and_manifest_only(home, file_engine, tmp_path):
    _add_company(file_engine, "acme.com")  # motor açık: satır henüz yalnızca WAL'da olabilir
    _write_profile(home["profile"], "Deneme Kişi")

    zip_path = _backup(home, NOW)

    assert zip_path.name == "flybycv-yedek-20261009-110500-manuel.zip"
    with zipfile.ZipFile(zip_path) as archive:
        # Yalnızca bu üç dosya: şifreler/API anahtarları keyring'de, yedeğe girmez.
        assert sorted(archive.namelist()) == sorted([DB_MEMBER, PROFILE_MEMBER, MANIFEST_MEMBER])
        assert archive.read(PROFILE_MEMBER) == home["profile"].read_bytes()
    assert _domains_in_zip(zip_path, tmp_path) == ["acme.com"]


def test_backup_without_profile_still_works(home, file_engine):
    zip_path = _backup(home, NOW)

    with zipfile.ZipFile(zip_path) as archive:
        assert PROFILE_MEMBER not in archive.namelist()
        assert DB_MEMBER in archive.namelist()


def test_backup_without_database_raises(home):
    with pytest.raises(BackupError):
        _backup(home, NOW)
    assert not home["db"].exists()  # sqlite3 boş bir veritabanı yaratmamalı


def test_same_second_backups_get_sequence_suffix(home, file_engine):
    first = _backup(home, NOW)
    second = _backup(home, NOW)

    assert first.name == "flybycv-yedek-20261009-110500-manuel.zip"
    assert second.name == "flybycv-yedek-20261009-110500-manuel-2.zip"
    assert [info.path for info in list_backups(home["backups"])] == [second, first]


def test_no_tmp_file_left_after_backup(home, file_engine):
    _backup(home, NOW)

    assert [path.name for path in home["backups"].iterdir() if path.name.endswith(".tmp")] == []


# --- Saklama: en yeni 7 ve yalnızca kendi dosyaları ---------------------------------------


def test_eighth_backup_removes_the_oldest(home, file_engine):
    created = [_backup(home, NOW + timedelta(hours=index)) for index in range(KEEP_BACKUPS + 1)]

    remaining = [info.path for info in list_backups(home["backups"])]
    assert len(remaining) == KEEP_BACKUPS
    assert created[0] not in remaining
    assert not created[0].exists()
    assert remaining[0] == created[-1]


def test_prune_never_touches_foreign_files(home, file_engine, tmp_path):
    backups = home["backups"]
    backups.mkdir()
    foreign = [
        backups / "notlarim.txt",
        backups / "flybycv-yedek-elle-adlandirilmis.zip",
        backups / "flybycv-yedek-20200101-000000-baska.zip",
    ]
    for path in foreign:
        path.write_text("dokunma", encoding="utf-8")
    nested_dir = backups / "eski"
    nested_dir.mkdir()
    nested = nested_dir / "flybycv-yedek-20200101-000000-manuel.zip"
    nested.write_text("alt klasör", encoding="utf-8")
    outside = tmp_path / "flybycv-yedek-20200101-000000-manuel.zip"
    outside.write_text("klasör dışı", encoding="utf-8")

    for index in range(KEEP_BACKUPS + 3):
        _backup(home, NOW + timedelta(minutes=index))

    assert all(path.exists() for path in foreign)
    assert nested.exists() and nested_dir.is_dir()
    assert outside.exists()
    assert len(list_backups(backups)) == KEEP_BACKUPS


def test_backups_with_the_old_app_name_are_listed_and_pruned(home, file_engine):
    """Ad değişikliğinden önceki `cv-autopilot-yedek-*.zip` dosyaları da yedek sayılır (geri yüklenebilir)."""
    _backup(home, NOW)
    old = home["backups"] / "cv-autopilot-yedek-20261001-090000-otomatik.zip"
    old.write_bytes(b"eski")

    infos = list_backups(home["backups"])
    assert [info.path for info in infos][-1] == old
    assert infos[-1].kind == KIND_AUTO

    for index in range(1, KEEP_BACKUPS + 1):
        _backup(home, NOW + timedelta(minutes=index))
    assert not old.exists()  # en eski olduğu için saklama sınırında silindi


def test_prune_removes_only_leftover_backup_tmp_files(home):
    backups = home["backups"]
    backups.mkdir()
    leftover = backups / "cv-autopilot-yedek-20261009-110500-manuel.zip.tmp"
    leftover.write_text("yarım", encoding="utf-8")
    foreign_tmp = backups / "baska-bir-sey.tmp"
    foreign_tmp.write_text("dokunma", encoding="utf-8")

    removed = prune_backups(backups)

    assert removed == [leftover]
    assert foreign_tmp.exists()


def test_prune_on_missing_folder_is_noop(tmp_path):
    assert prune_backups(tmp_path / "yok") == []


# --- Açılış yedeği: günde en fazla bir kez -----------------------------------------------------


def test_startup_backup_is_taken_once_per_day(home, file_engine):
    first = run_startup_backup(home["db"], home["profile"], home["backups"], now=NOW)
    second = run_startup_backup(home["db"], home["profile"], home["backups"], now=NOW + timedelta(hours=3))
    next_day = run_startup_backup(home["db"], home["profile"], home["backups"], now=NOW + timedelta(days=1))

    assert first is not None and first.name.endswith(f"-{KIND_AUTO}.zip")
    assert second is None
    assert next_day is not None
    assert len(list_backups(home["backups"])) == 2


def test_manual_and_pre_restore_backups_do_not_count_as_daily_auto(home, file_engine):
    _backup(home, NOW, kind=KIND_MANUAL)
    _backup(home, NOW, kind=KIND_PRE_RESTORE)

    assert has_auto_backup_on(home["backups"], NOW.date()) is False
    assert run_startup_backup(home["db"], home["profile"], home["backups"], now=NOW) is not None


def test_manual_backup_is_not_limited_by_daily_rule(home, file_engine):
    run_startup_backup(home["db"], home["profile"], home["backups"], now=NOW)

    assert _backup(home, NOW + timedelta(minutes=1)).exists()
    assert _backup(home, NOW + timedelta(minutes=2)).exists()


def test_startup_backup_skips_first_launch_without_database(home):
    assert run_startup_backup(home["db"], home["profile"], home["backups"], now=NOW) is None
    assert not home["backups"].exists()


def test_startup_backup_never_raises(home, file_engine, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError("disk dolu")

    monkeypatch.setattr(backup, "create_backup", broken)

    with caplog.at_level("WARNING", logger="src.core.backup"):
        assert run_startup_backup(home["db"], home["profile"], home["backups"], now=NOW) is None
    assert "Açılış yedeği alınamadı (RuntimeError)" in caplog.text


# --- Geri yükleme -----------------------------------------------------------------------------


class _DisposeSpy:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        self.engine.dispose()


def test_restore_brings_back_data_and_profile_after_safety_backup(home, file_engine, tmp_path):
    _add_company(file_engine, "eski.com")
    _write_profile(home["profile"], "Eski Ad")
    old_backup = _backup(home, NOW)

    _add_company(file_engine, "yeni.com")
    _write_profile(home["profile"], "Yeni Ad")
    dispose = _DisposeSpy(file_engine)

    result = restore_backup(
        old_backup, home["db"], home["profile"], home["backups"],
        dispose_engine=dispose, now=NOW + timedelta(hours=1),
    )

    assert dispose.calls == 1
    assert _domains(file_engine) == ["eski.com"]
    assert '"Eski Ad"' in home["profile"].read_text(encoding="utf-8")
    assert result.profile_restored is True
    # Geri yükleme öncesi durum ayrıca yedeklendi ve onun içinde "yeni.com" var.
    assert result.safety_backup.name.endswith(f"-{KIND_PRE_RESTORE}.zip")
    assert _domains_in_zip(result.safety_backup, tmp_path) == ["eski.com", "yeni.com"]
    assert '"Yeni Ad"' in zipfile.ZipFile(result.safety_backup).read(PROFILE_MEMBER).decode("utf-8")


def test_restore_without_profile_in_backup_keeps_current_profile(home, file_engine):
    old_backup = _backup(home, NOW)  # profil yokken alındı
    _write_profile(home["profile"], "Şimdiki Ad")

    result = restore_backup(
        old_backup, home["db"], home["profile"], home["backups"],
        dispose_engine=file_engine.dispose, now=NOW + timedelta(hours=1),
    )

    assert result.profile_restored is False
    assert '"Şimdiki Ad"' in home["profile"].read_text(encoding="utf-8")


def test_restoring_oldest_backup_with_full_pool_works(home, file_engine):
    _add_company(file_engine, "ilk.com")
    oldest = _backup(home, NOW)
    _add_company(file_engine, "sonra.com")
    for index in range(1, KEEP_BACKUPS):
        _backup(home, NOW + timedelta(hours=index))
    assert len(list_backups(home["backups"])) == KEEP_BACKUPS

    restore_backup(
        oldest, home["db"], home["profile"], home["backups"],
        dispose_engine=file_engine.dispose, now=NOW + timedelta(days=1),
    )

    assert _domains(file_engine) == ["ilk.com"]
    assert len(list_backups(home["backups"])) == KEEP_BACKUPS  # budama geri yüklemeden SONRA


def _sqlite_bytes(tmp_path: Path, *, with_tables: bool) -> bytes:
    path = tmp_path / f"uretilen-{len(list(tmp_path.glob('uretilen-*.db')))}.db"
    connection = sqlite3.connect(str(path))
    if with_tables:
        for table in ("company", "application", "setting"):
            connection.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
    else:
        connection.execute("CREATE TABLE baska (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    return path.read_bytes()


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def _invalid_backups(tmp_path: Path) -> dict[str, Path]:
    not_a_zip = tmp_path / "zip-degil.zip"
    not_a_zip.write_bytes(b"bu bir zip degil")
    return {
        "zip değil": not_a_zip,
        "app.db yok": _zip(tmp_path / "bos.zip", {"notlar.txt": b"merhaba"}),
        "yalnızca ../app.db": _zip(tmp_path / "kacis.zip", {"../app.db": _sqlite_bytes(tmp_path, with_tables=True)}),
        "db değil": _zip(tmp_path / "bozuk-db.zip", {DB_MEMBER: b"SQLite degil" * 100}),
        "başka uygulamanın db'si": _zip(tmp_path / "yabanci.zip", {DB_MEMBER: _sqlite_bytes(tmp_path, with_tables=False)}),
        "geçersiz profil": _zip(
            tmp_path / "bozuk-profil.zip",
            {DB_MEMBER: _sqlite_bytes(tmp_path, with_tables=True), PROFILE_MEMBER: b'{"yanlis": true}'},
        ),
    }


@pytest.mark.parametrize(
    "case",
    ["zip değil", "app.db yok", "yalnızca ../app.db", "db değil", "başka uygulamanın db'si", "geçersiz profil"],
)
def test_invalid_backup_is_rejected_and_nothing_changes(case, home, file_engine, tmp_path):
    invalid_dir = tmp_path / "gecersiz"
    invalid_dir.mkdir()
    zip_path = _invalid_backups(invalid_dir)[case]
    _add_company(file_engine, "mevcut.com")
    _write_profile(home["profile"], "Mevcut Ad")
    profile_before = home["profile"].read_bytes()
    dispose = _DisposeSpy(file_engine)

    with pytest.raises(BackupError):
        restore_backup(
            zip_path, home["db"], home["profile"], home["backups"],
            dispose_engine=dispose, now=NOW,
        )

    assert dispose.calls == 0
    assert _domains(file_engine) == ["mevcut.com"]
    assert home["profile"].read_bytes() == profile_before
    assert list_backups(home["backups"]) == []  # doğrulama geçmeden güvenlik yedeği bile alınmaz
    assert not (tmp_path / "app.db").exists()  # "../app.db" hiçbir yere çıkarılmadı
