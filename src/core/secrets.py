"""API anahtarlarını işletim sisteminin şifre kasasında (keyring) saklar.

`keyring` paketi, Windows'ta "Kimlik Bilgileri Yöneticisi"ne, macOS'ta "Anahtar Zinciri"ne
yazar. Anahtarlar bu yüzden ne koda, ne `.env` dosyasına, ne veritabanına girer
(CLAUDE.md kural 6).

Kasa her zaman kullanılabilir olmayabilir (Linux'ta kurulu olmayan bir arka uç, kilitli
kasa...). Böyle bir durumda okuma fonksiyonu hata fırlatmaz, `None` döner: uygulama
açılmaya devam etmeli ve anahtar kelime sağlayıcısıyla çalışmalı (CLAUDE.md kural 3).

DİKKAT: Anahtarın kendisi HİÇBİR yere loglanmaz — uzunluğu veya ilk harfleri bile.
"""
import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass
from typing import Optional

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from src.core import paths
from src.llm.providers import API_KEY_PROVIDERS

logger = logging.getLogger(__name__)

#: VARSAYILAN kurulumun kasa servis adı; kullanıcı adı olarak sağlayıcı adı kullanılır. Ayrı bir
#: `FLYBYCV_HOME` kurulumu için `service_name()` bu adın sonuna ev dizininin özetini ekler.
SERVICE_NAME = "flybycv"

#: Uygulamanın eski adıyla (CV Autopilot) kullanılan servis adı. Yalnızca okunur: açılışta
#: `migrate_legacy_secrets` buradaki kayıtları yeni servise KOPYALAR (silmez); kaldırıcının
#: "verilerimi de sil" seçeneği (`purge_all_secrets`) bunları da siler.
LEGACY_SERVICE_NAME = "cv-autopilot"

_HOME_DIGEST_LENGTH = 12


def _service_name_for(base: str) -> str:
    if not paths.is_custom_home():
        return base
    home = os.path.normcase(str(paths.app_home().resolve()))
    digest = hashlib.sha256(home.encode("utf-8")).hexdigest()[:_HOME_DIGEST_LENGTH]
    return f"{base}-{digest}"


def service_name() -> str:
    """Bu kurulumun kasa servis adı.

    Varsayılan kurulum (`FLYBYCV_HOME` yok ya da varsayılan klasörü gösteriyor) `SERVICE_NAME`'i
    kullanır. `FLYBYCV_HOME` BAŞKA bir klasörü gösteriyorsa servis adına o klasörün yolunun özeti
    eklenir (`flybycv-<12 hex>`): ayrı kurulum, gerçek kurulumun Gmail şifresini/API anahtarlarını
    ne okur ne de üzerine yazar. Yol büyük/küçük harf ve ayraç farkından etkilenmez
    (`os.path.normcase` + `resolve`). Özet yalnızca ayırt etmek içindir, sır değildir.
    """
    return _service_name_for(SERVICE_NAME)


def legacy_service_name() -> str:
    """Bu kurulumun eski addaki (`cv-autopilot`, ayrı kurulumda `cv-autopilot-<12 hex>`) servis adı."""
    return _service_name_for(LEGACY_SERVICE_NAME)

#: Gmail uygulama şifresi bu kullanıcı adıyla kaydedilir. `src/llm/providers.API_KEY_PROVIDERS`
#: listesine EKLENMEZ: o liste "kayıtlı anahtarı olan sağlayıcıyı LLM yap" mantığında kullanılıyor
#: (scripts/preview_application.py) — SMTP şifresi orada bir LLM sağlayıcısı sanılırdı.
SMTP_ACCOUNT = "smtp"

#: Google Places API anahtarı bu kullanıcı adıyla kaydedilir (`company_finder/source_selector.py` ve
#: `autopilot/runner.py` aynı değeri kendi adlarıyla taşır; `tests/test_secrets.py` üçünün aynı
#: kaldığını denetler). `purge_all_secrets` bu anahtarı da silmek zorunda.
GOOGLE_PLACES_ACCOUNT = "google_places"

#: Uygulamanın kasaya yazdığı TÜM hesaplar: LLM sağlayıcıları + Google Places + Gmail uygulama şifresi.
#: Kasaya yeni bir sır yazan her kod buraya eklenmeli, yoksa "verilerimi de sil" onu unutur.
ALL_SECRET_ACCOUNTS: tuple[str, ...] = (*API_KEY_PROVIDERS, GOOGLE_PLACES_ACCOUNT, SMTP_ACCOUNT)


def configure_backend() -> None:
    """Paketlenmiş (.exe) sürümde kasa arka ucunu elle seçer.

    PyInstaller ile paketlenmiş bir uygulamada `keyring`'in normal otomatik arka uç
    bulma mekanizması (paket meta verisi/`entry_points` taraması) çalışmayabilir --
    PyInstaller bu meta veriyi varsayılan olarak pakete koymaz. Bulunamazsa `keyring`
    sessizce işe yaramayan bir "fail" arka uca düşer ve TÜM okuma/yazmalar, kullanıcı
    hiç anlamadan, sessizce başarısız olur. Bu yüzden paketlenmişken Windows kasa arka
    ucu (`WinVaultKeyring`) elle, açıkça seçilir -- otomatik keşfe gerek kalmaz.

    Geliştirmede (`is_frozen()` False) hiçbir şey yapmaz: otomatik keşif zaten çalışıyor.
    Arka uç içe aktarılamazsa (ör. Windows dışı bir ortam) sessizce geçilir; `get_api_key`/
    `get_mail_password` kasa yokken zaten `None` dönüp anahtar kelime sağlayıcısına düşüyor
    (CLAUDE.md kural 3).
    """
    if not paths.is_frozen() or sys.platform != "win32":
        return

    try:
        from keyring.backends.Windows import WinVaultKeyring
    except ImportError as error:
        logger.warning(f"Windows kasa arka ucu içe aktarılamadı ({type(error).__name__})")
        return

    keyring.set_keyring(WinVaultKeyring())
    logger.info("Paketlenmiş sürüm: kasa arka ucu Windows Kimlik Bilgileri Yöneticisi olarak ayarlandı")


def get_api_key(provider: str) -> Optional[str]:
    """Sağlayıcının API anahtarını kasadan okur; kayıt yoksa veya kasa açılamazsa None."""
    try:
        key = keyring.get_password(service_name(), provider)
    except KeyringError as error:
        logger.warning(
            f"Şifre kasası açılamadı, {provider} API anahtarı okunamadı "
            f"({type(error).__name__})"
        )
        return None

    if not key:
        logger.debug(f"{provider} için kayıtlı API anahtarı yok")
        return None
    return key


def set_api_key(provider: str, key: str) -> None:
    """Sağlayıcının API anahtarını kasaya yazar.

    Anahtarın başındaki/sonundaki boşluklar temizlenir: kopyala-yapıştır edilen
    anahtarların sonunda kalan satır sonu çok yaygın bir hata kaynağıdır.

    Raises:
        ValueError: Anahtar boşsa (boş anahtar kaydetmek sessiz bir arızaya yol açardı).
        KeyringError: Kasaya yazılamazsa — yazma hatası sessizce yutulmamalı.
    """
    cleaned = (key or "").strip()
    if not cleaned:
        raise ValueError("API anahtarı boş olamaz")

    keyring.set_password(service_name(), provider, cleaned)
    logger.info(f"{provider} API anahtarı şifre kasasına kaydedildi")


def delete_api_key(provider: str) -> bool:
    """Sağlayıcının API anahtarını kasadan siler; kayıt yoktuysa False döner."""
    try:
        keyring.delete_password(service_name(), provider)
    except PasswordDeleteError:
        logger.info(f"{provider} için silinecek bir API anahtarı yoktu")
        return False
    except KeyringError as error:
        logger.warning(f"Şifre kasası açılamadı, silme yapılamadı ({type(error).__name__})")
        return False

    logger.info(f"{provider} API anahtarı şifre kasasından silindi")
    return True


@dataclass(frozen=True)
class PurgeReport:
    """`purge_all_secrets` sonucu: hesap ADLARI (hiçbir değer ya da uzunluk içermez)."""

    deleted: tuple[str, ...] = ()  # kayıt vardı, silindi
    absent: tuple[str, ...] = ()  # zaten kayıt yoktu
    failed: tuple[str, ...] = ()  # kasa açılamadı, silinemedi

    @property
    def ok(self) -> bool:
        return not self.failed


_DELETED, _ABSENT, _FAILED = "deleted", "absent", "failed"


def _delete_secret(service: str, account: str) -> str:
    try:
        keyring.delete_password(service, account)
    except PasswordDeleteError:
        return _ABSENT
    except KeyringError as error:
        logger.warning(f"Şifre kasası açılamadı, {account} silinemedi ({type(error).__name__})")
        return _FAILED
    return _DELETED


def purge_all_secrets() -> PurgeReport:
    """Bu kurulumun kasadaki TÜM kayıtlarını siler (Adım 10.9e).

    Kaldırıcının "verilerimi de sil" seçeneği içindir; geri alınamaz. Yalnızca BU kurulumun servis
    adları (`service_name()` ve eski addaki `legacy_service_name()`) altındaki, `ALL_SECRET_ACCOUNTS`'
    taki hesaplara dokunur: `FLYBYCV_HOME` ile açılmış ayrı bir kurulumun ya da başka bir uygulamanın
    kayıtları silinmez. Bir hesap iki servisten birinde silindiyse `deleted`, birinde silinemediyse
    `failed` sayılır. Bir hesap silinemese de kalanlar denenir; hata sessizce yutulmaz. Değerler
    okunmaz, hiçbir yere yazılmaz.
    """
    services = (service_name(), legacy_service_name())
    deleted: list[str] = []
    absent: list[str] = []
    failed: list[str] = []
    for account in ALL_SECRET_ACCOUNTS:
        outcomes = {_delete_secret(service, account) for service in services}
        if _FAILED in outcomes:
            failed.append(account)
        elif _DELETED in outcomes:
            deleted.append(account)
        else:
            absent.append(account)
    logger.info(
        f"Kasa temizlendi: {len(deleted)} silindi, {len(absent)} zaten yoktu, {len(failed)} silinemedi"
    )
    return PurgeReport(tuple(deleted), tuple(absent), tuple(failed))


def migrate_legacy_secrets() -> tuple[str, ...]:
    """Eski addaki (`legacy_service_name()`) kayıtları yeni servise KOPYALAR; kopyalanan hesap adlarını döner.

    Yalnızca yeni serviste kaydı OLMAYAN hesaplar kopyalanır (kullanıcının yeni adla girdiği bir
    şifrenin üzerine yazılmaz). Eski kayıtlar silinmez. Kasa açılamazsa o hesap atlanır, açılış
    engellenmez. Değerler hiçbir yere yazılmaz (yalnızca hesap adları loglanır).
    """
    new_service, old_service = service_name(), legacy_service_name()
    copied: list[str] = []
    for account in ALL_SECRET_ACCOUNTS:
        try:
            if keyring.get_password(new_service, account):
                continue
            value = keyring.get_password(old_service, account)
            if not value:
                continue
            keyring.set_password(new_service, account, value)
        except KeyringError as error:
            logger.warning(f"Eski kasa kaydı taşınamadı: {account} ({type(error).__name__})")
            continue
        copied.append(account)
    if copied:
        logger.info(f"Eski adla kayıtlı kasa kayıtları yeni servise kopyalandı: {', '.join(copied)}")
    return tuple(copied)


def get_mail_password() -> Optional[str]:
    """Gmail uygulama şifresini kasadan okur; kayıt yoksa veya kasa açılamazsa None."""
    return get_api_key(SMTP_ACCOUNT)


def set_mail_password(password: str) -> None:
    """Gmail uygulama şifresini kasaya yazar.

    Google, uygulama şifresini ekranda "abcd efgh ijkl mnop" gibi 4'erli boşluklu gösterir;
    gerçek şifre boşluksuz 16 karakterdir. `set_api_key` yalnızca baştaki/sondaki boşluğu
    kırpar, aradaki boşluklar kalırdı ve giriş sessizce başarısız olurdu — burada TÜM boşluklar
    (ara dahil) temizlenir.

    Raises:
        ValueError: Şifre boşsa.
        KeyringError: Kasaya yazılamazsa.
    """
    cleaned = re.sub(r"\s+", "", password or "")
    set_api_key(SMTP_ACCOUNT, cleaned)


def delete_mail_password() -> bool:
    """Gmail uygulama şifresini kasadan siler; kayıt yoktuysa False döner."""
    return delete_api_key(SMTP_ACCOUNT)
