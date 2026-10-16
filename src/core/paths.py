"""Uygulamanın kullandığı tüm yolların TEK kaynağı.

Geliştirmede (`python main.py`) veri hâlâ proje klasöründe (`data/`, `output/`) durur —
bu davranış DEĞİŞMEZ. Yalnızca PyInstaller ile paketlenip (.exe) çalıştırıldığında
(`sys.frozen`) kişisel veri `%APPDATA%\\FlybyCV` altına taşınır (Faz 8); .exe'nin
kendi paket klasörüne (`sys._MEIPASS`, her açılışta silinip yeniden açılan geçici bir
klasör) hiçbir kişisel veri yazılmaz.

Hiçbir fonksiyon burada klasör OLUŞTURMAZ (yan etkisiz, saf yol hesaplama) — ilgili
dosyayı yazacak kod (`db.py`, `renderer.py`, `smtp_sender.py`) kendi `mkdir`'ini zaten
yapıyor.

`FLYBYCV_HOME` ortam değişkeni (yoksa eski adı `CV_AUTOPILOT_HOME`) her iki modu da ezer:
testlerde ve .exe davranışını geliştirme ortamında denemek için.
"""
import os
import sys
from pathlib import Path
from typing import Optional

#: `src/core/paths.py` -> proje kökü (geliştirmede kullanılır).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

#: `%APPDATA%` altındaki veri klasörünün adı.
APP_DIR_NAME = "FlybyCV"

#: Uygulamanın eski adıyla kullanılan veri klasörü; yalnızca ilk açılış göçünde okunur
#: (`src/core/rename_migration.py`), içine hiçbir şey yazılmaz.
LEGACY_APP_DIR_NAME = "CV Autopilot"

#: Veri kökünü ezen ortam değişkeni; `LEGACY_HOME_ENV_VAR` (eski ad) yalnızca bu yoksa okunur.
HOME_ENV_VAR = "FLYBYCV_HOME"
LEGACY_HOME_ENV_VAR = "CV_AUTOPILOT_HOME"


def is_frozen() -> bool:
    """PyInstaller ile paketlenip paketlenmediğini söyler (`main.py --onedir` .exe'si)."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Yalnızca OKUNAN, uygulamayla birlikte gelen dosyaların kökü (şablonlar, örnek profil).

    Paketlenmişken PyInstaller'ın açtığı geçici/paket klasörü (`sys._MEIPASS`), aksi
    halde proje kökü. Kişisel veri BURAYA asla yazılmaz — .exe her kapanışta bu klasörü
    (onedir'de değil ama onefile'da) silebilir, ve zaten yazma izni garanti değildir.
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", _PROJECT_ROOT))
    return _PROJECT_ROOT


def _appdata_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"


def default_app_home() -> Path:
    """Ev ortam değişkeni YOKKEN kullanılan kök: paketlenmişse `%APPDATA%\\FlybyCV`,
    geliştirmede proje kökü."""
    if is_frozen():
        return _appdata_dir() / APP_DIR_NAME
    return _PROJECT_ROOT


def legacy_app_home() -> Path:
    """Eski adın veri klasörü (`%APPDATA%\\CV Autopilot`); yalnızca okunur."""
    return _appdata_dir() / LEGACY_APP_DIR_NAME


def home_override() -> Optional[str]:
    """`FLYBYCV_HOME`, o yoksa eski `CV_AUTOPILOT_HOME`; ikisi de boşsa `None`."""
    return os.environ.get(HOME_ENV_VAR) or os.environ.get(LEGACY_HOME_ENV_VAR) or None


def app_home() -> Path:
    """Kişisel verinin (profil, veritabanı, üretilen PDF'ler, loglar) kök klasörü.

    Öncelik: `FLYBYCV_HOME` (yoksa `CV_AUTOPILOT_HOME`) ortam değişkeni > paketlenmişse
    `%APPDATA%\\FlybyCV` > geliştirmede proje kökü (`data/`/`output/` proje içinde kalır).
    """
    override = home_override()
    if override:
        return Path(override)
    return default_app_home()


def _same_location(first: Path, second: Path) -> bool:
    return os.path.normcase(str(first.resolve())) == os.path.normcase(str(second.resolve()))


def is_custom_home() -> bool:
    """Ev ortam değişkeni (`home_override`), varsayılan kurulumdan FARKLI bir klasörü gösteriyor mu?

    Aynı klasörü gösteren bir ayar (ör. varsayılan `%APPDATA%\\FlybyCV`'yi elle yazmak) ayrı
    bir kurulum SAYILMAZ: aynı veriyi kullanır, bu yüzden aynı şifre kasası kaydını da kullanmalı
    (`src/core/secrets.py::service_name`).
    """
    override = home_override()
    if not override:
        return False
    return not _same_location(Path(override), default_app_home())


def data_dir() -> Path:
    return app_home() / "data"


def output_dir() -> Path:
    return app_home() / "output"


def logs_dir() -> Path:
    return app_home() / "logs"


def templates_dir() -> Path:
    """Jinja2 şablonlarının kökü — hem geliştirmede hem .exe'de `resource_dir()` altında."""
    return resource_dir() / "templates"


def professions_dir() -> Path:
    """Meslek paketlerinin (`presets/professions/*.json`) kökü — şablonlarla aynı ilke:
    salt okunur, uygulamayla birlikte paketlenir, `resource_dir()` altında."""
    return resource_dir() / "presets" / "professions"


def mail_template_presets_dir() -> Path:
    """Genel (mesleğe özel olmayan) hazır mail şablonlarının kökü (`presets/mail_templates/*.json`,
    PLAN.md Adım 10.4c) — `professions_dir()` ile aynı ilke: salt okunur, uygulamayla paketlenir."""
    return resource_dir() / "presets" / "mail_templates"


def cities_path() -> Path:
    """81 ilin listesi (`presets/cities.json`, PLAN.md Adım 10.6a) — şablonlarla aynı ilke:
    salt okunur, uygulamayla birlikte paketlenir, `resource_dir()` altında. `scripts/
    generate_cities.py` ile BİR KERELİK üretilir; uygulama çalışırken ağa hiç çıkılmaz."""
    return resource_dir() / "presets" / "cities.json"


def mail_providers_path() -> Path:
    """Mail sağlayıcı ön ayarları (`presets/mail_providers.json`) — şablonlarla aynı ilke:
    salt okunur, uygulamayla birlikte paketlenir, `resource_dir()` altında."""
    return resource_dir() / "presets" / "mail_providers.json"


def profile_path() -> Path:
    return data_dir() / "master_profile.json"


def own_cv_path() -> Path:
    """"Kendi PDF'imi olduğu gibi ekle" modunda (Faz 10 Adım 10.3a) kullanıcının yüklediği
    PDF'in kopyalandığı sabit konum — `src/cv_builder/own_cv.py::set_own_cv`."""
    return data_dir() / "own_cv.pdf"


def app_icon_path() -> Path:
    """Uygulama simgesi (pencere simgesi için); hem geliştirmede hem .exe'de `resource_dir()` altında."""
    return resource_dir() / "assets" / "app_icon.ico"


def profile_example_path() -> Path:
    """Paketlenmiş örnek profil — yalnızca ilk kurulumda kopyalanmak için okunur."""
    return resource_dir() / "data" / "master_profile.example.json"


def db_path() -> Path:
    return data_dir() / "app.db"


def configure_playwright_browsers_path() -> None:
    """Paketlenmişken Playwright'ın gömülü Chromium'u bulmasını sağlar (Faz 8.5).

    `PLAYWRIGHT_BROWSERS_PATH=0`, Playwright'a tarayıcıyı `%LOCALAPPDATA%\\ms-playwright`
    yerine kendi paket klasörünün altındaki `.local-browsers`'ta aramasını söyler.
    `scripts/build_exe.ps1` `playwright install chromium --only-shell`'i AYNI değişkenle
    çalıştırıp o klasörü doldurur; PyInstaller'ın kendi `playwright` kancası bu klasörü
    pakete gömer (`playwright/_impl/__pyinstaller/hook-playwright.sync_api.py`). İkisi
    farklı değer kullanırsa .exe kurulu Chromium'u bulamaz.

    Geliştirmede (`python main.py`) DOKUNMAZ: tarayıcı zaten `playwright install chromium`
    ile varsayılan konumda kurulu (bkz. Adım 8 kararları). Çağıran (`main.py`) bunu HER
    Playwright kullanımından (öz-test dahil) ÖNCE, mümkün olduğunca en başta çağırmalı.
    """
    if is_frozen():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")


def legal_text_path() -> Path:
    """KVKK / 6563 bilgilendirme metni (`presets/legal/kvkk_tr.md`, PLAN.md Adım 10.8b) — salt
    okunur, uygulamayla paketlenir (`presets/` klasörünün tamamı pakete girer)."""
    return resource_dir() / "presets" / "legal" / "kvkk_tr.md"


def places_pricing_path() -> Path:
    """Google Places fiyat bilgisi (`presets/google_places_pricing.json`, PLAN.md Adım 10.6h) —
    salt okunur, uygulamayla paketlenir. Fiyat elle güncellenir; dosyadaki `last_checked` tarihi
    Otopilot'un maliyet önizlemesinde kullanıcıya gösterilir."""
    return resource_dir() / "presets" / "google_places_pricing.json"
