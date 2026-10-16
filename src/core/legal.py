"""KVKK / 6563 bilgilendirmesi ve kullanıcı onayı (PLAN.md Faz 10 Adım 10.8b).

Metin `presets/legal/kvkk_tr.md`'dedir ("bilgilendirme, hukuki tavsiye değildir"). Onay
`Setting`'te saklanır: onaylanan metnin SÜRÜMÜ + zamanı. Metin anlamlı biçimde değişirse
`LEGAL_TEXT_VERSION` artırılır ve herkese bir kez yeniden sorulur.

Onay olmadan GERÇEK gönderim yapılmaz (`smtp_sender.send_application`, tüm gönderim yollarının
tek geçidi); test modu kullanıcının kendi adresine gittiği için onay istemez.
"""
import logging
from datetime import datetime
from typing import Optional

from sqlmodel import Session

from src.core import paths
from src.core.models import utcnow
from src.core.settings import (
    KEY_LEGAL_CONSENT_AT,
    KEY_LEGAL_CONSENT_VERSION,
    get_setting,
    set_setting,
)

logger = logging.getLogger(__name__)

#: Metin değiştiğinde artırılır (tarih biçimi). Değişince `has_consent()` False döner.
LEGAL_TEXT_VERSION = "2026-10-01"  # 10.9d: "Yeni sürüm kontrolü" (GitHub) maddesi eklendi

CONSENT_REQUIRED_REASON = (
    "KVKK / 6563 bilgilendirmesi henüz onaylanmadı, gerçek gönderim yapılmaz "
    "(uygulamayı yeniden açınca onay sorulur; test modu onaysız da çalışır)"
)


def load_legal_text() -> str:
    """Bilgilendirme metnini okur. Dosya okunamazsa istisna FIRLATIR: onay alınacak metin
    gösterilemiyorsa onay da alınamaz."""
    return paths.legal_text_path().read_text(encoding="utf-8")


def has_consent(session: Session) -> bool:
    """Geçerli metin sürümü onaylanmış mı?"""
    return get_setting(session, KEY_LEGAL_CONSENT_VERSION) == LEGAL_TEXT_VERSION


def record_consent(session: Session, now: Optional[datetime] = None) -> None:
    """Onayı yazar (sürüm + zaman). COMMIT ETMEZ."""
    moment = now if now is not None else utcnow()
    set_setting(session, KEY_LEGAL_CONSENT_VERSION, LEGAL_TEXT_VERSION)
    set_setting(session, KEY_LEGAL_CONSENT_AT, moment.isoformat())
    logger.info(f"KVKK/6563 bilgilendirmesi onaylandı (sürüm {LEGAL_TEXT_VERSION})")
