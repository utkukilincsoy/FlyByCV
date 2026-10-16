"""Paketlenmiş sürüm için hafif öz-test: kasa (keyring) ve paketleme bağımlılıkları çalışıyor mu.

`python main.py --selftest` ile çalıştırılır. Hiçbir mail göndermez, hiçbir API'yi
çağırmaz, hiçbir gerçek profil verisi kullanmaz, hiçbir şifre/anahtar log'a veya ekrana
YAZILMAZ (CLAUDE.md kural 6). Sonuç hem stdout'a hem log dosyasına yazılır (paketlenmiş
`--windowed` .exe'de stdout görünmez, bkz. `logging_setup.py`) -- bu yüzden çağıran
(`main.py`) `configure_logging()`'i selftest'ten ÖNCE çalıştırmalı.

Denetimler (TAMAM/HATA):
  - şablon klasörü / ana CV şablonu (`cv.html`) bulunuyor mu
  - aktif kasa arka ucunun adı (paketlenmişken `WinVaultKeyring` olmalı, bkz. `secrets.configure_backend`)
  - `PySide6.QtPdf`/`QtPdfWidgets` içe aktarılabiliyor mu (PySide6-Essentials pakette düşebilir)
  - Playwright + gömülü Chromium sabit bir test HTML'inden geçici klasöre küçük bir PDF üretebiliyor mu

Kasa hesaplarının (`var`/`yok`) raporu bilgi amaçlıdır ve çıkış koduna etki ETMEZ: kullanılmayan
bir sağlayıcının anahtarının kayıtlı olmaması hata değildir (CLAUDE.md kural 2/3). Yukarıdaki
denetimlerden biri bile HATA ise çıkış kodu 1'dir.
"""
import logging
import sys
import tempfile
from pathlib import Path
from typing import Callable

import keyring
from keyring.errors import KeyringError
from playwright.sync_api import sync_playwright

from src.core import paths
from src.core.secrets import SMTP_ACCOUNT, service_name
from src.llm.providers import API_KEY_PROVIDERS

logger = logging.getLogger(__name__)

#: Kasada denetlenen tüm hesap adları: LLM sağlayıcıları + Gmail uygulama şifresi.
CHECKED_ACCOUNTS: tuple[str, ...] = (*API_KEY_PROVIDERS, SMTP_ACCOUNT)

VAR = "var"
YOK = "yok"
ERISILEMEDI = "kasaya erişilemedi"

TAMAM = "TAMAM"
HATA = "HATA"

#: Paketlenmişken beklenen Windows kasa arka ucunun sınıf adı (bkz. `secrets.configure_backend`).
EXPECTED_FROZEN_BACKEND = "WinVaultKeyring"

#: Playwright'ın gerçek profil verisi olmadan PDF üretip üretemediğini denetlemek için
#: sabit, en küçük HTML -- `master_profile.json`'a hiç dokunmaz.
_SELFTEST_HTML = "<html><body><h1>selftest</h1></body></html>"

#: Bir denetimin sonucu: (TAMAM mı, kullanıcıya/log'a yazılacak kısa detay -- hiçbir sır içermez).
CheckResult = tuple[bool, str]


def _print_safe(text: str) -> None:
    """`print(text)` gibidir ama konsolun kod sayfası Türkçe karakterleri (Ş, İ, ç...)
    içermiyorsa (ör. cp1252) `UnicodeEncodeError` ile çökmez -- kodlanamayan karakterler
    `?` ile değiştirilip yazılır. Bu yalnızca öz-testin kendisini çalıştıran geliştiriciye
    görünür (paketlenmiş `--windowed` .exe'de zaten konsol yok, `_ensure_std_streams()`
    stdout'u `os.devnull`'a bağlar); asıl rapor her koşulda UTF-8 log dosyasına da yazılır.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(encoding, errors="replace").decode(encoding))


def check_account(account: str) -> str:
    """Tek bir hesabın durumunu döner: `VAR`, `YOK` veya `ERISILEMEDI`.

    Okunan değerin kendisi hiçbir yere yazılmaz, yalnızca boş olup olmadığına bakılır.
    """
    try:
        value = keyring.get_password(service_name(), account)
    except KeyringError:
        return ERISILEMEDI
    return VAR if value else YOK


def _check_templates() -> CheckResult:
    """Şablon klasörü ve ana CV şablonu (`cv.html`) bulunuyor mu."""
    template_path = paths.templates_dir() / "cv.html"
    if template_path.is_file():
        return True, str(template_path)
    return False, f"{template_path} bulunamadı"


def _check_keyring_backend() -> CheckResult:
    """Aktif kasa arka ucunun adı; paketlenmişken `WinVaultKeyring` olmalı."""
    try:
        backend_name = type(keyring.get_keyring()).__name__
    except Exception as error:  # pragma: no cover - keyring.get_keyring() pratikte hata vermez
        return False, f"kasa arka ucu okunamadı ({type(error).__name__})"

    if paths.is_frozen() and backend_name != EXPECTED_FROZEN_BACKEND:
        return False, f"{backend_name} (paketlenmişken {EXPECTED_FROZEN_BACKEND} bekleniyordu)"
    return True, backend_name


def _check_qtpdf() -> CheckResult:
    """`PySide6.QtPdf`/`QtPdfWidgets` içe aktarılabiliyor mu.

    `src/ui/queue_preview.py`'deki CV önizlemesi bu modüllere dayanır; PySide6-Essentials
    paketlemesinde (PyInstaller) düşebilirler (o modüldeki yedek davranışla aynı kontrol).
    """
    try:
        from PySide6.QtPdf import QPdfDocument  # noqa: F401
        from PySide6.QtPdfWidgets import QPdfView  # noqa: F401
    except ImportError as error:
        return False, f"içe aktarılamadı ({type(error).__name__})"
    return True, "içe aktarılabiliyor"


def _check_playwright_pdf() -> CheckResult:
    """Chromium açılıp sabit bir test HTML'inden geçici klasöre küçük bir PDF üretebiliyor mu.

    Profil verisi KULLANILMAZ -- yalnızca Playwright + gömülü Chromium'un paketlenmiş
    sürümde de çalıştığını doğrulamak amaçlıdır. Üretilen dosya geçici klasörle birlikte
    hemen silinir.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="flybycv-selftest-") as tmp_dir:
            out_path = Path(tmp_dir) / "selftest.pdf"
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page()
                    page.set_content(_SELFTEST_HTML, wait_until="load")
                    page.pdf(path=str(out_path))
                finally:
                    browser.close()
            size = out_path.stat().st_size if out_path.exists() else 0
    except Exception as error:
        return False, type(error).__name__

    if size <= 0:
        return False, "PDF üretildi ama boş"
    return True, f"{size} bayt"


#: (etiket, denetim fonksiyonu) -- sırayla çalıştırılıp raporlanır.
_CHECKS: tuple[tuple[str, Callable[[], CheckResult]], ...] = (
    ("Şablon klasörü / cv.html", _check_templates),
    ("Kasa arka ucu", _check_keyring_backend),
    ("PySide6.QtPdf", _check_qtpdf),
    ("Playwright + Chromium PDF üretimi", _check_playwright_pdf),
)


def run() -> int:
    """Tüm öz-test kontrollerini çalıştırır, stdout'a VE log dosyasına yazar.

    Denetimlerden biri bile HATA ise 1, hepsi TAMAM ise 0 döner. Kasa hesap raporu
    (var/yok) bilgi amaçlıdır, bu sonucu etkilemez.
    """
    lines = [f"FlybyCV öz-test -- kasa (keyring) durumu [servis: {service_name()}]"]
    for account in CHECKED_ACCOUNTS:
        lines.append(f"  {account}: {check_account(account)}")

    lines.append("Denetimler:")
    all_ok = True
    for label, check in _CHECKS:
        ok, detail = check()
        all_ok = all_ok and ok
        lines.append(f"  {label}: {TAMAM if ok else HATA} ({detail})")

    report = "\n".join(lines)
    _print_safe(report)
    logger.info("Öz-test raporu:\n%s", report)

    return 0 if all_ok else 1
