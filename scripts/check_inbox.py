"""Gelen kutusunu bir kez tarar: eşleşen cevapları kaydeder, bounce'ları işaretler.

Kullanım:
    python scripts/check_inbox.py

Yalnızca SALT OKUNUR IMAP erişimi kullanır (bkz. `src/inbox/imap_reader.py`); hiçbir mail
silinmez/işaretlenmez/okundu yapılmaz. Gmail kimlik bilgisi SMTP ile AYNIDIR
(`python scripts/set_mail_password.py` ile kaydedilir).
"""
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.db import get_engine, get_session, init_db  # noqa: E402
from src.core.logging_setup import quiet_noisy_loggers  # noqa: E402
from src.cv_builder.profile_loader import load_profile  # noqa: E402
from src.inbox.categories import REPLY_LABELS_TR  # noqa: E402
from src.inbox.imap_reader import ImapConnectionError  # noqa: E402
from src.inbox.service import check_inbox  # noqa: E402

logger = logging.getLogger(__name__)

PROFILE_PATH = PROJECT_ROOT / "data" / "master_profile.json"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    quiet_noisy_loggers()

    profile = load_profile(PROFILE_PATH)
    engine = get_engine()
    init_db(engine=engine)

    with get_session(engine=engine) as session:
        try:
            result = check_inbox(session, profile)
        except ImapConnectionError as error:
            logger.error(f"Gelen kutusu kontrol edilemedi: {error}")
            sys.exit(1)

        if result.error:
            if result.no_applications_sent:
                logger.info(result.error)
                return
            logger.error(result.error)
            sys.exit(1)

        logger.info(
            f"{result.headers_fetched} başlık tarandı, {result.candidates} aday bulundu, "
            f"{result.new_replies} yeni cevap kaydedildi "
            f"({result.duplicate_skipped} zaten kayıtlıydı)."
        )
        for category, count in result.new_reply_categories.items():
            label = REPLY_LABELS_TR.get(category, category)
            logger.info(f"  {label}: {count}")
        for domain in result.bounced_domains:
            logger.warning(f"  ⚠ teslim edilemedi: {domain}")
        if result.missing_message_id_skipped:
            logger.warning(
                f"{result.missing_message_id_skipped} eşleşme Message-ID eksik olduğu için "
                "kaydedilemedi"
            )


if __name__ == "__main__":
    main()
