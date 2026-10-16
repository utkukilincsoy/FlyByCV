"""Bir şirket için başvuruyu hazırlar: CV PDF'i + mail metni + `Application` kaydı.

Ayarları okur, CV'yi şirkete göre sıralar, doğrulamayı geçmezse ana CV'ye düşer
(CLAUDE.md kural 1) ve aynı şirkete ikinci kayıt açmaz (kural 4).

**CV modu** (`KEY_CV_MODE`, Faz 10 Adım 10.3a): `own_pdf` iken CV özelleştirme (`tailor()`) ve
PDF üretimi (Playwright, `renderer.render_pdf`) HİÇ çalışmaz; kullanıcının `src/cv_builder/
own_cv.py::set_own_cv` ile kopyaladığı sabit dosya (`paths.own_cv_path()`) aynen eklenir. Dosya
henüz yüklenmemişse `ValueError` fırlatılır (çağıran -- `src/ui/jobs.py::build_job` -- bunu
şirket başına yakalayıp Türkçe hata listesine ekler).

LLM'in bu akışta tek işi var, şirketin tanıtım metnine (`Company.about_text`) bağlıdır ve metin
yoksa hiç çağrı yapılmaz: **Etiketleme** — şirketin etiketi yoksa metninden çıkarılır ve
`Company`'ye kaydedilir. `tailor()`'dan ÖNCE yapılır, çünkü hem CV sıralaması hem de (ileride)
mail cümlesi bu etiketleri kullanır. LLM hata verse bile akış bozulmaz: `get_provider()` her
zaman `SafeProvider` döndürür, o da anahtar kelime sağlayıcısına düşer (kural 3).

Mail metni kullanıcının Ayarlar'da düzenlediği şablondan üretilir (`render_current_mail_text`
-> `mail_template.fill_mail_template`, PLAN.md Adım 10.4). `{hedef}`/`{meslek}` Ayarlar'daki
seçime göre doldurulur (`KEY_APPLICATION_TARGET`/`KEY_PROFESSION`/`KEY_MAIL_LANGUAGE`). LLM'in
şirkete özel giriş cümlesi ürettiği eski `company_focus_phrase()`/`_focus_phrase()` akışı bu
şablona henüz bağlı DEĞİL — `{giris_cumlesi}` yer tutucusu geldiğinde yeniden bağlanacak;
`company_focus_phrase()` ve `src/llm/phrase_validator.py` bunun için silinmedi.
"""
import logging
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from src.core import paths
from src.core.custom_profession import resolve_profession
from src.core.models import Application, Company, STATUS_QUEUED
from src.core.settings import (
    CV_MODE_OWN_PDF,
    KEY_APPLICATION_TARGET,
    KEY_CV_MODE,
    KEY_MAIL_LANGUAGE,
    KEY_MAIL_TEMPLATE_BODY,
    KEY_MAIL_TEMPLATE_SUBJECT,
    KEY_MAX_PROJECTS,
    KEY_TAILORING_ENABLED,
    get_setting,
)
from src.cv_builder.mail_template import fill_mail_template
from src.cv_builder.own_cv import own_cv_path_if_present
from src.cv_builder.profile_loader import MasterProfile
from src.cv_builder.renderer import company_folder_name, cv_attachment_name, render_pdf
from src.cv_builder.tag_matcher import TailoredCV, tailor
from src.cv_builder.validator import ValidationError, validate
from src.llm.base import LLMProvider
from src.llm.factory import get_provider

logger = logging.getLogger(__name__)


def _existing_application(session: Session, company_id: int) -> Optional[Application]:
    statement = select(Application).where(Application.company_id == company_id)
    return session.exec(statement).first()


def _validated_cv(
    profile: MasterProfile, tailored: TailoredCV, max_projects: Optional[int]
) -> TailoredCV:
    """Sıralamayı doğrular; geçmezse ana CV'ye düşer.

    `render_pdf` doğrulamayı kendi de yapıyor ama `max_projects` olmadan ve hata
    fırlatarak. Burada önceden doğrulamak proje sayısı kuralını da kapsar ve
    tarayıcı hiç açılmadan ana CV'ye dönmeyi sağlar.
    """
    try:
        validate(tailored, profile, max_projects=max_projects)
    except ValidationError as error:
        logger.warning(f"Özelleştirilmiş CV doğrulamayı geçemedi, ana CV kullanılıyor: {error}")
        return tailor(profile, [], enabled=False)
    return tailored


def _fill_sector_tags(session: Session, company: Company, provider: LLMProvider) -> None:
    """Şirketin etiketi yoksa tanıtım metninden çıkarır ve kaydeder.

    Etiketi dolu şirkete dokunulmaz: etiketler CSV'den ya da kullanıcıdan gelmiş olabilir,
    LLM onları ezmemeli. Boş liste dönerse de yazma yapılmaz.
    """
    if company.sector_tags or not company.about_text:
        return

    tags = provider.classify_company(company.name, company.about_text)
    if not tags:
        logger.info(f"{company.name} için etiket bulunamadı, etiketsiz devam ediliyor")
        return

    company.sector_tags = list(tags)
    session.add(company)
    session.flush()
    logger.info(f"{company.name} etiketleri metinden çıkarıldı: {tags}")


def render_current_mail_text(
    session: Session, company: Company, profile: MasterProfile
) -> tuple[str, str]:
    """Ayarlardaki GÜNCEL mail şablonunu okuyup (konu, gövde) döner.

    Hem `build_application()` (başvuru hazırlanırken) hem de `src/sender/smtp_sender.py::
    send_application()`/`build_message_for_application()` (gönderim/önizleme anında, henüz
    gönderilmemiş bir başvuru için) tarafından çağrılır — tek kaynak, iki kopya yok.
    """
    subject_template = get_setting(session, KEY_MAIL_TEMPLATE_SUBJECT)
    body_template = get_setting(session, KEY_MAIL_TEMPLATE_BODY)
    target = get_setting(session, KEY_APPLICATION_TARGET)
    language = get_setting(session, KEY_MAIL_LANGUAGE)
    profession = resolve_profession(session)
    return fill_mail_template(
        subject_template,
        body_template,
        profile,
        company.name,
        target=target,
        language=language,
        profession_name=profession.name_tr if profession else None,
    )


def build_application(
    session: Session,
    company: Company,
    profile: MasterProfile,
    output_dir: Optional[Path] = None,
) -> Application:
    """Şirket için başvuru kaydı üretir; kayıt zaten varsa mevcut olanı döndürür.

    `output_dir` verilmezse `src.core.paths.output_dir()` kullanılır — çağrı anında
    okunur (modül yüklenirken DEĞİL) ki `FLYBYCV_HOME`/`sys.frozen` değişse bile
    doğru yolu görsün.

    PDF `output_dir/<alan adı>/Ad_Soyad_CV.pdf` yoluna yazılır: ekin adı her şirkette
    aynıdır, toplu gönderim izlenimi vermesin.

    `commit` çağırana aittir (yalnızca `flush` edilir). Not: işlem geri alınsa bile
    üretilen PDF diskte kalır.

    Raises:
        ValueError: Şirket henüz veritabanına kaydedilmemişse.
        ValidationError: Ana CV de profille tutarsızsa (profilin kendisi bozuk).
    """
    if company.id is None:
        raise ValueError("Şirket henüz veritabanına kaydedilmemiş (id yok)")

    resolved_output_dir = output_dir if output_dir is not None else paths.output_dir()

    existing = _existing_application(session, company.id)
    if existing is not None:
        logger.info(f"{company.name} için başvuru zaten var, yenisi açılmadı")
        return existing

    cv_mode = get_setting(session, KEY_CV_MODE)

    # Sağlayıcı yalnızca tanıtım metni varsa kurulur: LLM'in tek işi o metne bağlı. Etiketleme
    # `cv_mode`dan BAĞIMSIZ çalışır (Adım 10.4a'daki `classify_company` kararıyla aynı ilke):
    # `own_pdf` modunda CV sıralamasında kullanılmaz ama ileride mail giriş cümlesi için
    # kullanılabilir (PLAN.md Adım 10.3a notu).
    provider = get_provider(session) if company.about_text else None
    if provider is not None:
        _fill_sector_tags(session, company, provider)

    if cv_mode == CV_MODE_OWN_PDF:
        # "Kendi PDF'imi olduğu gibi ekle": CV özelleştirme ve PDF üretimi (Playwright) hiç
        # çalışmaz (kural 2 ile uyumlu) — kullanıcının yüklediği dosya aynen eklenir.
        own_cv = own_cv_path_if_present()
        if own_cv is None:
            raise ValueError(
                "CV modu 'Kendi PDF'imi ekle' olarak ayarlı ama henüz bir PDF yüklenmedi "
                "(Ayarlar sekmesi > CV bölümünden yükleyin)."
            )
        pdf_path = own_cv
        matched_tags: list[str] = []
    else:
        tailoring_enabled = get_setting(session, KEY_TAILORING_ENABLED)
        max_projects = get_setting(session, KEY_MAX_PROJECTS)

        tailored_cv = tailor(
            profile,
            company.sector_tags,
            enabled=tailoring_enabled,
            max_projects=max_projects,
        )
        tailored_cv = _validated_cv(profile, tailored_cv, max_projects)

        pdf_path = render_pdf(
            profile,
            tailored_cv,
            resolved_output_dir / company_folder_name(company.domain) / cv_attachment_name(profile),
        )
        matched_tags = list(tailored_cv.matched_tags)

    subject, body = render_current_mail_text(session, company, profile)

    application = Application(
        company_id=company.id,
        status=STATUS_QUEUED,
        # matched_tags boş olması ana CV (ya da kendi PDF'i) kullanıldığı anlamına gelir.
        tailored=bool(matched_tags),
        matched_tags=matched_tags,
        cv_pdf_path=str(pdf_path),
        mail_subject=subject,
        mail_body=body,
    )
    session.add(application)
    session.flush()
    logger.info(f"Başvuru hazırlandı: {company.name} ({pdf_path})")
    return application
