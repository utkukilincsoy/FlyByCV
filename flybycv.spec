# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller derleme tanımı (Faz 8 - Adım 8.5): onedir, konsolsuz, gömülü Chromium.

BU DOSYAYI DOĞRUDAN `pyinstaller flybycv.spec` İLE ÇAĞIRMA — `scripts/build_exe.ps1`
kullan. Script, derlemeden ÖNCE gömülü Chromium'un doğru klasörde kurulu olduğunu
denetler ve derlemeden SONRA `dist/` içinde kişisel veri sızıntısı olup olmadığını
kontrol eder (CLAUDE.md kural 7); bu dosyanın kendisi hiçbir denetim yapmaz.

Nelerin pakete girdiği:
  * Kod: `main.py` + `src/` (Analysis'in import analiziyle kendiliğinden bulunur).
  * Kaynaklar (salt okunur, `src/core/paths.resource_dir()` altında okunur):
    - `templates/` (cv.html, cv.css, mail şablonları)
    - `data/master_profile.example.json` (yalnızca bu tek dosya — bkz.
      `paths.profile_example_path()`, ilk kurulum / "örnek profille başla" için)
    - `presets/` KLASÖRÜNÜN TAMAMI (professions/, mail_templates/, cities.json,
      mail_providers.json ve ileride eklenecek her şey) — tek tek dosya/alt klasör
      listelemek yerine bilerek tüm klasör kopyalanıyor ki yeni bir preset dosyası
      eklendiğinde bu dosya unutulup güncellenmesin (bkz. `paths.professions_dir()`,
      `paths.mail_template_presets_dir()`, `paths.cities_path()`,
      `paths.mail_providers_path()`). `presets/README.md` da haliyle pakete girer —
      zararsız, kişisel veri değil.
  * Gömülü Chromium: burada elle EKLENMEZ. `playwright` paketinin kendi PyInstaller
    kancası (`playwright/_impl/__pyinstaller/hook-playwright.sync_api.py`,
    `collect_data_files("playwright")`) paket klasörünün altındaki `driver/package/
    .local-browsers/`'ı otomatik toplar — bu klasörün `playwright install chromium
    --only-shell` ile `PLAYWRIGHT_BROWSERS_PATH=0` KULLANILARAK önceden doldurulmuş
    olması gerekir (bkz. `src/core/paths.configure_playwright_browsers_path()` —
    aynı değişkeni çalışma zamanında da ayarlar, aksi halde .exe kurulu Chromium'u
    farklı bir klasörde arar).

Nelerin pakete GİRMEDİĞİ (CLAUDE.md kural 7 — kişisel veri asla git'e/pakete eklenmez):
  `data/app.db`, `data/master_profile.json`, `data/*.pdf`, `data/*.md` (ör.
  `profil_kontrol.md`), `output/`. `data/companies.example.csv` de bilinçli olarak
  EKLENMEDİ: paketlenmiş çalışma zamanı kodunun (`src/`) hiçbir yeri ona başvurmuyor,
  yalnızca geliştirmede `scripts/import_companies.py` için bir örnek.
"""
import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

PROJECT_ROOT = Path(SPECPATH)  # noqa: F821 - PyInstaller .spec çalışma zamanı ismi


def _load_version_module():
    """`src/core/version.py`'yi dosya yolundan yükler (sürümün TEK kaynağı, Adım 10.9a).

    `src` paketini import etmek yerine dosyadan yüklenir: `.spec` çalışırken `pathex` henüz
    kurulmamış olabilir ve `version.py` bilerek başka hiçbir modüle bağlı değildir.
    """
    module_path = PROJECT_ROOT / "src" / "core" / "version.py"
    spec = importlib.util.spec_from_file_location("flybycv_version", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_version = _load_version_module()
APP_NAME = _version.APP_NAME
_VERSION_TUPLE = _version.version_tuple()
# Windows dosya özellikleri sekmesindeki "Ayrıntılar" (dosya/ürün sürümü, ürün adı). Sürüm sabit
# YAZILMAZ; `src/core/version.py`'den gelir. 0x041F = Türkçe, 1200 = Unicode.
VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=_VERSION_TUPLE,
        prodvers=_VERSION_TUPLE,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "041F04B0",
                    [
                        StringStruct("FileDescription", APP_NAME),
                        StringStruct("FileVersion", _version.__version__),
                        StringStruct("InternalName", APP_NAME),
                        StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                        StringStruct("ProductName", APP_NAME),
                        StringStruct("ProductVersion", _version.__version__),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [0x041F, 1200])]),
    ],
)

datas = [
    (str(PROJECT_ROOT / "templates"), "templates"),
    (str(PROJECT_ROOT / "data" / "master_profile.example.json"), "data"),
    (str(PROJECT_ROOT / "presets"), "presets"),
    (str(PROJECT_ROOT / "assets"), "assets"),  # uygulama simgesi (SVG + ICO)
]

# Arayüz teması ve ikonlar: qt-material (tema XML'leri, qss şablonu, Roboto yazı tipleri) ve
# qtawesome (Font Awesome .ttf + karakter haritaları) veri dosyaları .exe'ye elle eklenir; yoksa
# tema/ikonlar yalnızca geliştirmede çalışır, paketlenmiş uygulamada sessizce düşer.
datas += collect_data_files("qt_material")
datas += collect_data_files("qtawesome")

# anthropic/openai/google-genai istemcileri sürümü `importlib.metadata` ile okuyup
# User-Agent başlığına koyuyor; paket meta verisi (dist-info) gömülmezse ilk gerçek
# çağrıda (selftest'te DEĞİL, kural 1 gereği yalnızca kullanıcı bir sağlayıcı seçip
# LLM'i tetiklediğinde) `PackageNotFoundError` ile çöker. Sürüm adı `requirements.txt`
# ile aynı üç paket; biri kaldırılır/yeniden adlandırılırsa derleme HATA vermesin diye
# eksik olan sessizce atlanıyor.
for _package in ("anthropic", "openai", "google-genai"):
    try:
        datas += copy_metadata(_package)
    except Exception:
        pass

a = Analysis(  # noqa: F821
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # konsolsuz (Adım 8.2 kararı); --selftest çıktısı log dosyasına yazılır
    disable_windowed_traceback=False,
    argv_emulation=False,
    version=VERSION_INFO,
    icon=str(PROJECT_ROOT / "assets" / "app_icon.ico"),  # .exe simgesi
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
