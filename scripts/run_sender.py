"""Gönderim kuyruğunu işler: limitleri/mesai saatini uygular, mailleri sırayla gönderir.

Kullanım:
    python scripts/run_sender.py                # send_mode ayarına göre, sınırsız
    python scripts/run_sender.py --max 5         # en fazla 5 BAŞARILI gönderim

Manuel modda (varsayılan) yalnızca `python scripts/queue_cli.py approve <alan adı>` ile
onaylanmış başvurular gönderilir. Otomatik moda geçmek için:
`python scripts/set_setting.py send_mode auto`.

Acil durdur: python scripts/set_setting.py sending_paused true
Ctrl+C ile güvenle durur — o ana kadar yapılan gönderimler kalıcıdır.
"""
import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.db import get_engine, get_session, init_db  # noqa: E402
from src.core.logging_setup import quiet_noisy_loggers  # noqa: E402
from src.core.settings import (  # noqa: E402
    KEY_SEND_MODE,
    KEY_SENDING_PAUSED,
    KEY_TEST_MODE,
    get_setting,
)
from src.cv_builder.profile_loader import load_profile  # noqa: E402
from src.sender.queue import list_queue  # noqa: E402
from src.sender.rate_limiter import can_send_now  # noqa: E402
from src.sender.worker import process_queue  # noqa: E402

logger = logging.getLogger(__name__)

PROFILE_PATH = PROJECT_ROOT / "data" / "master_profile.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--max", type=int, default=None, help="En fazla kaç BAŞARILI gönderim yapılsın"
    )
    return parser.parse_args()


def _print_startup_summary(session) -> None:
    mode = get_setting(session, KEY_SEND_MODE)
    test_mode = get_setting(session, KEY_TEST_MODE)
    queue_size = len(list_queue(session))
    logger.info(
        f"Mod: {mode} | Test modu: {'AÇIK (mailler kendinize gider)' if test_mode else 'KAPALI'} "
        f"| Kuyrukta bekleyen: {queue_size}"
    )
    window = can_send_now(session, test_mode=bool(test_mode))
    if window.allowed:
        logger.info("Şu anda gönderim yapılabilir.")
    else:
        logger.info(f"Şu anda gönderim yapılamaz: {window.reason}")


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    quiet_noisy_loggers()

    profile = load_profile(PROFILE_PATH)
    engine = get_engine()
    init_db(engine=engine)

    with get_session(engine=engine) as session:
        if get_setting(session, KEY_SENDING_PAUSED):
            logger.error(
                "Gönderim duraklatılmış (sending_paused=true). Devam etmek için: "
                "python scripts/set_setting.py sending_paused false"
            )
            sys.exit(1)

        _print_startup_summary(session)

        result = process_queue(session, profile, max_count=args.max)

        logger.info("=" * 70)
        logger.info(
            f"Gönderildi: {result.sent_count} | Atlandı: {result.refused_count} | "
            f"Hatalı: {result.failed_count}"
        )
        for domain, reason in result.refused:
            logger.info(f"  atlandı: {domain} — {reason}")
        for domain, error in result.failed:
            logger.warning(f"  hata: {domain} — {error}")
        if result.paused_by_worker:
            logger.warning("Art arda 3 hata sonrası gönderim otomatik durduruldu.")
        if result.stop_reason:
            logger.info(f"Durma sebebi: {result.stop_reason}")
        if result.interrupted_domain:
            logger.warning(
                f"DİKKAT: kesinti tam '{result.interrupted_domain}' şirketinin gönderimi "
                'sırasında oldu; durumu belirsiz. Gmail "Gönderilenler" klasörünü kontrol edip '
                f"python scripts/queue_cli.py mark-sent {result.interrupted_domain} (mail "
                f"gittiyse) veya mark-queued {result.interrupted_domain} (gitmediyse) çalıştırın."
            )


if __name__ == "__main__":
    main()
