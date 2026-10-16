"""Bilinen bir ayarı okur veya değiştirir (Faz 5 ayarlar arayüzü gelene kadar).

Kullanım:
    python scripts/set_setting.py                     # tüm ayarları ve şu anki değerlerini listeler
    python scripts/set_setting.py test_mode false     # ayarı yazar

Değerler otomatik olarak yorumlanır: "true"/"false" → mantıksal (bool), "null" → boş (None),
tamsayı görünüyorsa sayı, aksi halde düz metin.

`test_mode` kapatılırken büyük bir uyarı basılır ve onay istenir: bundan sonra gönderilen
mailler gerçek şirketlere gider, kullanıcının kendi adresine değil.
"""
import logging
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.db import get_engine, get_session, init_db  # noqa: E402
from src.core.settings import DEFAULTS, KEY_TEST_MODE, get_setting, set_setting  # noqa: E402

logger = logging.getLogger(__name__)


def _parse_value(raw: str) -> Any:
    lowered = raw.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


def _list_settings(session) -> None:
    logger.info("Bilinen ayarlar ve şu anki değerleri:")
    for key in sorted(DEFAULTS):
        logger.info(f"  {key} = {get_setting(session, key)!r}")


def _confirm_test_mode_off() -> bool:
    print(
        "\nDİKKAT: test_mode kapatılıyor. Bundan sonra gönderilen mailler GERÇEK şirket "
        "adreslerine gidecek, kendi adresinize değil.\n"
    )
    answer = input("Onaylıyor musunuz? (e/h): ").strip().lower()
    return answer in ("e", "evet")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    engine = get_engine()
    init_db(engine=engine)

    with get_session(engine=engine) as session:
        if len(sys.argv) == 1:
            _list_settings(session)
            return

        if len(sys.argv) != 3:
            logger.error("Kullanım: python scripts/set_setting.py <ayar> <değer>")
            sys.exit(1)

        key, raw_value = sys.argv[1], sys.argv[2]
        value = _parse_value(raw_value)

        if key == KEY_TEST_MODE and value is False and not _confirm_test_mode_off():
            logger.info("İptal edildi, ayar değiştirilmedi.")
            return

        try:
            set_setting(session, key, value)
        except KeyError as error:
            logger.error(str(error))
            sys.exit(1)

        session.commit()
        logger.info(f"{key} = {value!r} olarak kaydedildi.")


if __name__ == "__main__":
    main()
