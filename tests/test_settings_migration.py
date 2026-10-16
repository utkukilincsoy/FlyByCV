"""src/core/settings_migration.py testleri (PLAN.md Faz 10 Adım 10.1b/10.1c/10.1f).

Gerçek `%APPDATA%`'ya hiç dokunulmaz (bkz. `test_data_migration.py`'nin aynı ilkesi) --
"var olan kullanıcı" senaryosu `tmp_path` altında SENTETİK ama gerçekçi bir veritabanıyla
kurulur: gerçek `app.db`de bulunanla aynı şekilde bazı ayarlar (`daily_limit`, `test_mode`, ...)
var ama `schema_version`/`autopilot_city`/`autopilot_queries` YOK (bkz. modül docstring'i).
Bu şekil, göç yazılmadan önce gerçek `app.db`nin bir kopyası üzerinde elle doğrulandı
(10.1f) -- her makinede farklı olan gerçek dosyaya bağımlı bir test burada YOK.
"""
from pathlib import Path

from sqlmodel import create_engine

from src.core.db import configure_sqlite_engine, get_session, init_db
from src.core.models import Setting
from src.core.settings import (
    KEY_APPLICATION_TARGET,
    KEY_AUTOPILOT_CITY,
    KEY_AUTOPILOT_QUERIES,
    KEY_DAILY_LIMIT,
    KEY_ONBOARDING_COMPLETED,
    KEY_PROFESSION,
    KEY_SCHEMA_VERSION,
    KEY_TEST_MODE,
    KEY_USE_OSM,
    get_setting,
    set_setting,
)
from src.core.settings_migration import (
    CURRENT_SCHEMA_VERSION,
    LEGACY_APPLICATION_TARGET,
    LEGACY_AUTOPILOT_CITY_CODE,
    LEGACY_AUTOPILOT_QUERIES,
    LEGACY_PROFESSION,
    run_settings_migrations,
)


def _file_engine(tmp_path: Path, name: str = "app.db"):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False}
    )
    configure_sqlite_engine(engine)
    return engine


def _make_legacy_db(engine) -> None:
    """`init_db()`'nin taze kurulumda zaten yazdığı `schema_version`'ı siler ve gerçek
    `app.db`de bulunanla aynı şekilde birkaç ayar ekler -- "var olan kullanıcı" simülasyonu."""
    with get_session(engine=engine) as session:
        session.delete(session.get(Setting, KEY_SCHEMA_VERSION))
        set_setting(session, KEY_DAILY_LIMIT, 20)
        set_setting(session, KEY_TEST_MODE, True)
        session.commit()


# --- Taze kurulum: hiçbir Setting satırı yok -------------------------------------------------


def test_fresh_install_gets_generic_defaults_no_legacy_write(tmp_path):
    """Boş bir veritabanında göç yalnızca `schema_version` yazar; Ankara/eski aramalar YAZILMAZ."""
    engine = _file_engine(tmp_path)
    init_db(engine=engine)  # migration `init_db()` içinden otomatik çalışır

    with get_session(engine=engine) as session:
        assert get_setting(session, KEY_SCHEMA_VERSION) == CURRENT_SCHEMA_VERSION
        assert session.get(Setting, KEY_AUTOPILOT_CITY) is None  # satır hiç yazılmadı
        assert get_setting(session, KEY_AUTOPILOT_CITY) is None  # genel varsayılan
        assert get_setting(session, KEY_AUTOPILOT_QUERIES) == []

    assert list(tmp_path.glob("app.db.bak-*")) == []  # taze kurulumda yedek alınmaz


# --- Var olan kullanıcı: göç öncesi değerleri açıkça yazar, yedek alır -----------------------


def test_existing_user_gets_legacy_values_written_and_backed_up(tmp_path):
    engine = _file_engine(tmp_path)
    init_db(engine=engine)
    _make_legacy_db(engine)

    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=tmp_path / "app.db")

    with get_session(engine=engine) as session:
        # v1 "Ankara" yazar, v3 aynı göçte plaka koduna çevirir.
        assert get_setting(session, KEY_AUTOPILOT_CITY) == LEGACY_AUTOPILOT_CITY_CODE == "06"
        assert get_setting(session, KEY_AUTOPILOT_QUERIES) == LEGACY_AUTOPILOT_QUERIES
        assert get_setting(session, KEY_APPLICATION_TARGET) == LEGACY_APPLICATION_TARGET
        assert get_setting(session, KEY_PROFESSION) == LEGACY_PROFESSION
        assert get_setting(session, KEY_USE_OSM) is False  # var olan kullanıcıda OSM KAPALI
        assert get_setting(session, KEY_SCHEMA_VERSION) == CURRENT_SCHEMA_VERSION
        assert get_setting(session, KEY_DAILY_LIMIT) == 20  # var olan ayara dokunulmadı

    backups = list(tmp_path.glob("app.db.bak-*"))
    assert len(backups) == 1

    # Yedek göç ÖNCESİ durumu yansıtmalı (henüz autopilot_city/schema_version yokken).
    backup_engine = create_engine(
        f"sqlite:///{backups[0]}", connect_args={"check_same_thread": False}
    )
    with get_session(engine=backup_engine) as backup_session:
        assert backup_session.get(Setting, KEY_AUTOPILOT_CITY) is None
        assert backup_session.get(Setting, KEY_SCHEMA_VERSION) is None
        assert get_setting(backup_session, KEY_DAILY_LIMIT) == 20


# --- Tekrar çalıştırma: idempotent, kullanıcının SONRADAN yaptığı değişikliği EZMEZ -----------


def test_migration_is_idempotent_and_does_not_overwrite_later_user_change(tmp_path):
    engine = _file_engine(tmp_path)
    init_db(engine=engine)
    _make_legacy_db(engine)

    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=tmp_path / "app.db")

    # Kullanıcı göçten SONRA şehri kendisi değiştirir (ör. ileride bir Ayarlar formuyla).
    with get_session(engine=engine) as session:
        set_setting(session, KEY_AUTOPILOT_CITY, "İzmir")
        session.commit()

    backups_before = sorted(tmp_path.glob("app.db.bak-*"))

    # Uygulama tekrar açılır -- göç tekrar çağrılır (her `init_db()`'de olduğu gibi).
    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=tmp_path / "app.db")

    with get_session(engine=engine) as session:
        assert get_setting(session, KEY_AUTOPILOT_CITY) == "İzmir"  # ezilmedi
    assert sorted(tmp_path.glob("app.db.bak-*")) == backups_before  # yeni yedek alınmadı


# --- v1 -> v2 (Adım 10.4): daha önce v1'e göçmüş bir kullanıcı da hedef/mesleği kazanır -------


def test_user_already_on_v1_gets_v2_legacy_values_without_rewriting_v1(tmp_path):
    """v1'e daha önce göçmüş (autopilot_city zaten Ankara) bir kullanıcı: v2 göçü yalnızca
    application_target/profession'ı YAZAR, autopilot_city'ye TEKRAR dokunmaz."""
    engine = _file_engine(tmp_path)
    init_db(engine=engine)
    _make_legacy_db(engine)
    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=tmp_path / "app.db")  # v1'e göçer
        assert get_setting(session, KEY_SCHEMA_VERSION) == CURRENT_SCHEMA_VERSION

    # Kullanıcı şehri kendisi değiştirmiş olsun -- v2 göçü buna dokunmamalı.
    with get_session(engine=engine) as session:
        set_setting(session, KEY_AUTOPILOT_CITY, "İzmir")
        session.commit()

    with get_session(engine=engine) as session:
        assert get_setting(session, KEY_APPLICATION_TARGET) == LEGACY_APPLICATION_TARGET
        assert get_setting(session, KEY_PROFESSION) == LEGACY_PROFESSION
        assert get_setting(session, KEY_AUTOPILOT_CITY) == "İzmir"  # v1 adımı tekrar çalışmadı


def test_fresh_install_stuck_at_v1_with_no_other_rows_stays_fresh_at_v2(tmp_path):
    """`schema_version=1` yazılmış (ör. eski bir sürümün göçü) ama başka HİÇBİR gerçek ayar
    yoksa hâlâ taze kurulum sayılır -- v2'ye geçerken LEGACY hedef/meslek zorla yazılmaz
    (bkz. modül docstring'i, "var olan kullanıcı" tespiti `schema_version`'ı SAYMAZ)."""
    engine = _file_engine(tmp_path)
    init_db(engine=engine)  # taze kurulum: doğrudan CURRENT_SCHEMA_VERSION'a göçer

    with get_session(engine=engine) as session:
        assert session.get(Setting, KEY_AUTOPILOT_CITY) is None
        set_setting(session, KEY_SCHEMA_VERSION, 1)  # eski bir sürümde kalmış gibi geri al
        session.commit()

    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=tmp_path / "app.db")

    with get_session(engine=engine) as session:
        assert get_setting(session, KEY_SCHEMA_VERSION) == CURRENT_SCHEMA_VERSION
        assert session.get(Setting, KEY_APPLICATION_TARGET) is None
        assert session.get(Setting, KEY_PROFESSION) is None
        assert get_setting(session, KEY_APPLICATION_TARGET) is None  # genel varsayılan


# --- Bellek içi veritabanı: yedekleme atlanır, göç yine de çalışır ----------------------------


def test_migration_skips_backup_without_db_path(session):
    """`session` fixture'ı zaten `init_db` ile göçü bir kez çalıştırdı; "var olan kullanıcı"
    senaryosunu yeniden kurmak için `schema_version` satırı silinir."""
    session.delete(session.get(Setting, KEY_SCHEMA_VERSION))
    set_setting(session, KEY_TEST_MODE, True)
    session.commit()

    run_settings_migrations(session, db_path=None)  # bellek içi motor -- gerçek dosya yok

    assert get_setting(session, KEY_SCHEMA_VERSION) == CURRENT_SCHEMA_VERSION
    assert get_setting(session, KEY_AUTOPILOT_CITY) == LEGACY_AUTOPILOT_CITY_CODE  # yine de yazılır
    assert get_setting(session, KEY_APPLICATION_TARGET) == LEGACY_APPLICATION_TARGET


# --- Yedekleme başarısız olursa göç yine de tamamlanır -----------------------------------------


def test_backup_failure_does_not_block_migration(tmp_path, monkeypatch):
    engine = _file_engine(tmp_path)
    init_db(engine=engine)
    _make_legacy_db(engine)

    import src.core.settings_migration as settings_migration

    def _raise(*_args, **_kwargs):
        raise OSError("disk dolu")

    monkeypatch.setattr(settings_migration, "_copy_sqlite_database", _raise)

    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=tmp_path / "app.db")

    with get_session(engine=engine) as session:
        assert get_setting(session, KEY_SCHEMA_VERSION) == CURRENT_SCHEMA_VERSION
        assert get_setting(session, KEY_AUTOPILOT_CITY) == LEGACY_AUTOPILOT_CITY_CODE
    assert list(tmp_path.glob("app.db.bak-*")) == []  # yedek denemesi başarısız oldu, dosya yok


# --- v2 -> v3 (Adım 10.6c): şehir metni -> plaka kodu, OSM var olan kullanıcıda KAPALI ---------


def _make_v2_db(engine, *, city, extra=None) -> None:
    """Adım 10.6c'den ÖNCEKİ gerçek şekil: v2'ye göçmüş kullanıcı (şehir hâlâ metin, `use_osm` yok)."""
    with get_session(engine=engine) as session:
        set_setting(session, KEY_DAILY_LIMIT, 20)
        set_setting(session, KEY_AUTOPILOT_CITY, city)
        set_setting(session, KEY_PROFESSION, "yazilim")
        for key, value in (extra or {}).items():
            set_setting(session, key, value)
        set_setting(session, KEY_SCHEMA_VERSION, 2)
        session.commit()


def test_v2_user_ankara_becomes_06_and_osm_stays_off(tmp_path):
    engine = _file_engine(tmp_path)
    init_db(engine=engine)
    _make_v2_db(engine, city="Ankara")

    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=tmp_path / "app.db")

    with get_session(engine=engine) as session:
        assert get_setting(session, KEY_AUTOPILOT_CITY) == "06"
        assert get_setting(session, KEY_USE_OSM) is False
        assert get_setting(session, KEY_PROFESSION) == "yazilim"  # dokunulmadı
        assert get_setting(session, KEY_SCHEMA_VERSION) == CURRENT_SCHEMA_VERSION
    assert len(list(tmp_path.glob("app.db.bak-*"))) == 1


def test_v2_user_city_name_from_cities_file_maps_to_its_code(tmp_path):
    engine = _file_engine(tmp_path)
    init_db(engine=engine)
    _make_v2_db(engine, city="İzmir")

    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=None)

    with get_session(engine=engine) as session:
        assert get_setting(session, KEY_AUTOPILOT_CITY) == "35"


def test_v2_user_unknown_city_text_is_cleared(tmp_path):
    engine = _file_engine(tmp_path)
    init_db(engine=engine)
    _make_v2_db(engine, city="Atlantis")

    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=None)

    with get_session(engine=engine) as session:
        assert get_setting(session, KEY_AUTOPILOT_CITY) is None


def test_v2_user_already_using_city_code_is_untouched(tmp_path):
    engine = _file_engine(tmp_path)
    init_db(engine=engine)
    _make_v2_db(engine, city="34")

    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=None)

    with get_session(engine=engine) as session:
        assert get_setting(session, KEY_AUTOPILOT_CITY) == "34"


def test_v2_user_who_already_chose_osm_keeps_their_choice(tmp_path):
    engine = _file_engine(tmp_path)
    init_db(engine=engine)
    _make_v2_db(engine, city="Ankara", extra={KEY_USE_OSM: True})

    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=None)

    with get_session(engine=engine) as session:
        assert get_setting(session, KEY_USE_OSM) is True


def test_ankara_maps_to_06_even_if_cities_file_is_missing(tmp_path, monkeypatch):
    """Var olan kullanıcı: `presets/cities.json` okunamasa bile "Ankara" -> "06" çalışmalı."""
    monkeypatch.setattr("src.core.settings_migration.load_cities", lambda: {})
    engine = _file_engine(tmp_path)
    init_db(engine=engine)
    _make_v2_db(engine, city="Ankara")

    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=None)

    with get_session(engine=engine) as session:
        assert get_setting(session, KEY_AUTOPILOT_CITY) == "06"


def test_fresh_install_gets_osm_on_by_default_without_any_written_row(tmp_path):
    engine = _file_engine(tmp_path)
    init_db(engine=engine)

    with get_session(engine=engine) as session:
        assert session.get(Setting, KEY_USE_OSM) is None
        assert get_setting(session, KEY_USE_OSM) is True
        assert session.get(Setting, KEY_AUTOPILOT_CITY) is None


# --- v4 (Adım 10.2a): var olan kullanıcıda ilk açılış sihirbazı açılmaz ---------------------------


def test_existing_user_is_marked_onboarded_so_wizard_never_opens(tmp_path):
    from src.ui.onboarding_data import is_onboarding_needed

    engine = _file_engine(tmp_path)
    init_db(engine=engine)
    _make_legacy_db(engine)  # gerçek app.db'deki gibi: bazı ayarlar var, schema_version yok

    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=tmp_path / "app.db")

    with get_session(engine=engine) as session:
        assert get_setting(session, KEY_ONBOARDING_COMPLETED) is True
        assert is_onboarding_needed(session) is False


def test_existing_user_already_on_v3_gets_v4_without_rewriting_older_values(tmp_path):
    engine = _file_engine(tmp_path)
    init_db(engine=engine)
    _make_legacy_db(engine)
    with get_session(engine=engine) as session:
        set_setting(session, KEY_USE_OSM, True)  # v3 sonrası kullanıcı kararı
        set_setting(session, KEY_SCHEMA_VERSION, 3)
        session.commit()

    with get_session(engine=engine) as session:
        run_settings_migrations(session, db_path=tmp_path / "app.db")

    with get_session(engine=engine) as session:
        assert get_setting(session, KEY_ONBOARDING_COMPLETED) is True
        assert get_setting(session, KEY_USE_OSM) is True
        assert get_setting(session, KEY_SCHEMA_VERSION) == CURRENT_SCHEMA_VERSION == 4


def test_fresh_install_is_not_marked_onboarded(tmp_path):
    from src.ui.onboarding_data import is_onboarding_needed

    engine = _file_engine(tmp_path)
    init_db(engine=engine)

    with get_session(engine=engine) as session:
        assert session.get(Setting, KEY_ONBOARDING_COMPLETED) is None
        assert is_onboarding_needed(session) is True


def test_wizard_progress_rows_do_not_turn_fresh_install_into_existing_user(tmp_path):
    """Sihirbaz ara ayarlar yazdıktan sonra uygulama tekrar açılınca göç yeniden ÇALIŞMAZ
    (schema_version zaten güncel) -- yarım kalan kurulum 'var olan kullanıcı' sayılmaz."""
    from src.ui.onboarding_data import is_onboarding_needed, save_step

    engine = _file_engine(tmp_path)
    init_db(engine=engine)
    with get_session(engine=engine) as session:
        save_step(session, "cv")
        set_setting(session, KEY_DAILY_LIMIT, 10)
        session.commit()

    init_db(engine=engine)  # ikinci açılış

    with get_session(engine=engine) as session:
        assert is_onboarding_needed(session) is True
