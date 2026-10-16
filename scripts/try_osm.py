"""Tek seferlik deneme: bir meslek + il için OpenStreetMap'te kaç firma bulunduğunu listeler.

Kullanım:
    python scripts/try_osm.py --meslek ic_mimarlik --il 06 --dry-run

Veritabanına HİÇBİR ŞEY yazılmaz (bu betik yalnızca deneme modundadır, `--dry-run` zorunludur).
Gerçek Overpass API'sine tek istek atar; sonuç `data/cache/osm/` altında 7 gün önbelleklenir,
ikinci çalıştırma ağa çıkmaz. Ayrıntı: `src/company_finder/osm_source.py`.
"""
import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.company_finder.osm_source import OsmSource  # noqa: E402
from src.core.cities import load_cities  # noqa: E402
from src.core.professions import load_profession_presets  # noqa: E402

logger = logging.getLogger(__name__)

MAX_LISTED = 100


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--meslek", required=True, help="Meslek paketi kimliği (ör. ic_mimarlik)")
    parser.add_argument("--il", required=True, help="İl plaka kodu (ör. 06)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Zorunlu: veritabanına hiçbir şey yazmadan yalnızca sonucu listeler",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args()
    if not args.dry_run:
        logger.error("Bu betik yalnızca deneme içindir; --dry-run ekleyin (veritabanına yazılmaz).")
        sys.exit(2)

    professions = load_profession_presets()
    cities = load_cities()
    profession = professions.get(args.meslek)
    city = cities.get(args.il)
    if profession is None:
        logger.error(f"Meslek bulunamadı: {args.meslek!r}. Seçenekler: {', '.join(sorted(professions))}")
        sys.exit(1)
    if city is None:
        logger.error(f"İl kodu bulunamadı: {args.il!r} (iki haneli plaka kodu, ör. 06)")
        sys.exit(1)
    if not profession.osm_tags:
        logger.error(f"{profession.id} paketinde osm_tags yok; OSM bu meslekte kullanılmaz.")
        sys.exit(1)

    logger.info(f"{profession.name_tr} / {city.name_tr} — etiketler: {', '.join(profession.osm_tags)}")
    leads = list(OsmSource(profession, city).discover())

    logger.info("=" * 70)
    for lead in leads[:MAX_LISTED]:
        logger.info(f"  + {lead.name} — {lead.website}")
    if len(leads) > MAX_LISTED:
        logger.info(f"  … ve {len(leads) - MAX_LISTED} firma daha")
    logger.info(f"DENEME: {len(leads)} firma bulundu (web sitesi olan, sosyal medya hariç); "
                "veritabanına hiçbir şey yazılmadı.")
    logger.info("Veri © OpenStreetMap katkıcıları (ODbL).")
    if not leads:
        logger.info("Sonuç boşsa ağ hatası olmuş olabilir (yukarıdaki uyarılara bakın) ya da "
                    "OSM'de bu ilde bu tür kayıt az.")


if __name__ == "__main__":
    main()
