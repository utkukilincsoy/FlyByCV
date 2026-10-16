"""`src/core/data_migration.py` testleri.

Gerçek `data/` klasörüne ve gerçek `%APPDATA%`'ya HİÇ dokunulmaz -- her test kendi
`tmp_path` altında sahte kaynak/hedef klasörleri kurar.
"""
import sqlite3
from pathlib import Path

from sqlmodel import Session, create_engine, select

from src.core.data_migration import (
    LEGACY_SOURCE_ENV_VAR,
    LegacySource,
    PROFILE_FILE_NAME,
    find_legacy_source,
    is_first_launch,
    legacy_source_override,
    migrate_legacy_data,
)
from src.core.db import configure_sqlite_engine, init_db
from src.core.models import Application, Company


def _make_legacy_data_dir(root: Path) -> Path:
    """`app.db` GERÇEK bir sqlite dosyası olmalı -- `migrate_legacy_data` artık onu
    `sqlite3`in yedekleme API'siyle kopyalıyor (bkz. `_copy_sqlite_database`), rastgele
    baytlarla "file is not a database" hatası verir."""
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    (data_dir / PROFILE_FILE_NAME).write_text('{"ad": "test"}', encoding="utf-8")
    connection = sqlite3.connect(str(data_dir / "app.db"))
    connection.execute("CREATE TABLE deneme (deger TEXT)")
    connection.execute("INSERT INTO deneme (deger) VALUES ('ornek-satir')")
    connection.commit()
    connection.close()
    return data_dir


def _read_deneme_rows(db_path: Path) -> list[tuple]:
    connection = sqlite3.connect(str(db_path))
    rows = connection.execute("SELECT deger FROM deneme").fetchall()
    connection.close()
    return rows


def _make_legacy_source(root: Path) -> LegacySource:
    data_dir = _make_legacy_data_dir(root)
    return LegacySource(data_dir=data_dir, output_dir=root / "output")


def _make_source_installation(root: Path) -> LegacySource:
    """Gerçek `SQLModel` şemasıyla bir `app.db` + bir üretilmiş CV içeren eski kurulum."""
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    (data_dir / PROFILE_FILE_NAME).write_text("{}", encoding="utf-8")

    output_dir = root / "output"
    company_dir = output_dir / "ornek-sirket"
    company_dir.mkdir(parents=True)
    pdf_path = company_dir / "Ad_Soyad_CV.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 sahte")

    engine = create_engine(f"sqlite:///{data_dir / 'app.db'}")
    configure_sqlite_engine(engine)
    init_db(engine=engine)
    with Session(engine) as session:
        company = Company(name="Örnek A.Ş.", domain="ornek-sirket.com", source="manual")
        session.add(company)
        session.flush()
        application = Application(company_id=company.id, cv_pdf_path=str(pdf_path))
        session.add(application)
        session.commit()
    engine.dispose()

    return LegacySource(data_dir=data_dir, output_dir=output_dir)


def _read_applications(db_path: Path) -> list[Application]:
    engine = create_engine(f"sqlite:///{db_path}")
    configure_sqlite_engine(engine)
    with Session(engine) as session:
        applications = session.exec(select(Application)).all()
    engine.dispose()
    return applications


# -- migrate_legacy_data: temel kopyalama -----------------------------------------------


def test_migrate_copies_all_files_preserving_structure(tmp_path):
    source = _make_legacy_source(tmp_path / "eski-proje")
    (source.data_dir / "alt_klasor").mkdir()
    (source.data_dir / "alt_klasor" / "not.txt").write_text("iç içe dosya", encoding="utf-8")
    dest_data_dir = tmp_path / "yeni-appdata" / "data"
    dest_output_dir = tmp_path / "yeni-appdata" / "output"

    result = migrate_legacy_data(source, dest_data_dir, dest_output_dir)

    assert result.copied_count == 3
    assert result.conflicts == []
    assert (dest_data_dir / PROFILE_FILE_NAME).read_text(encoding="utf-8") == '{"ad": "test"}'
    assert _read_deneme_rows(dest_data_dir / "app.db") == [("ornek-satir",)]
    assert (dest_data_dir / "alt_klasor" / "not.txt").read_text(encoding="utf-8") == "iç içe dosya"


def test_migrate_never_touches_or_deletes_source(tmp_path):
    source = _make_legacy_source(tmp_path / "eski-proje")
    original_files = sorted(p.name for p in source.data_dir.iterdir())
    dest_data_dir = tmp_path / "yeni-appdata" / "data"
    dest_output_dir = tmp_path / "yeni-appdata" / "output"

    migrate_legacy_data(source, dest_data_dir, dest_output_dir)

    assert sorted(p.name for p in source.data_dir.iterdir()) == original_files
    assert (source.data_dir / PROFILE_FILE_NAME).read_text(encoding="utf-8") == '{"ad": "test"}'


def test_migrate_missing_source_does_nothing(tmp_path):
    source = LegacySource(
        data_dir=tmp_path / "hic-olmayan-klasor", output_dir=tmp_path / "hic-olmayan-output"
    )
    dest_data_dir = tmp_path / "yeni-appdata" / "data"
    dest_output_dir = tmp_path / "yeni-appdata" / "output"

    result = migrate_legacy_data(source, dest_data_dir, dest_output_dir)

    assert result.copied == []
    assert result.conflicts == []
    assert not dest_data_dir.exists()


def test_migrate_missing_output_dir_only_copies_data(tmp_path):
    """`output/` hiç oluşmamışsa (hiç başvuru hazırlanmamış) sorun çıkarmadan atlanmalı."""
    source = _make_legacy_source(tmp_path / "eski-proje")
    assert not source.output_dir.exists()
    dest_data_dir = tmp_path / "yeni-appdata" / "data"
    dest_output_dir = tmp_path / "yeni-appdata" / "output"

    result = migrate_legacy_data(source, dest_data_dir, dest_output_dir)

    assert result.copied_count == 2
    assert not dest_output_dir.exists()


# -- 1) sqlite3 backup API: WAL'daki commit edilmiş satırlar kaybolmamalı ---------------


def test_migrate_copies_wal_committed_rows_not_yet_checkpointed(tmp_path):
    """Ham bir bayt kopyası, henüz `-wal`den ana dosyaya "checkpoint" edilmemiş bir satırı
    kaybederdi -- `_copy_sqlite_database` bunun yerine `sqlite3`in çevrimiçi yedekleme
    API'sini kullanmalı, bu satırı da yakalamalı.

    Gerçek şema (`init_db`) kullanılıyor -- `_rewrite_cv_pdf_paths`in kopyadan SONRA
    çalıştırdığı `application` sorgusu gerçek bir eski kurulumda her zaman var olan bu
    tabloyu bekliyor.
    """
    data_dir = tmp_path / "eski-proje" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / PROFILE_FILE_NAME).write_text("{}", encoding="utf-8")
    source_db = data_dir / "app.db"

    # `configure_sqlite_engine` WAL modunu zaten AÇAR (Faz 6.2 kararı, gerçek uygulama
    # her zaman WAL kullanıyor) -- bu yüzden burada ayrıca `PRAGMA journal_mode=WAL`
    # çağırmaya gerek yok.
    engine = create_engine(f"sqlite:///{source_db}")
    configure_sqlite_engine(engine)
    init_db(engine=engine)

    # Şema hazır olduktan SONRA, motoru KAPATMADAN (`engine.dispose()` ÇAĞIRMADAN) ham bir
    # `sqlite3` bağlantısıyla bir satır ekleniyor: motorun havuzdaki bağlantısı hâlâ açık
    # olduğu için bu, "veritabanına son açık bağlantı kapandığında otomatik checkpoint"
    # tetiklenmeden, satırın yalnızca `app.db-wal`de kalmasını sağlıyor.
    writer = sqlite3.connect(str(source_db))
    writer.execute(
        "INSERT INTO company (name, domain, source, created_at) "
        "VALUES ('Wal A.Ş.', 'wal.com', 'manual', datetime('now'))"
    )
    writer.commit()
    assert (data_dir / "app.db-wal").exists()

    source = LegacySource(data_dir=data_dir, output_dir=tmp_path / "eski-proje" / "output")
    dest_data_dir = tmp_path / "yeni-appdata" / "data"
    dest_output_dir = tmp_path / "yeni-appdata" / "output"

    result = migrate_legacy_data(source, dest_data_dir, dest_output_dir)
    writer.close()
    engine.dispose()

    assert not result.aborted_due_to_conflicts
    dest_db = dest_data_dir / "app.db"
    reader = sqlite3.connect(str(dest_db))
    rows = reader.execute("SELECT name, domain FROM company").fetchall()
    reader.close()

    assert rows == [("Wal A.Ş.", "wal.com")]
    # WAL yardımcı dosyaları kopyalanmamalı -- tek başlarına anlamsızlar, backup API
    # onlara ihtiyaç duymadan tek bir tutarlı dosya üretir.
    assert not (dest_data_dir / "app.db-wal").exists()
    assert not (dest_data_dir / "app.db-shm").exists()


# -- 2) Application.cv_pdf_path yeni output_dir()'e yeniden yazılmalı -------------------


def test_migrate_copies_output_and_rewrites_cv_pdf_path(tmp_path):
    source = _make_source_installation(tmp_path / "eski-proje")
    dest_data_dir = tmp_path / "yeni-appdata" / "data"
    dest_output_dir = tmp_path / "yeni-appdata" / "output"

    result = migrate_legacy_data(source, dest_data_dir, dest_output_dir)

    assert not result.aborted_due_to_conflicts
    expected_pdf = dest_output_dir / "ornek-sirket" / "Ad_Soyad_CV.pdf"
    assert expected_pdf.exists()
    assert expected_pdf.read_bytes() == b"%PDF-1.4 sahte"

    assert result.rewritten_cv_paths == 1
    applications = _read_applications(dest_data_dir / "app.db")
    assert len(applications) == 1
    assert applications[0].cv_pdf_path == str(expected_pdf)


# -- 3) Herhangi bir çakışma varsa HİÇBİR ŞEY kopyalanmamalı -----------------------------


def test_migrate_aborts_entirely_on_any_destination_conflict(tmp_path):
    source = _make_legacy_source(tmp_path / "eski-proje")
    dest_data_dir = tmp_path / "yeni-appdata" / "data"
    dest_output_dir = tmp_path / "yeni-appdata" / "output"
    dest_data_dir.mkdir(parents=True)
    (dest_data_dir / PROFILE_FILE_NAME).write_text('{"ad": "zaten-vardi"}', encoding="utf-8")

    result = migrate_legacy_data(source, dest_data_dir, dest_output_dir)

    assert result.aborted_due_to_conflicts is True
    assert result.copied == []
    assert dest_data_dir / PROFILE_FILE_NAME in result.conflicts
    # Çakışan dosya HİÇ dokunulmadan aynı kalmalı.
    assert (dest_data_dir / PROFILE_FILE_NAME).read_text(encoding="utf-8") == '{"ad": "zaten-vardi"}'
    # Çakışmayan app.db bile kopyalanmamalı -- taşımanın TAMAMI iptal edilir.
    assert not (dest_data_dir / "app.db").exists()


def test_migrate_conflict_in_output_also_aborts_data_copy(tmp_path):
    """Çakışma `output/` altında olsa bile `data/`daki HİÇBİR dosya kopyalanmamalı --
    kontrol iki klasörü BİRLİKTE, atomik olarak değerlendiriyor."""
    source = _make_source_installation(tmp_path / "eski-proje")
    dest_data_dir = tmp_path / "yeni-appdata" / "data"
    dest_output_dir = tmp_path / "yeni-appdata" / "output"
    conflict_path = dest_output_dir / "ornek-sirket" / "Ad_Soyad_CV.pdf"
    conflict_path.parent.mkdir(parents=True)
    conflict_path.write_bytes(b"baska-bir-pdf")

    result = migrate_legacy_data(source, dest_data_dir, dest_output_dir)

    assert result.aborted_due_to_conflicts is True
    assert conflict_path in result.conflicts
    assert not dest_data_dir.exists()
    assert conflict_path.read_bytes() == b"baska-bir-pdf"


# -- find_legacy_source / is_first_launch / legacy_source_override ----------------------


def test_find_legacy_source_accepts_data_folder_itself(tmp_path):
    data_dir = _make_legacy_data_dir(tmp_path / "proje")
    source = find_legacy_source(data_dir)
    assert source == LegacySource(data_dir=data_dir, output_dir=tmp_path / "proje" / "output")


def test_find_legacy_source_accepts_project_root(tmp_path):
    project_root = tmp_path / "proje"
    data_dir = _make_legacy_data_dir(project_root)
    source = find_legacy_source(project_root)
    assert source == LegacySource(data_dir=data_dir, output_dir=project_root / "output")


def test_find_legacy_source_rejects_wrong_folder(tmp_path):
    (tmp_path / "alakasiz.txt").write_text("x", encoding="utf-8")
    assert find_legacy_source(tmp_path) is None


def test_is_first_launch_true_when_profile_missing(tmp_path):
    assert is_first_launch(tmp_path / "does-not-exist") is True


def test_is_first_launch_false_when_profile_present(tmp_path):
    data_dir = _make_legacy_data_dir(tmp_path)
    assert is_first_launch(data_dir) is False


def test_legacy_source_override_reads_env_var(monkeypatch, tmp_path):
    monkeypatch.delenv(LEGACY_SOURCE_ENV_VAR, raising=False)
    assert legacy_source_override() is None

    data_dir = tmp_path / "proje" / "data"
    monkeypatch.setenv(LEGACY_SOURCE_ENV_VAR, str(data_dir))
    assert legacy_source_override() == LegacySource(
        data_dir=data_dir, output_dir=tmp_path / "proje" / "output"
    )
