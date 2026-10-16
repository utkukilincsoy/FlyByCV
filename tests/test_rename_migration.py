"""`src/core/rename_migration.py` testleri: `%APPDATA%\\CV Autopilot` -> `%APPDATA%\\FlybyCV` kopyası.

Gerçek `%APPDATA%`'ya HİÇ dokunulmaz: her test `tmp_path` altında sahte eski/yeni klasör kurar.
"""
import logging
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from src.core import paths, rename_migration
from src.core.db import configure_sqlite_engine, init_db
from src.core.models import Application, Company
from src.core.rename_migration import STAGING_SUFFIX, migrate_legacy_home, should_migrate


def _make_legacy_home(root: Path) -> Path:
    """Gerçek şemalı `app.db` + profil + üretilmiş CV + yedek + log içeren eski veri klasörü."""
    pdf = root / "output" / "ornek-sirket" / "Ad_Soyad_CV.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4 sahte")
    data = root / "data"
    (data / "backups").mkdir(parents=True)
    (data / "master_profile.json").write_text('{"ad": "test"}', encoding="utf-8")
    (data / "backups" / "cv-autopilot-yedek-20261001-090000-otomatik.zip").write_bytes(b"zip")
    (root / "logs").mkdir()
    (root / "logs" / "cv_autopilot.log").write_text("eski log", encoding="utf-8")

    engine = create_engine(f"sqlite:///{data / 'app.db'}")
    configure_sqlite_engine(engine)
    init_db(engine=engine)
    with Session(engine) as session:
        company = Company(name="Örnek A.Ş.", domain="ornek-sirket.com", source="manual")
        session.add(company)
        session.flush()
        session.add(Application(company_id=company.id, cv_pdf_path=str(pdf)))
        session.commit()
    engine.dispose()
    return root


def _cv_paths(db_path: Path) -> list[str]:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with Session(engine) as session:
            return [app.cv_pdf_path for app in session.exec(select(Application)).all()]
    finally:
        engine.dispose()


def _all_files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_copies_everything_and_keeps_the_old_folder(tmp_path):
    legacy = _make_legacy_home(tmp_path / "CV Autopilot")
    before = _all_files(legacy)
    new = tmp_path / "FlybyCV"

    result = migrate_legacy_home(legacy, new)

    assert result is not None
    assert _all_files(new) == before
    assert result.copied_files == len(before)
    assert _all_files(legacy) == before  # eski klasör yerinde, eksiksiz
    assert (new / "data" / "master_profile.json").read_text(encoding="utf-8") == '{"ad": "test"}'
    assert not new.with_name(new.name + STAGING_SUFFIX).exists()


def test_cv_paths_in_the_copied_database_point_to_the_new_folder(tmp_path):
    legacy = _make_legacy_home(tmp_path / "CV Autopilot")
    new = tmp_path / "FlybyCV"

    result = migrate_legacy_home(legacy, new)

    expected = new / "output" / "ornek-sirket" / "Ad_Soyad_CV.pdf"
    assert result.rewritten_cv_paths == 1
    assert _cv_paths(new / "data" / "app.db") == [str(expected)]
    assert expected.is_file()
    # Eski veritabanı değişmedi.
    assert _cv_paths(legacy / "data" / "app.db") == [str(legacy / "output" / "ornek-sirket" / "Ad_Soyad_CV.pdf")]


def test_does_nothing_when_the_new_folder_already_has_data(tmp_path):
    legacy = _make_legacy_home(tmp_path / "CV Autopilot")
    new = tmp_path / "FlybyCV"
    (new / "data").mkdir(parents=True)
    (new / "data" / "master_profile.json").write_text('{"ad": "yeni"}', encoding="utf-8")

    assert migrate_legacy_home(legacy, new) is None
    assert _all_files(new) == {"data/master_profile.json"}
    assert (new / "data" / "master_profile.json").read_text(encoding="utf-8") == '{"ad": "yeni"}'


def test_an_empty_new_folder_does_not_block_the_copy(tmp_path):
    legacy = _make_legacy_home(tmp_path / "CV Autopilot")
    new = tmp_path / "FlybyCV"
    (new / "logs").mkdir(parents=True)  # boş klasörler: henüz hiçbir dosya yazılmamış

    assert migrate_legacy_home(legacy, new) is not None
    assert (new / "data" / "app.db").is_file()


def test_does_nothing_without_a_legacy_folder(tmp_path):
    assert should_migrate(tmp_path / "CV Autopilot", tmp_path / "FlybyCV") is False
    assert migrate_legacy_home(tmp_path / "CV Autopilot", tmp_path / "FlybyCV") is None
    assert not (tmp_path / "FlybyCV").exists()


def test_a_failed_copy_leaves_no_half_new_folder(tmp_path, monkeypatch):
    legacy = _make_legacy_home(tmp_path / "CV Autopilot")
    new = tmp_path / "FlybyCV"

    def boom(*args, **kwargs):
        raise OSError("disk dolu")

    with monkeypatch.context() as patch:
        patch.setattr(rename_migration.shutil, "copy2", boom)
        with pytest.raises(OSError):
            migrate_legacy_home(legacy, new)

    assert not new.exists()
    assert not new.with_name(new.name + STAGING_SUFFIX).exists()
    assert migrate_legacy_home(legacy, new) is not None  # sonraki açılışta baştan denenir


def test_leftover_staging_folder_from_a_crash_is_replaced(tmp_path):
    legacy = _make_legacy_home(tmp_path / "CV Autopilot")
    new = tmp_path / "FlybyCV"
    staging = new.with_name(new.name + STAGING_SUFFIX)
    staging.mkdir()
    (staging / "yarim.txt").write_text("x", encoding="utf-8")

    migrate_legacy_home(legacy, new)

    assert not (new / "yarim.txt").exists()
    assert not staging.exists()


# ---- açılış ---------------------------------------------------------------------------------


@pytest.fixture
def frozen_appdata(monkeypatch, tmp_path) -> Path:
    monkeypatch.delenv("FLYBYCV_HOME", raising=False)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_startup_copies_the_old_appdata_folder_when_frozen(frozen_appdata, caplog):
    _make_legacy_home(frozen_appdata / "CV Autopilot")

    outcome = rename_migration.run_startup_migration()
    with caplog.at_level(logging.INFO):
        rename_migration.log_outcome(outcome)

    assert outcome.error is None and outcome.result is not None
    assert (frozen_appdata / "FlybyCV" / "data" / "app.db").is_file()
    assert (frozen_appdata / "CV Autopilot" / "data" / "app.db").is_file()
    assert "Eski klasör silinmedi" in caplog.text


def test_startup_does_nothing_in_development(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "frozen", False, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    _make_legacy_home(tmp_path / "CV Autopilot")

    assert rename_migration.run_startup_migration() == rename_migration.StartupMigration()
    assert not (tmp_path / "FlybyCV").exists()


@pytest.mark.parametrize("variable", ["FLYBYCV_HOME", "CV_AUTOPILOT_HOME"])
def test_startup_does_nothing_with_a_home_override(frozen_appdata, monkeypatch, variable):
    _make_legacy_home(frozen_appdata / "CV Autopilot")
    monkeypatch.setenv(variable, str(frozen_appdata / "ozel"))

    assert rename_migration.run_startup_migration().result is None
    assert not (frozen_appdata / "FlybyCV").exists()


def test_startup_reports_errors_instead_of_crashing(frozen_appdata, monkeypatch, caplog):
    _make_legacy_home(frozen_appdata / "CV Autopilot")
    def denied(*args):
        raise OSError("izin yok")

    monkeypatch.setattr(rename_migration, "_copy_tree", denied)

    outcome = rename_migration.run_startup_migration()
    with caplog.at_level(logging.ERROR):
        rename_migration.log_outcome(outcome)

    assert isinstance(outcome.error, OSError)
    assert "kopyalanamadı" in caplog.text
    assert not (frozen_appdata / "FlybyCV").exists()
