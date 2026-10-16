"""Bir CSV/Excel dosyasındaki şirket listesini veritabanına aktarır.

Kullanım:
    python scripts/import_companies.py data/companies.example.csv
    python scripts/import_companies.py liste.xlsx --dry-run

`--dry-run` verildiğinde veritabanına HİÇBİR ŞEY yazılmaz (veritabanı dosyası daha önce yoksa
oluşturulmaz bile); sadece rapor gösterilir. Sütun adları esnektir, ayrıntı için bkz.
`src/company_finder/csv_import.py`.
"""
import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.company_finder.csv_import import ImportReport, RowNote, import_companies  # noqa: E402
from src.core.db import DB_PATH, configure_sqlite_engine, get_engine, get_session, init_db  # noqa: E402

logger = logging.getLogger(__name__)

MAX_DETAIL_LINES = 50


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dosya", type=Path, help="İçe aktarılacak .csv veya .xlsx dosyası")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Veritabanına hiçbir şey yazmadan sadece raporu göster",
    )
    return parser.parse_args()


def _memory_engine():
    """Bellek içi SQLite motoru; gerçek `data/app.db`'ye hiç dokunmaz."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    configure_sqlite_engine(engine)
    init_db(engine=engine)
    return engine


def _resolve_engine(dry_run: bool):
    """Dry-run'da gerçek veritabanı dosyası yoksa, onu OLUŞTURMADAN bellek içi motor kullanır."""
    if dry_run and not DB_PATH.exists():
        logger.info("Veritabanı henüz yok, tekrar kontrolü boş listeyle yapıldı (--dry-run)")
        return _memory_engine()
    engine = get_engine()
    init_db(engine=engine)
    return engine


def _print_notes(title: str, notes: list[RowNote], marker: str) -> None:
    if not notes:
        return
    logger.info(f"{title} ({len(notes)}):")
    for note in notes[:MAX_DETAIL_LINES]:
        display_name = note.name or "(ad yok)"
        logger.info(f"  {marker} {note.row}. satır: {display_name} — {note.reason}")
    remaining = len(notes) - MAX_DETAIL_LINES
    if remaining > 0:
        logger.info(f"  … ve {remaining} satır daha (ayrıntı için DEBUG)")


def _print_report(report: ImportReport, dosya: Path, dry_run: bool) -> None:
    logger.info("=" * 70)
    logger.info("İÇE AKTARMA RAPORU")
    logger.info(f"Dosya: {dosya}")
    if dry_run:
        logger.info("MOD: DENEME – veritabanına hiçbir şey yazılmadı")
    if report.added:
        logger.info(f"Eklenen ({len(report.added)}):")
        for added in report.added:
            logger.info(f"  + {added.row}. satır: {added.name} ({added.domain})")
    _print_notes("Uyarı", report.warnings, "!")
    _print_notes("Atlanan", report.skipped, "-")
    logger.info(report.summary())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args()

    if not args.dosya.exists():
        logger.error(f"Dosya bulunamadı: {args.dosya}")
        sys.exit(1)

    engine = _resolve_engine(args.dry_run)

    try:
        with get_session(engine=engine) as session:
            try:
                report = import_companies(session, args.dosya, dry_run=args.dry_run)
            except ValueError as exc:
                logger.error(str(exc))
                sys.exit(1)

            _print_report(report, args.dosya, args.dry_run)

            if not args.dry_run:
                try:
                    session.commit()
                    logger.info(f"Veritabanına yazıldı: {DB_PATH}")
                except IntegrityError:
                    session.rollback()
                    logger.error(
                        "Veritabanına yazılamadı: bir alan adı tekrarı kaydetme anında "
                        "oluştu, hiçbir satır kaydedilmedi. Dosyayı kontrol edip tekrar deneyin."
                    )
                    sys.exit(1)
    except OSError as exc:
        logger.error(f"Dosya okunamadı: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
