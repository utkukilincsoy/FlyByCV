"""Tek bir şirkete başvuru mailini gönderir (ya da hiç göndermeden .eml olarak kaydeder).

Kullanım:
    python scripts/send_one.py --company firma.com          # ayarlara göre gönderir
    python scripts/send_one.py --company firma.com --eml    # hiç göndermeden output/'a .eml yazar

Başvuru (CV PDF + mail metni) yoksa otomatik olarak hazırlanır. Test modu AÇIKKEN (varsayılan)
mail kullanıcının kendi adresine gider ve veritabanı kaydı değişmez. Test modunu kapatmak için:
`python scripts/set_setting.py test_mode false`.

Bu script MANUEL modda çalışır: rol adresi olmayan bir adrese göndermeden önce açıkça onay ister
(CLAUDE.md kural 10 — "yalnızca manuel modda, kullanıcı onaylarsa gönderilir").
"""
import argparse
import logging
import sys
from pathlib import Path

from sqlmodel import Session, select

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.db import get_engine, get_session, init_db  # noqa: E402
from src.core.logging_setup import quiet_noisy_loggers  # noqa: E402
from src.core.mail_providers import resolve_mail_connection  # noqa: E402
from src.core.models import Company, normalize_domain  # noqa: E402
from src.core.settings import KEY_SENDING_PAUSED, KEY_TEST_MODE, get_setting  # noqa: E402
from src.cv_builder.application_builder import build_application  # noqa: E402
from src.cv_builder.profile_loader import load_profile  # noqa: E402
from src.sender.rate_limiter import can_send_now, is_hard_cap_block, load_limits  # noqa: E402
from src.sender.smtp_sender import (  # noqa: E402
    build_message_for_application,
    load_account,
    resolve_identity,
    save_eml,
    send_application,
)

logger = logging.getLogger(__name__)

PROFILE_PATH = PROJECT_ROOT / "data" / "master_profile.json"
OUTPUT_DIR = PROJECT_ROOT / "output"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--company", required=True, help="Şirketin alan adı veya web sitesi")
    parser.add_argument(
        "--eml",
        action="store_true",
        help="Hiç göndermeden output/ altına .eml dosyası kaydet (Outlook/Thunderbird önizlemesi)",
    )
    return parser.parse_args()


def _find_company(session: Session, domain: str) -> Company:
    company = session.exec(select(Company).where(Company.domain == domain)).first()
    if company is None:
        logger.error(
            f"'{domain}' bulunamadı. Önce şirketleri içe aktarın: "
            f"python scripts/import_companies.py <dosya>"
        )
        sys.exit(1)
    return company


def _print_summary(company: Company, application, identity, test_mode: bool) -> None:
    logger.info("=" * 70)
    logger.info(f"Şirket: {company.name} ({company.domain})")
    logger.info(f"Şirketin adresi: {company.email or '-'}")
    logger.info(f"Rol adresi mi: {company.email_is_role}")
    logger.info(f"Test modu: {'AÇIK (kendi adresinize gider)' if test_mode else 'KAPALI (gerçek gönderim)'}")
    logger.info(f"Gönderen: {identity.name} <{identity.email}>")
    logger.info(f"Konu: {application.mail_subject}")
    logger.info(f"Ek: {application.cv_pdf_path}")


def _confirm(prompt: str, expected: str = "e") -> bool:
    answer = input(prompt).strip().lower()
    return answer in (expected, "evet") if expected == "e" else answer == expected


def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    quiet_noisy_loggers()

    domain = normalize_domain(args.company)
    profile = load_profile(PROFILE_PATH)
    engine = get_engine()
    init_db(engine=engine)

    with get_session(engine=engine) as session:
        company = _find_company(session, domain)
        application = build_application(session, company, profile, output_dir=OUTPUT_DIR)
        session.commit()  # PDF diske yazıldı; gönderim iptal olsa bile kayıt kalsın

        identity = resolve_identity(session, profile)
        test_mode = get_setting(session, KEY_TEST_MODE)
        _print_summary(company, application, identity, test_mode)

        if args.eml:
            message = build_message_for_application(session, application, profile)
            path = save_eml(message, OUTPUT_DIR / company.domain / "mail.eml")
            logger.info(f".eml dosyası kaydedildi: {path}")
            return

        # Acil durdur MUTLAK: aşılamaz. Kodda sabit üst sınırlar (HARD_MAX_DAILY/HARD_MAX_HOURLY,
        # PLAN.md Adım 10.8a) da AŞILAMAZ. Limit/mesai kısıtı kullanıcının KENDİ (tavanın altındaki)
        # ayarından geliyorsa burada sadece uyarı + açık onay — tek seferlik manuel gönderim
        # bilinçli bir eylem, ama limitin FARKINDA OLMADAN aşılması değil (CLAUDE.md kural 5).
        window = can_send_now(session, test_mode=bool(test_mode))
        if not window.allowed:
            if get_setting(session, KEY_SENDING_PAUSED):
                logger.error(f"GÖNDERİLMEDİ: {window.reason}")
                sys.exit(1)
            if is_hard_cap_block(window, load_limits(session)):
                logger.error(f"GÖNDERİLMEDİ (sabit üst sınır, aşılamaz): {window.reason}")
                sys.exit(1)
            logger.warning(f"DİKKAT: {window.reason}")
            if not _confirm("Yine de göndermek için 'gonder' yazın: ", expected="gonder"):
                logger.info("İptal edildi.")
                return

        auto_mode = True
        if company.email_is_role is False:
            logger.warning(
                "DİKKAT: bu adres kariyer@/info@ gibi bir rol adresi değil, kişi adresi gibi "
                "görünüyor."
            )
            if not _confirm("Göndermek istediğinize emin misiniz? (e/h): "):
                logger.info("İptal edildi.")
                return
            auto_mode = False  # manuel onay alındı (CLAUDE.md kural 10)

        if not test_mode:
            if not _confirm(
                f"GERÇEK GÖNDERİM yapılacak, mail {company.email} adresine gidecek. "
                f"Onaylamak için 'gonder' yazın: ",
                expected="gonder",
            ):
                logger.info("İptal edildi.")
                return

        account = load_account(identity, connection=resolve_mail_connection(session))
        if account is None:
            sys.exit(1)

        result = send_application(
            session, application, profile, auto_mode=auto_mode, account=account
        )
        session.commit()

        if result.refused_reason:
            logger.error(f"GÖNDERİLMEDİ: {result.refused_reason}")
            sys.exit(1)
        if result.error:
            logger.error(f"GÖNDERİLMEDİ: {result.error}")
            sys.exit(1)
        if result.test_mode:
            logger.info(
                f"TEST MODU: mail {result.envelope_to} adresine gönderildi "
                f"(gerçek hedef: {result.intended_to}), kayıt \"queued\" bırakıldı."
            )
        else:
            logger.info(f"GÖNDERİLDİ: {result.envelope_to} (Message-ID: {result.message_id})")


if __name__ == "__main__":
    main()
