"""SMTP/IMAP bağlantı bilgisi (PLAN.md Adım 10.7).

`src/sender/smtp_sender.py`nin `MailAccount.connection` alanı ve `src/inbox/imap_reader.py`
bu modüldeki `MailConnection`'ı kullanır. Ayrı bir modülde yaşamasının nedeni: mail sağlayıcı
ön ayarlarını okuyan `src/core/mail_providers.py` (pydantic, `presets/mail_providers.json`)
buna ihtiyaç duyar ve `core` katmanının `sender` katmanını İTHAL ETMEMESİ gerekir (ters yönde
bir bağımlılık olurdu) — bu yüzden `MailConnection`/sabitler burada, `core` ve `sender` ikisi
de buradan alır.

`GMAIL_CONNECTION`, `MailAccount.connection`in varsayılanıdır: sağlayıcı hiç seçilmemişse
(Faz 10 öncesi var olan kullanıcı, ya da testlerdeki `MailAccount(identity=..., password=...)`
çağrıları) davranış AYNEN Gmail'de kalır — Adım 10.7b, "mevcut hesap Gmail olarak işaretlenir".
"""
from dataclasses import dataclass

#: SMTP güvenlik türleri. STARTTLS: düz bağlantı açılır, sonra TLS'e yükseltilir (Gmail/Outlook
#: 587). SSL: bağlantı baştan TLS'lidir (Yandex 465). `src/sender/smtp_sender.py::_open_smtp`
#: ikisini de destekler (Adım 10.7c).
SECURITY_STARTTLS = "starttls"
SECURITY_SSL = "ssl"
SECURITIES = (SECURITY_STARTTLS, SECURITY_SSL)

#: `presets/mail_providers.json`de YOK — kullanıcı host/port/güvenliği Ayarlar'da kendisi girer
#: (bkz. `src/core/mail_providers.py::resolve_connection_for_provider`).
PROVIDER_CUSTOM = "custom"


@dataclass(frozen=True)
class MailConnection:
    """Bir hesabın SMTP + IMAP bağlantı bilgisi — şifre İÇERMEZ (o `MailAccount.password`de)."""

    provider: str
    smtp_host: str
    smtp_port: int
    smtp_security: str
    imap_host: str
    imap_port: int


#: IMAP her zaman SSL (993) varsayılır — Gmail/Yandex/Outlook üçü de bu şekilde çalışıyor;
#: `custom` sağlayıcısında da IMAP güvenliği bilinçli olarak seçilemez (kapsam dışı, Adım
#: 10.7'nin notuna bkz.), yalnızca host/port değişir.
GMAIL_CONNECTION = MailConnection(
    provider="gmail",
    smtp_host="smtp.gmail.com",
    smtp_port=587,
    smtp_security=SECURITY_STARTTLS,
    imap_host="imap.gmail.com",
    imap_port=993,
)
