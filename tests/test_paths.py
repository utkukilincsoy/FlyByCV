"""`src/core/paths.py` testleri.

`importlib.reload` KULLANILMAZ: modüldeki hiçbir fonksiyon import anında bir şey
hesaplamıyor (yan etkisiz, saf), bu yüzden `monkeypatch` ile ortamı değiştirip
fonksiyonları doğrudan çağırmak yeterli -- gerçek davranışla (her çağrıda güncel
ortamı okuma) birebir örtüşüyor.
"""
from pathlib import Path

from src.core import paths


def test_dev_mode_uses_project_root(monkeypatch):
    """Geliştirmede (frozen değil, env override yok) her şey proje kökünde durur."""
    monkeypatch.delenv("FLYBYCV_HOME", raising=False)
    monkeypatch.setattr(paths.sys, "frozen", False, raising=False)

    project_root = Path(__file__).resolve().parent.parent
    assert paths.is_frozen() is False
    assert paths.app_home() == project_root
    assert paths.resource_dir() == project_root
    assert paths.data_dir() == project_root / "data"
    assert paths.output_dir() == project_root / "output"
    assert paths.templates_dir() == project_root / "templates"
    assert paths.profile_path() == project_root / "data" / "master_profile.json"
    assert paths.db_path() == project_root / "data" / "app.db"


def test_env_override_wins_even_when_frozen(monkeypatch, tmp_path):
    """`FLYBYCV_HOME` her koşulda (frozen dahil) diğer her şeyi ezer."""
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path))

    assert paths.app_home() == tmp_path
    assert paths.data_dir() == tmp_path / "data"
    assert paths.output_dir() == tmp_path / "output"
    assert paths.logs_dir() == tmp_path / "logs"
    assert paths.db_path() == tmp_path / "data" / "app.db"


def test_frozen_mode_uses_appdata(monkeypatch, tmp_path):
    """Paketlenmişken (env override yok) kişisel veri `%APPDATA%\\FlybyCV` altında olmalı."""
    monkeypatch.delenv("FLYBYCV_HOME", raising=False)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))

    expected_home = tmp_path / "FlybyCV"
    assert paths.app_home() == expected_home
    assert paths.data_dir() == expected_home / "data"
    assert paths.output_dir() == expected_home / "output"
    assert paths.logs_dir() == expected_home / "logs"


def test_legacy_env_var_still_works_as_a_fallback(monkeypatch, tmp_path):
    """Eski `CV_AUTOPILOT_HOME` yalnızca `FLYBYCV_HOME` yokken okunur."""
    monkeypatch.delenv("FLYBYCV_HOME", raising=False)
    monkeypatch.setenv("CV_AUTOPILOT_HOME", str(tmp_path / "eski"))

    assert paths.app_home() == tmp_path / "eski"
    assert paths.is_custom_home() is True

    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "yeni"))
    assert paths.app_home() == tmp_path / "yeni"


def test_legacy_app_home_is_the_old_appdata_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert paths.legacy_app_home() == tmp_path / "CV Autopilot"


def test_is_custom_home_false_without_override(monkeypatch):
    monkeypatch.delenv("FLYBYCV_HOME", raising=False)

    assert paths.is_custom_home() is False


def test_is_custom_home_true_for_a_different_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path))

    assert paths.is_custom_home() is True


def test_is_custom_home_false_when_override_is_the_default_folder(monkeypatch, tmp_path):
    """Varsayılan klasörü elle yazmak (farklı yazımla bile) ayrı kurulum değildir."""
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "x" / ".." / "FlybyCV"))

    assert paths.default_app_home() == tmp_path / "FlybyCV"
    assert paths.is_custom_home() is False


def test_frozen_mode_resource_dir_uses_meipass(monkeypatch, tmp_path):
    """Şablonlar gibi salt okunur kaynaklar `sys._MEIPASS` altından okunmalı, `%APPDATA%` DEĞİL."""
    monkeypatch.delenv("FLYBYCV_HOME", raising=False)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert paths.resource_dir() == tmp_path
    assert paths.templates_dir() == tmp_path / "templates"
    assert paths.profile_example_path() == tmp_path / "data" / "master_profile.example.json"


def test_no_directories_created_as_side_effect(monkeypatch, tmp_path):
    """Yol fonksiyonları klasör OLUŞTURMAZ -- yalnızca yol hesaplar (yan etkisiz)."""
    target = tmp_path / "does-not-exist-yet"
    monkeypatch.setenv("FLYBYCV_HOME", str(target))

    paths.data_dir()
    paths.output_dir()
    paths.logs_dir()
    paths.db_path()

    assert not target.exists()


def test_configure_playwright_browsers_path_noop_in_dev(monkeypatch):
    """Geliştirmede (frozen değil) ortam değişkenine hiç dokunulmaz."""
    monkeypatch.setattr(paths.sys, "frozen", False, raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    paths.configure_playwright_browsers_path()

    assert "PLAYWRIGHT_BROWSERS_PATH" not in paths.os.environ


def test_configure_playwright_browsers_path_sets_zero_when_frozen(monkeypatch):
    """Paketlenmişken `PLAYWRIGHT_BROWSERS_PATH=0` ayarlanır (gömülü Chromium).

    `os.environ.setdefault` doğrudan çağrıldığı için (`monkeypatch.setenv` DEĞİL)
    `monkeypatch` bu değişikliği kendiliğinden geri ALMAZ -- gerçek Playwright
    çağrılarını (ör. `tests/test_renderer.py`) etkileyecek kalıcı bir sızıntı
    olmasın diye `finally`de elle temizleniyor.
    """
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    try:
        paths.configure_playwright_browsers_path()
        assert paths.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "0"
    finally:
        paths.os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)


def test_configure_playwright_browsers_path_does_not_override_existing(monkeypatch):
    """Zaten ayarlı bir değer varsa (ör. testte) EZİLMEZ (`setdefault`)."""
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/custom/path")

    paths.configure_playwright_browsers_path()

    assert paths.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/custom/path"
