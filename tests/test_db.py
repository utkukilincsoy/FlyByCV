import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from src.core.db import configure_sqlite_engine, ensure_columns, init_db


def _pragma(engine, name: str):
    with engine.connect() as connection:
        return connection.execute(text(f"PRAGMA {name}")).scalar()


def test_memory_engine_tolerates_wal_pragma():
    """Bellek içi veritabanı WAL isteğini sessizce yok sayar (hata vermez)."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    configure_sqlite_engine(engine)
    init_db(engine=engine)

    assert _pragma(engine, "journal_mode") == "memory"
    assert _pragma(engine, "busy_timeout") == 5000
    assert _pragma(engine, "foreign_keys") == 1


def test_file_engine_uses_wal_and_busy_timeout(tmp_path):
    """Dosya tabanlı veritabanında WAL kalıcı olarak açılmalı."""
    db_path = tmp_path / "app.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    configure_sqlite_engine(engine)
    init_db(engine=engine)

    assert _pragma(engine, "journal_mode") == "wal"
    assert _pragma(engine, "busy_timeout") == 5000
    assert _pragma(engine, "foreign_keys") == 1


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as connection:
        return {row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))}


def test_ensure_columns_adds_missing_columns_without_losing_data(tmp_path):
    """Faz 7 öncesi (eski) şemalı bir emailreply tablosuna yeni kolonlar eklenmeli, var olan
    satır SİLİNMEDEN/BOZULMADAN kalmalı — data/app.db elle silmeye gerek kalmamalı."""
    db_path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE emailreply ("
                "id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, "
                "from_addr TEXT NOT NULL, subject TEXT, snippet TEXT, "
                "received_at TEXT NOT NULL, category TEXT NOT NULL, "
                "classified_by TEXT NOT NULL, message_id TEXT NOT NULL UNIQUE)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO emailreply "
                "(id, application_id, from_addr, received_at, category, classified_by, message_id) "
                "VALUES (1, 1, 'ik@acme.com', '2026-01-01T00:00:00', 'pending', 'keyword', 'msg-1')"
            )
        )

    init_db(engine=engine)  # create_all (eksik tabloları açar) + ensure_columns

    columns = _columns(engine, "emailreply")
    assert {"in_reply_to", "body_preview", "matched_by", "auto_category", "updated_at"} <= columns

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT from_addr, category, message_id FROM emailreply WHERE id = 1")
        ).fetchone()
    assert row == ("ik@acme.com", "pending", "msg-1")

    ensure_columns(engine=engine)  # ikinci çağrı idempotent olmalı, hata vermemeli


def test_ensure_columns_adds_application_bounced_at(tmp_path):
    """application tablosunda da aynı mekanizma çalışmalı (bounced_at eklenmeli)."""
    db_path = tmp_path / "old_app.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE application ("
                "id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL UNIQUE, "
                "status TEXT NOT NULL, tailored BOOLEAN NOT NULL, matched_tags JSON, "
                "cv_pdf_path TEXT, mail_subject TEXT, mail_body TEXT, message_id TEXT, "
                "created_at TEXT NOT NULL, sent_at TEXT, error TEXT)"
            )
        )

    init_db(engine=engine)

    assert "bounced_at" in _columns(engine, "application")


def test_ensure_columns_adds_autopilot_columns(tmp_path):
    """Faz 9 için eklenen `company.crawled_at`/`application.test_sent_at` da aynı yolla eklenmeli."""
    db_path = tmp_path / "old_autopilot.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE company ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL, website TEXT, "
                "domain TEXT NOT NULL UNIQUE, email TEXT, email_source TEXT, city TEXT, "
                "sector_tags JSON, about_text TEXT, source TEXT NOT NULL, mx_valid BOOLEAN, "
                "found_emails JSON, email_is_role BOOLEAN, created_at TEXT NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE application ("
                "id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL UNIQUE, "
                "status TEXT NOT NULL, tailored BOOLEAN NOT NULL, matched_tags JSON, "
                "cv_pdf_path TEXT, mail_subject TEXT, mail_body TEXT, message_id TEXT, "
                "created_at TEXT NOT NULL, sent_at TEXT, error TEXT, bounced_at TEXT)"
            )
        )

    init_db(engine=engine)

    assert "crawled_at" in _columns(engine, "company")
    assert "test_sent_at" in _columns(engine, "application")


def test_ensure_columns_raises_for_missing_not_null_column(tmp_path):
    """Eksik bir zorunlu (NOT NULL) kolon sessizce atlanmamalı, açıkça hata vermeli."""
    db_path = tmp_path / "broken.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)

    with engine.begin() as connection:
        # category (NOT NULL) eksik bırakıldı.
        connection.execute(
            text(
                "CREATE TABLE emailreply ("
                "id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL, "
                "from_addr TEXT NOT NULL, subject TEXT, snippet TEXT, "
                "received_at TEXT NOT NULL, "
                "classified_by TEXT NOT NULL, message_id TEXT NOT NULL UNIQUE)"
            )
        )

    with pytest.raises(RuntimeError, match="category"):
        ensure_columns(engine=engine)
