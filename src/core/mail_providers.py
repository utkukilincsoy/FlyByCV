"""Mail sağlayıcı ön ayarları: `presets/mail_providers.json` — PLAN.md Adım 10.7.

Amaç: kullanıcı Ayarlar'da SMTP/IMAP host/port/güvenlik türünü ELLE girmesin, bir sağlayıcı
(Gmail/Yandex/…) seçsin. "Özel" bu dosyada bir ön ayar DEĞİL — kullanıcı host/port'u kendisi
girer, bkz. `PROVIDER_CUSTOM` (`src/sender/mail_connection.py`).

Outlook/Hotmail listede görünür ama `supported=false`: Microsoft kişisel hesaplarda uygulama
şifresiyle SMTP/IMAP girişini kapatıp OAuth2'ye zorunlu geçiriyor (bu sürüm OAuth2
desteklemiyor) — `src/ui/settings_data.py::validate_form` seçilirse reddeder, arayüz
combo'da öğeyi devre dışı bırakır.

Bozuk/eksik bir ön ayar dosyası uygulamayı ÇÖKERTMEZ (`core/professions.py` ile aynı ilke):
okunamazsa ya da içinde "gmail" yoksa sabit `FALLBACK_PRESETS`e düşülür — var olan bir
kullanıcının gönderimi bir ön ayar dosyası hatası yüzünden kesilmez.
"""
import json
import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ValidationError, field_validator
from sqlmodel import Session

from src.core.paths import mail_providers_path
from src.core.settings import (
    KEY_CUSTOM_IMAP_HOST,
    KEY_CUSTOM_IMAP_PORT,
    KEY_CUSTOM_SMTP_HOST,
    KEY_CUSTOM_SMTP_PORT,
    KEY_CUSTOM_SMTP_SECURITY,
    KEY_MAIL_PROVIDER,
    get_setting,
)
from src.sender.mail_connection import (
    GMAIL_CONNECTION,
    PROVIDER_CUSTOM,
    SECURITIES,
    SECURITY_STARTTLS,
    MailConnection,
)

logger = logging.getLogger(__name__)


class MailProviderPreset(BaseModel):
    id: str
    name_tr: str
    supported: bool
    smtp_host: str
    smtp_port: int
    smtp_security: str
    imap_host: str
    imap_port: int
    #: Kayıtlı yapılacak şifrenin nereden alınacağını anlatan Türkçe ipucu (Ayarlar'da gösterilir).
    password_hint_tr: Optional[str] = None
    #: Uygulama şifresinin adım adım alınışı (kurulum sihirbazında numaralı liste olarak gösterilir);
    #: yoksa `password_hint_tr`'ye düşülür. Menü adları zamanla değişebilir, metin ana kaynaktır.
    password_guide_tr: Optional[list[str]] = None
    #: Yalnızca `supported=false` iken dolu — desteklenmeme sebebi (Ayarlar'da gösterilir).
    unsupported_reason_tr: Optional[str] = None

    @field_validator("smtp_security")
    @classmethod
    def security_known(cls, value: str) -> str:
        if value not in SECURITIES:
            raise ValueError(f"smtp_security şunlardan biri olmalı: {SECURITIES}")
        return value

    @field_validator("smtp_port", "imap_port")
    @classmethod
    def port_in_range(cls, value: int) -> int:
        if not (1 <= value <= 65535):
            raise ValueError("port 1-65535 aralığında olmalı")
        return value

    def to_connection(self) -> MailConnection:
        return MailConnection(
            provider=self.id,
            smtp_host=self.smtp_host,
            smtp_port=self.smtp_port,
            smtp_security=self.smtp_security,
            imap_host=self.imap_host,
            imap_port=self.imap_port,
        )


#: Ön ayar dosyası hiç okunamazsa düşülecek TEK ön ayar — bkz. modül docstring'i.
FALLBACK_PRESETS: dict[str, MailProviderPreset] = {
    "gmail": MailProviderPreset(
        id="gmail",
        name_tr="Gmail",
        supported=True,
        smtp_host=GMAIL_CONNECTION.smtp_host,
        smtp_port=GMAIL_CONNECTION.smtp_port,
        smtp_security=GMAIL_CONNECTION.smtp_security,
        imap_host=GMAIL_CONNECTION.imap_host,
        imap_port=GMAIL_CONNECTION.imap_port,
        password_hint_tr=(
            "Google Hesabı → Güvenlik → Uygulama şifreleri'nden alınan 16 haneli uygulama "
            "şifresi (normal Gmail şifreniz DEĞİL; hesapta 2 adımlı doğrulama açık olmalı)."
        ),
    ),
}


def load_mail_provider_presets(path: Optional[Path] = None) -> dict[str, MailProviderPreset]:
    """Tüm geçerli mail sağlayıcı ön ayarlarını `id -> MailProviderPreset` olarak döner.

    `path` verilmezse `paths.mail_providers_path()` kullanılır (testler kendi geçici dosyasını
    verebilir). Dosya okunamaz/bozuksa veya içinde "gmail" yoksa `FALLBACK_PRESETS`e düşülür.
    """
    base = path if path is not None else mail_providers_path()
    try:
        raw = json.loads(base.read_text(encoding="utf-8"))
        entries = raw["providers"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        logger.warning(
            f"Mail sağlayıcı ön ayarları okunamadı, yalnızca Gmail kullanılabilir olacak: {error}"
        )
        return dict(FALLBACK_PRESETS)

    presets: dict[str, MailProviderPreset] = {}
    for entry in entries:
        try:
            preset = MailProviderPreset.model_validate(entry)
        except ValidationError as error:
            logger.warning(
                f"Mail sağlayıcı ön ayarı şemaya uymuyor, atlandı: {entry.get('id', '?')}: {error}"
            )
            continue
        presets[preset.id] = preset

    if "gmail" not in presets:
        logger.warning("Mail sağlayıcı ön ayarlarında 'gmail' yok, yedeğe düşülüyor")
        presets["gmail"] = FALLBACK_PRESETS["gmail"]

    logger.info(f"{len(presets)} mail sağlayıcı ön ayarı yüklendi: {sorted(presets)}")
    return presets


def resolve_connection_for_provider(
    provider: str,
    presets: dict[str, MailProviderPreset],
    *,
    custom_smtp_host: Optional[str] = None,
    custom_smtp_port: Optional[int] = None,
    custom_smtp_security: str = SECURITY_STARTTLS,
    custom_imap_host: Optional[str] = None,
    custom_imap_port: Optional[int] = None,
) -> MailConnection:
    """Sağlayıcı kimliğinden gerçek bağlantı bilgisini kurar; ASLA çökmez/`None` dönmez.

    `provider == PROVIDER_CUSTOM` ise `custom_*` alanlarından kurulur; biri eksikse (kaydetmeden
    önce `src/ui/settings_data.py::validate_form` zaten engellemiş olmalı, ama gönderim yolu
    buna güvenmez) Gmail'e düşülür. Bilinmeyen ya da `supported=false` bir sağlayıcı (ör. eski
    bir "outlook" kaydı, ya da ön ayar dosyasından silinmiş bir id) de aynı şekilde Gmail'e
    düşer — göndermeye çalışan kod her zaman kullanılabilir bir bağlantı bulur.
    """
    if provider == PROVIDER_CUSTOM:
        if custom_smtp_host and custom_smtp_port and custom_imap_host and custom_imap_port:
            return MailConnection(
                provider=PROVIDER_CUSTOM,
                smtp_host=custom_smtp_host,
                smtp_port=custom_smtp_port,
                smtp_security=custom_smtp_security,
                imap_host=custom_imap_host,
                imap_port=custom_imap_port,
            )
        logger.warning("Özel mail sunucusu ayarları eksik, Gmail'e düşülüyor")
        return GMAIL_CONNECTION

    preset = presets.get(provider)
    if preset is None:
        logger.warning(f"Bilinmeyen mail sağlayıcısı '{provider}', Gmail'e düşülüyor")
        return GMAIL_CONNECTION
    if not preset.supported:
        logger.warning(f"'{provider}' desteklenmiyor, Gmail'e düşülüyor")
        return GMAIL_CONNECTION
    return preset.to_connection()


def resolve_mail_connection(session: Session) -> MailConnection:
    """Ayarlardaki (`KEY_MAIL_PROVIDER` + `custom_*`) seçime göre gerçek bağlantıyı kurar.

    Gönderim/IMAP yollarının (worker/jobs/runner/send_one/service) TEK kaynağı budur — hiçbiri
    `presets/mail_providers.json`i kendisi okumaz.
    """
    presets = load_mail_provider_presets()
    provider = get_setting(session, KEY_MAIL_PROVIDER)
    return resolve_connection_for_provider(
        provider,
        presets,
        custom_smtp_host=get_setting(session, KEY_CUSTOM_SMTP_HOST),
        custom_smtp_port=get_setting(session, KEY_CUSTOM_SMTP_PORT),
        custom_smtp_security=get_setting(session, KEY_CUSTOM_SMTP_SECURITY),
        custom_imap_host=get_setting(session, KEY_CUSTOM_IMAP_HOST),
        custom_imap_port=get_setting(session, KEY_CUSTOM_IMAP_PORT),
    )
