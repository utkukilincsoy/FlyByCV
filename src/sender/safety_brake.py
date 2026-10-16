"""Gönderim güvenlik freni (CLAUDE.md kural 5, PLAN.md Faz 9 Adım 9.4).

İki bağımsız fren var, ikisi de aynı `sending_paused` ayarını (acil durdur ile TAMAMEN aynı
anahtar) yazar:
  * **Art arda SMTP hatası** — `src/sender/worker.py` (art arda `MAX_CONSECUTIVE_ERRORS` başarısız
    gönderim, tek bir çalıştırma içinde).
  * **Bounce oranı** — `check_bounce_rate()`, `src/inbox/service.check_inbox()` her taramadan
    sonra çağırır (bounce'lar yalnızca gelen kutusu kontrolünde ortaya çıkıyor, gönderim anında
    değil).

`sending_paused` zaten açıksa `trigger_pause()` dokunmaz: ilk tetikleyenin sebebi
(`pause_reason`) kalır, kullanıcı kaldırana kadar bir sonraki fren onu EZMEZ.
"""
import logging
from typing import Optional

from sqlmodel import Session, select

from src.core.models import Application
from src.core.settings import (
    KEY_BOUNCE_MIN_SAMPLE,
    KEY_BOUNCE_PAUSE_RATE,
    KEY_PAUSE_REASON,
    KEY_SENDING_PAUSED,
    get_setting,
    set_setting,
)

logger = logging.getLogger(__name__)


def trigger_pause(session: Session, reason: str) -> bool:
    """`sending_paused`'ı açar ve `pause_reason`'ı yazar; zaten açıksa DOKUNMAZ.

    `commit` çağırana aittir (bu projedeki diğer tüm yazma fonksiyonlarıyla aynı ilke).
    Döndürülen `False`, bu çağrının pratikte hiçbir şey yapmadığını (zaten duraklatılmıştı)
    belirtir — çağıran isterse bunu görmezden gelebilir.
    """
    if get_setting(session, KEY_SENDING_PAUSED):
        return False
    set_setting(session, KEY_SENDING_PAUSED, True)
    set_setting(session, KEY_PAUSE_REASON, reason)
    logger.warning(f"Gönderim güvenlik freniyle durduruldu: {reason}")
    return True


def bounce_stats(session: Session) -> tuple[int, int]:
    """(bounce sayısı, toplam GERÇEK gönderim sayısı) döner.

    Yalnızca `Application.sent_at` dolu kayıtlar sayılır — test modu `sent_at`'i hiç yazmıyor
    (Faz 6.1 kararı), bu yüzden test mailleri örneklemi bilerek etkilemiyor.
    """
    bounced_ats = session.exec(
        select(Application.bounced_at).where(Application.sent_at.is_not(None))
    ).all()
    total = len(bounced_ats)
    bounced = sum(1 for bounced_at in bounced_ats if bounced_at is not None)
    return bounced, total


def check_bounce_rate(session: Session) -> Optional[str]:
    """Bounce oranı eşiği aştıysa gönderimi durdurur ve Türkçe sebebi döner; aksi halde `None`.

    En az `bounce_min_sample` gerçek gönderim yapılmadan oran hesaplanmaz (2 mailin 1'i geri
    dönse "%50 bounce" demek yanıltıcı olurdu) — `bounce_min_sample` bu yüzden var. Sebep,
    `sending_paused` zaten açık olsa bile hesaplanıp döndürülür (çağıran loglayabilsin diye);
    yalnızca `trigger_pause` var olan bir sebebi EZMEZ.
    """
    min_sample = get_setting(session, KEY_BOUNCE_MIN_SAMPLE)
    pause_rate = get_setting(session, KEY_BOUNCE_PAUSE_RATE)

    bounced, total = bounce_stats(session)
    if total < min_sample:
        return None

    rate = bounced / total
    if rate <= pause_rate:
        return None

    reason = (
        f"bounce oranı güvenlik eşiğini aştı ({bounced}/{total} gönderim = %{rate * 100:.0f}, "
        f"eşik %{pause_rate * 100:.0f}); gönderim otomatik durduruldu (sending_paused=true). "
        "E-posta adreslerini gözden geçirdikten sonra: python scripts/set_setting.py "
        "sending_paused false"
    )
    trigger_pause(session, reason)
    return reason
