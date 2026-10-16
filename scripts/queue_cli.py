"""Gönderim kuyruğunu listeler, onaylar, reddeder ya da yarıda kalmış bir gönderimi kurtarır.

Kullanım:
    python scripts/queue_cli.py list
    python scripts/queue_cli.py approve firma.com
    python scripts/queue_cli.py reject firma.com
    python scripts/queue_cli.py mark-sent firma.com     # kurtarma: 'sending' -> 'sent'
    python scripts/queue_cli.py mark-queued firma.com   # kurtarma: 'sending' -> 'queued'

`sending` durumu: gönderim Ctrl+C/çökme sırasında yarıda kalmışsa oluşur (bkz.
`src/sender/smtp_sender.py`) — mail gitmiş OLABİLİR ama durum belirsizdir. Gmail
"Gönderilenler" klasörüne bakıp `mark-sent` (gitti) ya da `mark-queued` (gitmedi, tekrar
denenebilir) ile karar verin. Bu iki komut YALNIZCA `sending` durumundaki kayıtlarda çalışır.
"""
import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.db import get_engine, get_session, init_db  # noqa: E402
from src.core.logging_setup import quiet_noisy_loggers  # noqa: E402
from src.core.models import STATUS_SENDING  # noqa: E402
from src.core.settings import KEY_TEST_MODE, get_setting  # noqa: E402
from src.core.timeutil import to_istanbul  # noqa: E402
from src.sender.queue import (  # noqa: E402
    approve_application,
    find_application_by_domain,
    is_auto_eligible,
    list_queue,
    mark_queued,
    mark_sent,
    reject_application,
)
from src.sender.rate_limiter import can_send_now  # noqa: E402

logger = logging.getLogger(__name__)

_ACTIONS = {
    "approve": approve_application,
    "reject": reject_application,
    "mark-sent": mark_sent,
    "mark-queued": mark_queued,
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="Kuyruktaki başvuruları listele")
    subparsers.add_parser("approve", help="Başvuruyu onayla (queued -> approved)").add_argument(
        "domain", help="Şirketin alan adı veya web sitesi"
    )
    subparsers.add_parser("reject", help="Başvuruyu reddet (queued -> rejected)").add_argument(
        "domain", help="Şirketin alan adı veya web sitesi"
    )
    subparsers.add_parser(
        "mark-sent", help="Kurtarma: 'sending' durumundaki başvuruyu 'sent' yap"
    ).add_argument("domain", help="Şirketin alan adı veya web sitesi")
    subparsers.add_parser(
        "mark-queued", help="Kurtarma: 'sending' durumundaki başvuruyu 'queued' yap"
    ).add_argument("domain", help="Şirketin alan adı veya web sitesi")

    return parser.parse_args()


def _format_tristate(value) -> str:
    if value is True:
        return "evet"
    if value is False:
        return "hayır"
    return "bilinmiyor"


def _print_list(session) -> None:
    applications = list_queue(session)
    if not applications:
        logger.info("Kuyrukta gösterilecek başvuru yok.")
    else:
        by_status: dict[str, list] = {}
        for application in applications:
            by_status.setdefault(application.status, []).append(application)

        for status, rows in by_status.items():
            logger.info(f"--- {status} ({len(rows)}) ---")
            for application in rows:
                company = application.company
                if status == STATUS_SENDING:
                    logger.info(
                        f"  ⚠ {company.name} ({company.domain}): durumu BELİRSİZ, Gmail "
                        f'"Gönderilenler" klasörünü kontrol edin — kurtarma: '
                        f"mark-sent/mark-queued {company.domain}"
                    )
                    continue
                created = to_istanbul(application.created_at).strftime("%d.%m.%Y %H:%M")
                logger.info(
                    f"  {company.name} ({company.domain}) — e-posta: {company.email or '-'}, "
                    f"rol adresi: {_format_tristate(company.email_is_role)}, "
                    f"MX: {_format_tristate(company.mx_valid)}, "
                    f"otomatik moda uygun: {'evet' if is_auto_eligible(company) else 'hayır'}, "
                    f"oluşturulma: {created}"
                )

    window = can_send_now(session, test_mode=bool(get_setting(session, KEY_TEST_MODE)))
    if window.allowed:
        logger.info("Şu anda gönderim yapılabilir.")
    else:
        logger.info(f"Şu anda gönderim yapılamaz: {window.reason}")


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    quiet_noisy_loggers()

    engine = get_engine()
    init_db(engine=engine)

    with get_session(engine=engine) as session:
        if args.command == "list":
            _print_list(session)
            return

        application = find_application_by_domain(session, args.domain)
        if application is None:
            logger.error(f"'{args.domain}' için başvuru bulunamadı.")
            sys.exit(1)

        reason = _ACTIONS[args.command](session, application)
        if reason is not None:
            logger.error(reason)
            sys.exit(1)

        session.commit()
        logger.info(f"{args.domain}: '{args.command}' tamamlandı (yeni durum: {application.status}).")


if __name__ == "__main__":
    main()
