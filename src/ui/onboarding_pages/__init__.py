"""İlk açılış sihirbazının sayfaları (PLAN.md Adım 10.2). Yeni sayfa: dosyayı ekle, buraya yaz."""
from pathlib import Path
from typing import Callable

from sqlalchemy.engine import Engine

from src.ui.onboarding_data import (
    PAGE_CV,
    PAGE_FINISH,
    PAGE_MAIL,
    PAGE_PERSONAL,
    PAGE_SELF_TEST,
    PAGE_SOURCES,
    PAGE_TARGET,
    PAGE_TEMPLATE,
    PAGE_WELCOME,
)
from src.ui.onboarding_pages.base import OnboardingPage
from src.ui.onboarding_pages.cv import CvPage
from src.ui.onboarding_pages.finish import FinishPage
from src.ui.onboarding_pages.mail import MailPage
from src.ui.onboarding_pages.personal import PersonalPage
from src.ui.onboarding_pages.self_test import SelfTestPage
from src.ui.onboarding_pages.sources import SourcesPage
from src.ui.onboarding_pages.template import TemplatePage
from src.ui.onboarding_pages.target import TargetPage
from src.ui.onboarding_pages.welcome import WelcomePage

#: `(engine, profile_path) -> sayfa`; anahtar `onboarding_data.PAGE_*`. Sihirbaz yalnızca burada
#: kayıtlı sayfaları, `PAGE_ORDER` sırasıyla gösterir (henüz yazılmayanlar atlanır).
PageFactory = Callable[[Engine, Path], OnboardingPage]

PAGE_FACTORIES: dict[str, PageFactory] = {
    PAGE_WELCOME: WelcomePage,
    PAGE_PERSONAL: PersonalPage,
    PAGE_TARGET: TargetPage,
    PAGE_CV: CvPage,
    PAGE_MAIL: MailPage,
    PAGE_SOURCES: SourcesPage,
    PAGE_TEMPLATE: TemplatePage,
    PAGE_SELF_TEST: SelfTestPage,
    PAGE_FINISH: FinishPage,
}
