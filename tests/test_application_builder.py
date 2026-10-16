from pathlib import Path
from typing import Optional

import pytest
from sqlmodel import select

from src.core.db import get_session
from src.core.models import Application, Company
from src.core.settings import (
    CV_MODE_OWN_PDF,
    KEY_CV_MODE,
    KEY_MAX_PROJECTS,
    KEY_TAILORING_ENABLED,
    set_setting,
)
from src.cv_builder.application_builder import build_application
from src.cv_builder.renderer import cv_attachment_name
from src.cv_builder.tag_matcher import TailoredCV
from src.cv_builder.validator import ValidationError
from src.llm.base import REPLY_PENDING, LLMProvider
from src.llm.factory import SafeProvider
from tests.conftest import build_company

MATCHING_TAGS = ["yapay-zeka", "veri-bilimi"]

ABOUT_TEXT = (
    "Yapay zeka tabanlı görüntü işleme çözümleri üretiyoruz. Kurumsal ağ altyapısı "
    "kurulumu ve bakımı da hizmetlerimiz arasındadır."
)


@pytest.fixture
def rendered(monkeypatch) -> list[TailoredCV]:
    """render_pdf'i sahte bir üreticiyle değiştirir; Playwright hiç çalışmaz.

    Dönen liste her çağrıda kullanılan sıralamayı tutar, böylece hangi CV'nin
    üretildiği doğrulanabilir.
    """
    calls: list[TailoredCV] = []

    def fake_render_pdf(profile, tailored, out_path):
        calls.append(tailored)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF-1.4 test")
        return out_path

    monkeypatch.setattr(
        "src.cv_builder.application_builder.render_pdf", fake_render_pdf
    )
    return calls


@pytest.fixture
def company(session):
    company = build_company(
        name="Örnek Ağ A.Ş.", domain="ornek-ag.com", sector_tags=list(MATCHING_TAGS)
    )
    session.add(company)
    session.flush()
    return company


class _FakeProvider(LLMProvider):
    """Ne döndüreceği testte belirlenen, çağrı sayan sahte sağlayıcı.

    `SafeProvider` ile sarılarak kurulur: üretimde de öyle çalışıyor, böylece düşme ve
    doğrulama adımları testte de gerçekten devrede olur.
    """

    name = "fake"

    def __init__(
        self,
        tags: Optional[list[str]] = None,
        phrase: Optional[str] = None,
        error: Optional[Exception] = None,
    ) -> None:
        self.tags = tags or []
        self.phrase = phrase
        self.error = error
        self.tag_calls = 0
        self.phrase_calls = 0

    def classify_company(self, name: str, about_text: str) -> list[str]:
        self.tag_calls += 1
        if self.error is not None:
            raise self.error
        return list(self.tags)

    def company_focus_phrase(self, name: str, about_text: str) -> Optional[str]:
        self.phrase_calls += 1
        if self.error is not None:
            raise self.error
        return self.phrase

    def classify_reply(self, subject: str, body: str) -> str:
        return REPLY_PENDING


@pytest.fixture
def fake_llm(monkeypatch):
    """Verilen sahte sağlayıcıyı `build_application`'ın kullandığı sağlayıcı yapar."""

    def install(provider: _FakeProvider) -> _FakeProvider:
        monkeypatch.setattr(
            "src.cv_builder.application_builder.get_provider",
            lambda session: SafeProvider(provider),
        )
        return provider

    return install


def _company_with_about(session, **overrides) -> Company:
    defaults = dict(
        name="Örnek A.Ş.", domain="ornek.com", about_text=ABOUT_TEXT, sector_tags=[]
    )
    defaults.update(overrides)
    company = build_company(**defaults)
    session.add(company)
    session.flush()
    return company


def test_creates_row_with_all_fields(session, company, sample_profile, tmp_path, rendered):
    """Application'ın bütün alanları dolmalı."""
    application = build_application(session, company, sample_profile, output_dir=tmp_path)

    assert application.id is not None
    assert application.company_id == company.id
    assert application.status == "queued"
    assert application.tailored is True
    assert application.matched_tags == MATCHING_TAGS
    assert application.mail_subject == "Örnek Ağ A.Ş. – Başvuru"
    assert "Test Kişi" in application.mail_body
    assert "Örnek Ağ A.Ş." in application.mail_body
    assert Path(application.cv_pdf_path).exists()


def test_returns_existing_row_for_same_company(
    session, company, sample_profile, tmp_path, rendered
):
    """Aynı şirkete ikinci çağrı yeni kayıt açmamalı (kural 4)."""
    first = build_application(session, company, sample_profile, output_dir=tmp_path)
    second = build_application(session, company, sample_profile, output_dir=tmp_path)

    assert second.id == first.id
    assert len(session.exec(select(Application)).all()) == 1
    assert len(rendered) == 1  # ikinci çağrıda PDF yeniden üretilmedi


def test_untailored_when_tailoring_disabled(
    session, company, sample_profile, tmp_path, rendered
):
    """Özelleştirme kapalıysa ana CV gitmeli (kural 2)."""
    set_setting(session, KEY_TAILORING_ENABLED, False)

    application = build_application(session, company, sample_profile, output_dir=tmp_path)

    assert application.tailored is False
    assert application.matched_tags == []


def test_untailored_when_no_tag_matches(session, sample_profile, tmp_path, rendered):
    """Etiketler profille eşleşmiyorsa tailored False olmalı."""
    company = build_company(domain="baska.com", sector_tags=["oyun-gelistirme"])
    session.add(company)
    session.flush()

    application = build_application(session, company, sample_profile, output_dir=tmp_path)

    assert application.tailored is False
    assert application.matched_tags == []


def test_respects_max_projects(session, company, sample_profile, tmp_path, rendered):
    set_setting(session, KEY_MAX_PROJECTS, 2)

    build_application(session, company, sample_profile, output_dir=tmp_path)

    assert len(rendered[0].project_ids) == 2


def test_falls_back_to_main_cv_when_validation_fails(
    session, company, sample_profile, tmp_path, rendered, monkeypatch
):
    """Doğrulama hatasında ana CV'ye düşülmeli (kural 1)."""

    def fake_validate(tailored, profile, max_projects=None):
        if tailored.matched_tags:
            raise ValidationError("test: sıralama bozuk")

    monkeypatch.setattr("src.cv_builder.application_builder.validate", fake_validate)

    application = build_application(session, company, sample_profile, output_dir=tmp_path)

    assert application.tailored is False
    assert application.matched_tags == []
    assert rendered[-1].project_ids == [p.id for p in sample_profile.projects]


# --- PDF yolu ---


def test_attachment_name_has_no_domain(session, company, sample_profile, tmp_path, rendered):
    """Ek adında şirketin alan adı geçmemeli."""
    application = build_application(session, company, sample_profile, output_dir=tmp_path)

    pdf_path = Path(application.cv_pdf_path)
    assert pdf_path.name == cv_attachment_name(sample_profile)
    assert "ornek-ag" not in pdf_path.name


def test_pdf_goes_into_domain_folder(session, company, sample_profile, tmp_path, rendered):
    """Alan adı dosya adı yerine klasör adında olmalı."""
    application = build_application(session, company, sample_profile, output_dir=tmp_path)

    pdf_path = Path(application.cv_pdf_path)
    assert pdf_path.parent.name == "ornek-ag.com"
    assert pdf_path.parent.parent == tmp_path


# --- Oturum davranışı ---


def test_does_not_commit(engine, session, company, sample_profile, tmp_path, rendered):
    """Commit çağırana ait: rollback sonrası kayıt kalmamalı."""
    build_application(session, company, sample_profile, output_dir=tmp_path)
    session.rollback()

    with get_session(engine=engine) as other:
        assert other.exec(select(Application)).all() == []


def test_requires_saved_company(session, sample_profile, tmp_path, rendered):
    """Kaydedilmemiş şirket için anlaşılır bir hata verilmeli."""
    company = build_company(domain="kayitsiz.com")

    with pytest.raises(ValueError, match="kaydedilmemiş"):
        build_application(session, company, sample_profile, output_dir=tmp_path)


# --- LLM: etiketleme ---


def test_fills_empty_sector_tags_from_about_text(
    engine, session, sample_profile, tmp_path, rendered, fake_llm
):
    """Etiketi olmayan şirket metninden etiketlenmeli ve etiketler KAYDEDİLMELİ."""
    provider = fake_llm(_FakeProvider(tags=["yapay-zeka"]))
    company = _company_with_about(session)

    application = build_application(session, company, sample_profile, output_dir=tmp_path)
    session.commit()

    assert provider.tag_calls == 1
    assert company.sector_tags == ["yapay-zeka"]
    # Etiketler CV sıralamasını da beslemeli (tailor'dan önce doldukları için).
    assert application.matched_tags == ["yapay-zeka"]
    with get_session(engine=engine) as other:
        assert other.get(Company, company.id).sector_tags == ["yapay-zeka"]


def test_keeps_existing_sector_tags(session, sample_profile, tmp_path, rendered, fake_llm):
    """Etiketi dolu şirket için sınıflandırma hiç çağrılmamalı."""
    provider = fake_llm(_FakeProvider(tags=["oyun-gelistirme"]))
    company = _company_with_about(session, sector_tags=list(MATCHING_TAGS))

    application = build_application(session, company, sample_profile, output_dir=tmp_path)

    assert provider.tag_calls == 0
    assert company.sector_tags == MATCHING_TAGS
    assert application.matched_tags == MATCHING_TAGS


def test_no_llm_call_without_about_text(
    session, company, sample_profile, tmp_path, rendered, fake_llm
):
    """Tanıtım metni yoksa hiçbir LLM çağrısı yapılmamalı."""
    provider = fake_llm(_FakeProvider(tags=["oyun-gelistirme"], phrase="yapay zeka çözümleri"))

    build_application(session, company, sample_profile, output_dir=tmp_path)

    assert provider.tag_calls == 0
    assert provider.phrase_calls == 0


def test_application_is_built_when_provider_fails(
    session, sample_profile, tmp_path, rendered, fake_llm
):
    """Sağlayıcı her çağrıda hata verse de başvuru üretilmeli (kural 3)."""
    fake_llm(_FakeProvider(error=RuntimeError("kota aşıldı")))
    company = _company_with_about(session)

    application = build_application(session, company, sample_profile, output_dir=tmp_path)

    # Anahtar kelime sağlayıcısına düşüldü: etiketler metinden bulundu.
    assert application.matched_tags == ["yapay-zeka"]
    assert Path(application.cv_pdf_path).exists()


def test_mail_text_uses_current_template_setting(
    session, company, sample_profile, tmp_path, rendered
):
    """Mail konusu/gövdesi, Ayarlar'daki güncel şablondan üretilmeli (kural: kod sabit metin
    üretmez, PLAN.md Adım 10.4)."""
    from src.core.settings import KEY_MAIL_TEMPLATE_BODY, KEY_MAIL_TEMPLATE_SUBJECT

    set_setting(session, KEY_MAIL_TEMPLATE_SUBJECT, "Özel konu: {sirket_adi}")
    set_setting(session, KEY_MAIL_TEMPLATE_BODY, "Merhaba, ben {ad_soyad}.")

    application = build_application(session, company, sample_profile, output_dir=tmp_path)

    assert application.mail_subject == "Özel konu: Örnek Ağ A.Ş."
    assert application.mail_body == "Merhaba, ben Test Kişi."


# -- CV modu (Faz 10 Adım 10.3a) -----------------------------------------------------------


def test_own_pdf_mode_skips_tailoring_and_render(
    session, company, sample_profile, tmp_path, rendered, monkeypatch
):
    """`cv_mode=own_pdf` iken `tailor()`/`render_pdf()` hiç çağrılmamalı, kendi PDF aynen
    kullanılmalı (kural 2 ile uyumlu -- CV özelleştirme hiç çalışmaz)."""
    own_cv = tmp_path / "own.pdf"
    own_cv.write_bytes(b"%PDF-1.4 kendi cv")
    monkeypatch.setattr(
        "src.cv_builder.application_builder.own_cv_path_if_present", lambda: own_cv
    )
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)

    application = build_application(session, company, sample_profile, output_dir=tmp_path)

    assert rendered == []  # Playwright/render_pdf hiç çalışmadı
    assert application.tailored is False
    assert application.matched_tags == []
    assert application.cv_pdf_path == str(own_cv)


def test_own_pdf_mode_raises_without_uploaded_file(
    session, company, sample_profile, tmp_path, rendered, monkeypatch
):
    """Kendi PDF yüklenmemişse anlaşılır bir hata fırlatılmalı (build_job/runner bunu şirket
    başına yakalar)."""
    monkeypatch.setattr(
        "src.cv_builder.application_builder.own_cv_path_if_present", lambda: None
    )
    set_setting(session, KEY_CV_MODE, CV_MODE_OWN_PDF)

    with pytest.raises(ValueError):
        build_application(session, company, sample_profile, output_dir=tmp_path)

    assert rendered == []
