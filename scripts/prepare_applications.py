"""E-postası olan ve henüz başvurusu hazırlanmamış şirketler için CV PDF'i + mail metni üretir.

Kullanım:
    python scripts/prepare_applications.py                # tüm adaylar
    python scripts/prepare_applications.py --limit 5      # sadece ilk 5 şirket

Şirket başına Playwright ile PDF üretir (yavaş, birkaç saniye); her şirketten sonra commit
edilir, Ctrl+C ile durdurulursa öncekiler kaybolmaz (`enrich_companies.py` ile aynı desen).
Bir şirket patlarsa (bozuk profil, Playwright hatası) hata Türkçe loglanır, diğerlerine devam
edilir. Üretilen başvurular `queued` durumunda kuyruğa girer — göndermek için:
`python scripts/queue_cli.py list` / `approve` ve `python scripts/run_sender.py`.
"""
import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlmodel import select  # noqa: E402

from src.core.db import get_engine, get_session, init_db  # noqa: E402
from src.core.logging_setup import quiet_noisy_loggers  # noqa: E402
from src.core.models import Application, Company  # noqa: E402
from src.cv_builder.application_builder import build_application  # noqa: E402
from src.cv_builder.profile_loader import load_profile  # noqa: E402

logger = logging.getLogger(__name__)

PROFILE_PATH = PROJECT_ROOT / "data" / "master_profile.json"
OUTPUT_DIR = PROJECT_ROOT / "output"
#: --limit verilmeden bu sayının üzerinde aday varsa onay istenir (Playwright yavaş).
CONFIRM_THRESHOLD = 20


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None, help="En fazla kaç şirket işlensin")
    return parser.parse_args()


def _select_candidates(session, *, limit) -> list[Company]:
    query = (
        select(Company)
        .where(Company.email.is_not(None))
        .where(Company.id.not_in(select(Application.company_id)))
    )
    if limit:
        query = query.limit(limit)
    return list(session.exec(query).all())


def _confirm(prompt: str) -> bool:
    answer = input(prompt).strip().lower()
    return answer in ("e", "evet")


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    quiet_noisy_loggers()

    profile = load_profile(PROFILE_PATH)
    engine = get_engine()
    init_db(engine=engine)

    with get_session(engine=engine) as session:
        candidates = _select_candidates(session, limit=args.limit)
        if not candidates:
            logger.info("Başvurusu hazırlanacak şirket yok (hepsi hazır veya e-postası yok).")
            return

        if args.limit is None and len(candidates) > CONFIRM_THRESHOLD:
            if not _confirm(
                f"{len(candidates)} şirket için CV hazırlanacak, bu biraz sürebilir. "
                f"Devam edilsin mi? (e/h): "
            ):
                logger.info("İptal edildi.")
                return

        logger.info(f"{len(candidates)} şirket için başvuru hazırlanacak.")
        prepared, failed = 0, 0
        for company in candidates:
            try:
                build_application(session, company, profile, output_dir=OUTPUT_DIR)
                session.commit()
            except Exception as error:
                logger.error(f"{company.domain}: başvuru hazırlanamadı ({error!r})")
                session.rollback()
                failed += 1
                continue

            role = (
                "rol adresi"
                if company.email_is_role
                else "kişi adı gibi görünüyor, otomatik gönderim YASAK (CLAUDE.md 10. kural)"
            )
            logger.info(f"{company.name} ({company.domain}): hazır — {company.email} ({role})")
            prepared += 1

    logger.info(f"Bitti: {prepared} başvuru hazırlandı, {failed} hata.")


if __name__ == "__main__":
    main()
