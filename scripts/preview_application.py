"""Gerçek profille üç örnek başvuru hazırlar ve oluşan mailleri yazdırır.

Kullanım:
    python scripts/preview_application.py          # anahtar kelime modu (ağa çıkmaz, ücretsiz)
    python scripts/preview_application.py --llm    # kayıtlı anahtarlı LLM sağlayıcısıyla

`--llm` verildiğinde şirket metinleri gerçekten LLM'e gönderilir (küçük çağrılar, ücretli
olabilir). Fark iki yerde görülür: etiketi olmayan şirket otomatik etiketlenir ve mail
cümlesinde etiket adları yerine şirketin kendi diliyle yazılmış ifade kullanılır.

Geçici (bellek içi) veritabanı kullanır, `data/app.db`'ye dokunmaz.
"""
import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.db import configure_sqlite_engine, get_session, init_db  # noqa: E402
from src.core.logging_setup import quiet_noisy_loggers  # noqa: E402
from src.core.models import Company, normalize_domain  # noqa: E402
from src.core.secrets import get_api_key  # noqa: E402
from src.core.settings import KEY_LLM_PROVIDER, set_setting  # noqa: E402
from src.cv_builder.application_builder import build_application  # noqa: E402
from src.cv_builder.profile_loader import load_profile  # noqa: E402
from src.llm.providers import API_KEY_PROVIDERS  # noqa: E402

logger = logging.getLogger(__name__)

PROFILE_PATH = PROJECT_ROOT / "data" / "master_profile.json"
OUTPUT_DIR = PROJECT_ROOT / "output"

# (ad, alan adı, etiketler, tanıtım metni). Üçüncü şirketin etiketi BOŞ: etiketler
# tanıtım metninden çıkarılacak. Metinler kısa tutulmadı — 30 karakterin altındaki
# metinler için LLM'e hiç gidilmez (src/llm/json_provider.py).
COMPANIES = (
    (
        "Örnek Ağ A.Ş.",
        "ornek-ag.com",
        ["ag-yonetimi", "siber-guvenlik"],
        "2010 yılından bu yana kurumsal ağ altyapısı kuruyor ve işletiyoruz. Cisco "
        "yönlendirici ve switch kurulumu, LAN/WAN tasarımı, güvenlik duvarı "
        "yapılandırması ve 7/24 ağ izleme hizmeti veriyoruz. Sızma testi ve KVKK "
        "uyum danışmanlığı da hizmetlerimiz arasındadır.",
    ),
    (
        "Örnek Oyun Stüdyosu",
        "ornek-oyun.com",
        ["oyun-gelistirme"],
        "Mobil ve PC platformları için bağımsız oyunlar geliştiriyoruz. Unity ve "
        "Unreal Engine ile üretim yapan ekibimiz, oyun tasarımından karakter "
        "animasyonuna kadar tüm süreci stüdyo içinde yürütüyor.",
    ),
    (
        "Örnek Bulut Bilişim",
        "ornek-bulut.com",
        [],  # etiket yok: metinden çıkarılacak
        "Şirketlerin sistemlerini bulut bilişim altyapısına taşıyoruz. AWS ve Azure "
        "üzerinde Kubernetes kümeleri kuruyor, Docker konteynerleri ve Terraform ile "
        "otomatik kurulum hatları (CI/CD) hazırlıyoruz. Linux sunucu yönetimi ve "
        "yedekleme çözümleri de sunuyoruz.",
    ),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Anahtarı kayıtlı LLM sağlayıcısını kullan (gerçek ağ çağrısı yapar)",
    )
    return parser.parse_args()


def _memory_engine():
    """Bellek içi SQLite motoru (StaticPool olmadan her bağlantı boş veritabanı açar)."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    configure_sqlite_engine(engine)
    init_db(engine=engine)
    return engine


def _enable_llm(session: Session) -> None:
    """Anahtarı şifre kasasında kayıtlı ilk sağlayıcıyı ayara yazar.

    Kayıtlı anahtar yoksa uyarır ve anahtar kelime modunda devam eder: önizleme
    çalışmaya devam etsin.
    """
    name = next((provider for provider in API_KEY_PROVIDERS if get_api_key(provider)), None)
    if name is None:
        logger.warning(
            "Hiçbir sağlayıcının API anahtarı kayıtlı değil (python scripts/set_api_key.py), "
            "anahtar kelime modunda devam ediliyor"
        )
        return

    set_setting(session, KEY_LLM_PROVIDER, name)
    logger.info(f"LLM modu açık, sağlayıcı: {name}")


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    quiet_noisy_loggers()

    profile = load_profile(PROFILE_PATH)
    engine = _memory_engine()

    with get_session(engine=engine) as session:
        if args.llm:
            _enable_llm(session)
        else:
            logger.info("Anahtar kelime modu (LLM kullanılmıyor). LLM için: --llm")

        for name, domain, tags, about_text in COMPANIES:
            company = Company(
                name=name,
                domain=normalize_domain(domain),
                sector_tags=list(tags),
                about_text=about_text,
                source="manual",
            )
            session.add(company)
            session.flush()

            application = build_application(session, company, profile, output_dir=OUTPUT_DIR)

            logger.info("=" * 70)
            logger.info(f"Şirket: {name} ({domain})")
            logger.info(f"Şirket etiketleri: {company.sector_tags or '-'}")
            logger.info(f"Eşleşen etiketler: {application.matched_tags or '-'}")
            logger.info(f"Özelleştirildi mi: {'evet' if application.tailored else 'hayır'}")
            logger.info(f"CV PDF: {application.cv_pdf_path}")
            logger.info(f"Konu: {application.mail_subject}")
            logger.info(f"Gövde:\n{application.mail_body}")

        session.commit()


if __name__ == "__main__":
    main()
