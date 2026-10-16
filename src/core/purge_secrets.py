"""`python main.py --purge-secrets` / `FlybyCV.exe --purge-secrets` (Faz 10 Adım 10.9e).

Kaldırıcı, kullanıcı "verilerimi de sil" derse bunu çağırır: şifre kasasındaki (keyring) Gmail
uygulama şifresi ve API anahtarları silinir (`secrets.purge_all_secrets`). Arayüz AÇILMAZ, ağa
çıkılmaz, veritabanına/profile dokunulmaz; `%APPDATA%` klasörünü silmek kaldırıcının işidir.
Geri alınamaz olduğu için bayrak yalnızca açıkça verildiğinde çalışır ve onay sormaz (kaldırıcı
zaten kullanıcıya sormuştur).

Rapor stdout'a VE log dosyasına yazılır (paketlenmiş `--windowed` .exe'de stdout görünmez, bkz.
`selftest.py`); yalnızca hesap adları yazılır, hiçbir sır ya da uzunluğu yazılmaz (CLAUDE.md kural 6).
Çıkış kodu: tüm kayıtlar silindi ya da zaten yoktu -> 0; kasa açılamadığı için en az biri
silinemedi -> 1 (kaldırıcı kullanıcıya "anahtarlar silinemedi" diyebilsin).
"""
import logging
import sys

from src.core.secrets import PurgeReport, legacy_service_name, purge_all_secrets, service_name

logger = logging.getLogger(__name__)

DELETED = "silindi"
ABSENT = "kayıt yoktu"
FAILED = "SİLİNEMEDİ (kasa açılamadı)"


def format_report(report: PurgeReport) -> str:
    lines = [f"FlybyCV kasa temizliği [servis: {service_name()}, eski: {legacy_service_name()}]"]
    lines += [f"  {account}: {DELETED}" for account in report.deleted]
    lines += [f"  {account}: {ABSENT}" for account in report.absent]
    lines += [f"  {account}: {FAILED}" for account in report.failed]
    return "\n".join(lines)


def _print_safe(text: str) -> None:
    """Konsol kod sayfası Türkçe karakterleri kodlayamazsa çökmeden `?` ile yazar (`selftest` ile aynı)."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(encoding, errors="replace").decode(encoding))


def run() -> int:
    report = purge_all_secrets()
    text = format_report(report)
    _print_safe(text)
    logger.info("Kasa temizliği raporu:\n%s", text)
    return 0 if report.ok else 1
