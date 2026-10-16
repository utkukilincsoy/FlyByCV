"""Gerçek profille iki örnek CV PDF'i üretir: ana CV ve şirkete özel CV."""
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.cv_builder.profile_loader import load_profile  # noqa: E402
from src.cv_builder.renderer import cv_filename, render_pdf  # noqa: E402
from src.cv_builder.tag_matcher import tailor  # noqa: E402

logger = logging.getLogger(__name__)

PROFILE_PATH = PROJECT_ROOT / "data" / "master_profile.json"
OUTPUT_DIR = PROJECT_ROOT / "output"

# Örnek şirket: ağ yönetimi alanında çalışan hayali bir firma.
EXAMPLE_DOMAIN = "ornek-ag.com"
EXAMPLE_TAGS = ["ag-yonetimi"]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    profile = load_profile(PROFILE_PATH)

    general = tailor(profile, [], enabled=False)
    general_path = OUTPUT_DIR / cv_filename(profile)
    render_pdf(profile, general, general_path)

    tailored = tailor(profile, EXAMPLE_TAGS, enabled=True)
    tailored_path = OUTPUT_DIR / cv_filename(profile, EXAMPLE_DOMAIN)
    render_pdf(profile, tailored, tailored_path)

    logger.info(f"Eşleşen etiketler: {tailored.matched_tags or '-'}")
    logger.info(f"Ana CV: {general_path}")
    logger.info(f"Özelleştirilmiş CV: {tailored_path}")


if __name__ == "__main__":
    main()
