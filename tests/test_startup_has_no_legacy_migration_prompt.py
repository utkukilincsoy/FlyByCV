"""Adım 10.9b: açılış akışında "Eski veriyi taşı" penceresi YOK.

Paketlenmiş .exe'nin ilk açılışta eski `data/` klasörünü soran penceresi kaldırıldı (tek eski
kullanıcı zaten taşıdı); taze kurulum doğrudan kurulum sihirbazına gider (`src/ui/startup.py`).
"""
import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_main_does_not_offer_legacy_data_migration():
    text = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

    assert "migration_dialog" not in text
    assert "offer_legacy_data_migration" not in text
    assert "is_first_launch" not in text


def test_migration_dialog_module_is_gone():
    assert importlib.util.find_spec("src.ui.migration_dialog") is None
    assert not (PROJECT_ROOT / "src" / "ui" / "migration_dialog.py").exists()


def test_data_migration_core_is_kept_for_the_settings_migration():
    """`settings_migration` güvenli SQLite kopyası için hâlâ `data_migration`'a dayanıyor."""
    from src.core import data_migration, settings_migration

    assert settings_migration._copy_sqlite_database is data_migration._copy_sqlite_database
