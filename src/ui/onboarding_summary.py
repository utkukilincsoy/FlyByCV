"""Sihirbazın bitiş sayfasının özeti (PySide6 İÇERMEZ) -- PLAN.md Adım 10.2m.

Veritabanındaki/profildeki KAYITLI durumu okur (sihirbazın önceki sayfaları "İleri"de yazdı), hiçbir şey
YAZMAZ -- özellikle test modu ayarına dokunmaz: taze kurulumda test modu varsayılan AÇIKTIR ve sihirbaz onu
kapatmaz; bitiş yalnızca bunu gösterir ve nasıl kapatılacağını söyler. Sır içermez (şifre/anahtar yalnızca
"kayıtlı mı" olarak, değerleri hiç okunmaz).
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlmodel import Session

from src.company_finder.source_selector import load_source_settings, select_sources
from src.core.cities import load_cities
from src.core.custom_profession import resolve_profession
from src.core.mail_providers import load_mail_provider_presets
from src.core.secrets import get_mail_password
from src.cv_builder.own_cv import own_cv_status_text
from src.sender.mail_connection import PROVIDER_CUSTOM
from src.ui.onboarding_data import load_existing_profile
from src.ui.settings_data import (
    APPLICATION_TARGET_LABELS_TR,
    CV_MODE_LABELS_TR,
    SOURCE_LABELS_TR,
    read_form,
)

logger = logging.getLogger(__name__)

NOT_SET = "(seçilmedi)"
PROFILE_UNREADABLE = "(profil dosyası okunamadı)"
CUSTOM_PROVIDER_NAME = "Özel sunucu"
NO_SOURCES_TEXT = "otomatik kaynak yok (yalnızca CSV ile içe aktarma)"

TEST_MODE_ON_TEXT = (
    "AÇIK — Otopilot ve gönderimler firmalara DEĞİL, kendi adresinize mail gönderir. Hiçbir firmaya gerçek "
    "başvuru gitmez."
)
TEST_MODE_OFF_TEXT = (
    "KAPALI — GERÇEK GÖNDERİM AÇIK: mailler firmalara gider! Geri dönmek için \"Test moduna dön\" düğmesine basın."
)
HOW_TO_GO_LIVE_TEXT = (
    "Gerçek gönderime hazır olduğunuzda aşağıdaki \"Gerçek gönderime geç\" düğmesine (ya da Ayarlar "
    "sekmesindeki \"Gönderim güvenliği\" bölümüne) basabilirsiniz; açık bir onay ister. Önce Otopilot'u test "
    "modunda deneyip Onay Kuyruğu'nu kontrol etmeniz önerilir."
)


@dataclass(frozen=True)
class SummaryLine:
    label: str
    value: str


@dataclass(frozen=True)
class FinishSummary:
    lines: tuple[SummaryLine, ...]
    test_mode: bool

    @property
    def test_mode_text(self) -> str:
        return TEST_MODE_ON_TEXT if self.test_mode else TEST_MODE_OFF_TEXT


def _profile_lines(profile_path: Path) -> list[SummaryLine]:
    try:
        profile = load_existing_profile(profile_path)
    except ValueError:
        logger.exception("Bitiş özeti profil dosyasını okuyamadı")
        return [SummaryLine("Kişisel bilgiler", PROFILE_UNREADABLE)]
    if profile is None:
        return [SummaryLine("Kişisel bilgiler", NOT_SET)]
    info = profile.personal_info
    return [SummaryLine("Ad soyad", info.name), SummaryLine("E-posta", info.email)]


def _cv_value(cv_mode: str) -> str:
    label = CV_MODE_LABELS_TR.get(cv_mode, cv_mode)
    if cv_mode == "own_pdf":
        return f"{label} ({own_cv_status_text()})"
    return label


def build_finish_summary(session: Session, profile_path: Path) -> FinishSummary:
    """Kurulum sonunda gösterilecek özet satırları + test modu durumu."""
    form = read_form(session)
    cities = load_cities()
    presets = load_mail_provider_presets()
    profession = resolve_profession(session)

    city = cities.get(form.autopilot_city) if form.autopilot_city else None
    city_text = city.name_tr if city else (form.autopilot_city or NOT_SET)

    if form.mail_provider == PROVIDER_CUSTOM:
        provider_name = CUSTOM_PROVIDER_NAME
    else:
        preset = presets.get(form.mail_provider)
        provider_name = preset.name_tr if preset else form.mail_provider
    sender = form.sender_email or NOT_SET
    password_text = "uygulama şifresi kayıtlı" if get_mail_password() else "uygulama şifresi kayıtlı DEĞİL"

    sources = select_sources(profession, form.autopilot_city, load_source_settings(session), cities)
    sources_text = ", ".join(SOURCE_LABELS_TR[source] for source in sources) or NO_SOURCES_TEXT

    target_text: Optional[str] = (
        APPLICATION_TARGET_LABELS_TR.get(form.application_target) if form.application_target else None
    )

    lines = [
        *_profile_lines(profile_path),
        SummaryLine("Aranan", target_text or NOT_SET),
        SummaryLine("Meslek", profession.name_tr if profession else NOT_SET),
        SummaryLine("Şehir", city_text),
        SummaryLine("CV", _cv_value(form.cv_mode)),
        SummaryLine("Mail hesabı", f"{sender} ({provider_name}; {password_text})"),
        SummaryLine("Firma kaynakları", sources_text),
        SummaryLine("Mail konusu", form.mail_template_subject),
    ]
    return FinishSummary(lines=tuple(lines), test_mode=bool(form.test_mode))
