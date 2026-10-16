"""Sihirbazın kayıtlı TÜM sayfaları birlikte (PLAN.md 10.2i–k + "Diğer" sonrası kurulum bütünlüğü).

Tek tek sayfa testleri kendi sayfalarıyla çalışır; burası kayıt defterinin tamamının taze bir kurulumda
sırayla kurulup gösterilebildiğini ve her sayfanın "İleri"de doğrulama yapıp boş kurulumu geçirmediğini sınar.
"""
import pytest
from PySide6.QtWidgets import QMessageBox

from src.core import secrets
from src.ui import onboarding_data as od
from src.ui.onboarding_pages import PAGE_FACTORIES
from src.ui.onboarding_wizard import OnboardingWizard


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(secrets, "keyring", _FakeKeyring())
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)


def test_registry_pages_are_in_page_order_and_all_build(qtbot, engine, tmp_path):
    wizard = OnboardingWizard(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(wizard)

    assert wizard.page_ids == [page_id for page_id in od.PAGE_ORDER if page_id in PAGE_FACTORIES]
    assert {od.PAGE_WELCOME, od.PAGE_PERSONAL, od.PAGE_TARGET, od.PAGE_CV, od.PAGE_MAIL,
            od.PAGE_SOURCES, od.PAGE_TEMPLATE, od.PAGE_SELF_TEST, od.PAGE_FINISH} <= set(wizard.page_ids)


def test_every_page_loads_on_a_fresh_install_and_blocks_an_empty_form(qtbot, engine, tmp_path):
    wizard = OnboardingWizard(engine, tmp_path / "master_profile.json")
    qtbot.addWidget(wizard)

    for page_id in wizard.page_ids:
        page = wizard.page(od.PAGE_ORDER.index(page_id))
        page.load()  # taze veritabanı/profil yok: çökmemeli
        if page_id in (od.PAGE_WELCOME, od.PAGE_PERSONAL, od.PAGE_TARGET, od.PAGE_MAIL, od.PAGE_SOURCES,
                       od.PAGE_SELF_TEST):
            assert page.validation_errors(), f"{page_id} boş kurulumu geçirmemeli"
