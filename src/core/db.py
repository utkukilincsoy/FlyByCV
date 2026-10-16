import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from src.core import models  # noqa: F401 - tabloları metadata'ya kaydeder
from src.core import paths

logger = logging.getLogger(__name__)

_engine: Optional[Engine] = None


def default_database_url() -> str:
    """Veritabanı dosyasının bağlantı adresi.

    Sabit bir modül seviyesi değişken DEĞİL: `src.core.paths.db_path()` (dolayısıyla
    `FLYBYCV_HOME`/`sys.frozen`) her çağrıda okunur, testler ortam değişkenini
    import'tan SONRA değiştirebilir.
    """
    return f"sqlite:///{paths.db_path()}"


def configure_sqlite_engine(engine: Engine) -> None:
    """SQLite'ı yabancı anahtar denetimi AÇIK ve eşzamanlı yazıma dayanıklı hale getirir.

    `PRAGMA foreign_keys=ON`: SQLite varsayılan olarak FK kısıtlarını kontrol etmez; bu
    olmadan ilişkili kayıtları silme/ekleme testleri anlamsız olur.

    `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000`: Faz 6.2'nin gönderim işçisi
    (`src/sender/worker.py`) gönderimler arası 3-12 dakika uyurken, kullanıcı BAŞKA bir
    pencereden `sending_paused` ayarını değiştirebilmeli (acil durdur). Varsayılan
    rollback-journal modunda açık bir okuma işlemi (SQLAlchemy'nin "autobegin"i sayesinde
    `get_setting` bile bir işlem açar) yazıcıyı bloke edebilir ve "database is locked" hatası
    verir. WAL bir yazar + çok okuyucuya izin verir (okuyucu yazarı bloke etmez);
    `busy_timeout` da iki yazarın çakıştığı kısa anı bekleyerek çözer — ikisi birlikte gerekli.
    Bellek içi test veritabanında (`sqlite://`) `journal_mode=WAL` sessizce `"memory"` döner,
    hiçbir testi bozmaz (bkz. `tests/test_db.py`).
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def get_engine(database_url: Optional[str] = None) -> Engine:
    """Uygulamanın tekil (singleton) veritabanı motorunu döndürür."""
    global _engine
    url = database_url or default_database_url()
    if _engine is None or str(_engine.url) != url:
        paths.db_path().parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})
        configure_sqlite_engine(engine)
        _engine = engine
    return _engine


def ensure_columns(engine: Optional[Engine] = None) -> None:
    """Var olan tablolarda eksik olan model kolonlarını `ALTER TABLE ... ADD COLUMN` ile ekler.

    `SQLModel.metadata.create_all()` yalnızca hiç olmayan TABLOLARI oluşturur, var olan bir
    tabloya sonradan eklenen kolonu eklemez (bkz. PLAN.md Faz 4 notu — bu yüzden önceden
    `data/app.db` elle silinip yeniden oluşturuluyordu). `init_db()` bunu `create_all`'dan
    hemen sonra çağırır; veri SİLİNMEZ, yalnızca eksik kolon eklenir.

    Yalnızca `None` olabilen (nullable) kolonlar otomatik eklenir: SQLite'ta `ALTER TABLE ADD
    COLUMN` bir `NOT NULL` kolonu ancak sabit bir `DEFAULT` ile kabul eder, ve var olan
    satırlara ne yazılacağına burada güvenle karar verilemez. Model NOT NULL bir kolon
    eksik bırakırsa (yeni bir tabloya değil, VAR OLAN bir tabloya), bu daha büyük, elle
    yapılması gereken bir şema göçüne işaret eder — sessizce atlanmaz, hata fırlatılır.
    """
    target = engine or get_engine()
    inspector = inspect(target)
    existing_tables = set(inspector.get_table_names())

    with target.begin() as connection:
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # yeni tablo, create_all zaten oluşturdu
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                if not column.nullable:
                    raise RuntimeError(
                        f"{table.name}.{column.name} zorunlu (NOT NULL) ve veritabanında "
                        "eksik; ensure_columns bunu otomatik ekleyemez, elle bir şema göçü "
                        "gerekiyor."
                    )
                ddl_type = column.type.compile(dialect=target.dialect)
                connection.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}')
                )
                logger.info(f"{table.name} tablosuna eksik kolon eklendi: {column.name}")


def sqlite_file_path(engine: Engine) -> Optional[Path]:
    """Motorun DOSYA tabanlı bir SQLite veritabanına mı bağlı olduğunu söyler (bellek içi
    test motorlarında `None` — yedeklemeye gerek yok, bkz. `settings_migration.py` ve
    `core/backup.py`: Ayarlar'daki yedek düğmeleri de buna bakar, testler gerçek `data/`'ya
    hiç ulaşamaz)."""
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    return Path(database)


def init_db(engine: Optional[Engine] = None) -> None:
    """Henüz var olmayan tabloları oluşturur, var olanlardaki eksik kolonları tamamlar,
    ayar şeması göçlerini çalıştırır (`settings_migration.run_settings_migrations` — Adım
    10.1b/10.1c; idempotent, her çağrıda güvenli)."""
    target = engine or get_engine()
    SQLModel.metadata.create_all(target)
    ensure_columns(target)

    # Yerel (fonksiyon içi) import: settings_migration -> data_migration -> db.py döngüsel
    # importunu kırar (data_migration zaten `configure_sqlite_engine`'i buradan alıyor).
    from src.core.settings_migration import run_settings_migrations

    with get_session(engine=target) as session:
        run_settings_migrations(session, db_path=sqlite_file_path(target))


@contextmanager
def get_session(engine: Optional[Engine] = None) -> Iterator[Session]:
    """Bir veritabanı oturumu (Session) döndürür. Commit çağırana aittir.

    `expire_on_commit` BİLİNÇLİ olarak varsayılanında (`True`) bırakılır ve elle
    `False` yapılmamalı: `commit()` sonrası nesnelerin "expired" olması, gönderim
    işçisinin (`src/sender/worker.py`) `sending_paused` gibi ayarları her commit'ten
    sonra TAZE okumasının dayandığı mekanizmadır — `False` yapılırsa acil durdurun başka
    bir pencereden görülmesi kırılır.
    """
    with Session(engine or get_engine()) as session:
        yield session
