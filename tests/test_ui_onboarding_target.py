"""src/ui/onboarding_pages/target.py testleri (PLAN.md Adım 10.2g; qtbot, GERÇEK meslek paketleriyle).

Sihirbazın içinde, yalnızca hedef/meslek sayfası kayıtlıyken (tek sayfa = son sayfa, "Bitir").
"""
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QWizard

from src.core.custom_profession import CUSTOM_PROFESSION_ID
from src.core.professions import load_profession_presets
from src.core.settings import (
    KEY_APPLICATION_TARGET,
    KEY_CUSTOM_PROFESSION_KEYWORDS,
    KEY_CUSTOM_PROFESSION_NAME,
    KEY_MAIL_TEMPLATE_BODY,
    KEY_MAIL_TEMPLATE_SUBJECT,
    KEY_PROFESSION,
    get_setting,
    set_setting,
)
from src.cv_builder.mail_template import TARGETS
from src.ui import onboarding_data as od
from src.ui.onboarding_pages import PAGE_FACTORIES
from src.ui.onboarding_pages.target import TargetPage
from src.ui.onboarding_wizard import OnboardingWizard
from src.ui.settings_data import APPLICATION_TARGET_LABELS_TR


@pytest.fixture
def warnings(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text) or 0
    )
    return shown


def _make_wizard(qtbot, engine, tmp_path) -> OnboardingWizard:
    wizard = OnboardingWizard(
        engine, tmp_path / "master_profile.json", page_factories={od.PAGE_TARGET: TargetPage}
    )
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


def _page(wizard) -> TargetPage:
    return wizard.page(od.PAGE_ORDER.index(od.PAGE_TARGET))


def _finish(wizard) -> None:
    wizard.button(QWizard.FinishButton).click()


def test_target_page_is_registered():
    assert PAGE_FACTORIES[od.PAGE_TARGET] is TargetPage


def test_lists_come_from_the_same_sources_as_settings(qtbot, engine, tmp_path):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)

    targets = [page.target_combo.itemData(i) for i in range(page.target_combo.count())]
    assert targets == [None, *TARGETS]
    assert page.target_combo.itemText(1) == APPLICATION_TARGET_LABELS_TR[TARGETS[0]]
    professions = load_profession_presets()
    assert page.profession_combo.count() == len(professions) + 2  # "(seçilmedi)" + paketler + "Diğer"
    assert {page.profession_combo.itemData(i) for i in range(1, page.profession_combo.count())} == set(
        professions
    ) | {CUSTOM_PROFESSION_ID}
    assert page.profession_combo.itemData(page.profession_combo.count() - 1) == CUSTOM_PROFESSION_ID


def test_nothing_chosen_is_blocked_and_nothing_is_written(
    qtbot, engine, tmp_path, session, warnings
):
    wizard = _make_wizard(qtbot, engine, tmp_path)

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert len(warnings) == 1
    assert "Ne aradığınızı" in warnings[0] and "meslek seçin" in warnings[0]
    session.expire_all()
    assert get_setting(session, KEY_APPLICATION_TARGET) is None
    assert get_setting(session, KEY_PROFESSION) is None


def test_target_without_profession_is_blocked(qtbot, engine, tmp_path, warnings):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    page.target_combo.setCurrentIndex(page.target_combo.findData("internship"))

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert "Ne aradığınızı" not in warnings[0] and "meslek seçin" in warnings[0]


def test_finish_saves_target_and_profession(qtbot, engine, tmp_path, session):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    page.target_combo.setCurrentIndex(page.target_combo.findData("internship"))
    page.profession_combo.setCurrentIndex(page.profession_combo.findData("yazilim"))

    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    session.expire_all()
    assert get_setting(session, KEY_APPLICATION_TARGET) == "internship"
    assert get_setting(session, KEY_PROFESSION) == "yazilim"


def test_saved_choices_are_prefilled(qtbot, engine, tmp_path, session):
    set_setting(session, KEY_APPLICATION_TARGET, "part_time")
    set_setting(session, KEY_PROFESSION, "hukuk")
    session.commit()

    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)

    assert page.target_combo.currentData() == "part_time"
    assert page.profession_combo.currentData() == "hukuk"


def test_company_types_hint_follows_the_chosen_profession(qtbot, engine, tmp_path):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    assert page.company_types_label.text() == ""

    page.profession_combo.setCurrentIndex(page.profession_combo.findData("hukuk"))

    preset = load_profession_presets()["hukuk"]
    assert page.company_types_label.text() == f"Aranacak firma türleri: {', '.join(preset.company_types)}."


def test_page_does_not_touch_the_mail_template(qtbot, engine, tmp_path, session):
    set_setting(session, KEY_MAIL_TEMPLATE_SUBJECT, "Benim konum")
    set_setting(session, KEY_MAIL_TEMPLATE_BODY, "Benim metnim")
    session.commit()
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    page.target_combo.setCurrentIndex(page.target_combo.findData("job"))
    page.profession_combo.setCurrentIndex(page.profession_combo.findData("hukuk"))

    _finish(wizard)  # onay penceresi çıkmaz (çıksaydı test takılırdı)

    session.expire_all()
    assert get_setting(session, KEY_MAIL_TEMPLATE_SUBJECT) == "Benim konum"
    assert get_setting(session, KEY_MAIL_TEMPLATE_BODY) == "Benim metnim"


def test_unreadable_profession_list_blocks_the_page(qtbot, engine, tmp_path, monkeypatch, warnings):
    monkeypatch.setattr("src.ui.onboarding_pages.target.load_profession_presets", lambda: {})
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    page.target_combo.setCurrentIndex(page.target_combo.findData("job"))

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert page.error_label.isHidden() is False
    assert "Meslek listesi okunamadı" in warnings[0]


# -- "Diğer" meslek (PLAN.md 10.2g sapmasının kapatılması) -----------------------------------------


def _choose_custom(page, name="Diş hekimliği", keywords="diş kliniği, ortodonti"):
    page.target_combo.setCurrentIndex(page.target_combo.findData("internship"))
    page.profession_combo.setCurrentIndex(page.profession_combo.findData(CUSTOM_PROFESSION_ID))
    page.custom_fields.name_edit.setText(name)
    page.custom_fields.keywords_edit.setText(keywords)


def test_custom_fields_are_visible_only_for_other(qtbot, engine, tmp_path):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    assert page.custom_fields.isHidden() is True

    page.profession_combo.setCurrentIndex(page.profession_combo.findData(CUSTOM_PROFESSION_ID))
    assert page.custom_fields.isHidden() is False
    assert page.company_types_label.text() == ""  # paket yok → firma türü ipucu yok

    page.profession_combo.setCurrentIndex(page.profession_combo.findData("hukuk"))
    assert page.custom_fields.isHidden() is True


def test_other_profession_saves_name_and_keywords(qtbot, engine, tmp_path, session):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    _choose_custom(page, keywords=" diş kliniği ,ortodonti, , Ortodonti ,implant")

    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    session.expire_all()
    assert get_setting(session, KEY_PROFESSION) == CUSTOM_PROFESSION_ID
    assert get_setting(session, KEY_CUSTOM_PROFESSION_NAME) == "Diş hekimliği"
    assert get_setting(session, KEY_CUSTOM_PROFESSION_KEYWORDS) == ["diş kliniği", "ortodonti", "implant"]


def test_other_without_name_or_keywords_is_blocked(qtbot, engine, tmp_path, session, warnings):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    _choose_custom(page, name="  ", keywords="")

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert "Meslek adını yazın" in warnings[0] and "arama kelimesi" in warnings[0]
    session.expire_all()
    assert get_setting(session, KEY_PROFESSION) is None


def test_other_with_too_many_keywords_is_blocked(qtbot, engine, tmp_path, warnings):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    _choose_custom(page, keywords=", ".join(f"kelime{i}" for i in range(11)))

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert "En çok 10" in warnings[0]


def test_saved_other_profession_is_prefilled(qtbot, engine, tmp_path, session):
    set_setting(session, KEY_APPLICATION_TARGET, "job")
    set_setting(session, KEY_PROFESSION, CUSTOM_PROFESSION_ID)
    set_setting(session, KEY_CUSTOM_PROFESSION_NAME, "Veterinerlik")
    set_setting(session, KEY_CUSTOM_PROFESSION_KEYWORDS, ["veteriner kliniği", "pet shop"])
    session.commit()

    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)

    assert page.profession_combo.currentData() == CUSTOM_PROFESSION_ID
    assert page.custom_fields.isHidden() is False
    assert page.custom_fields.name_edit.text() == "Veterinerlik"
    assert page.custom_fields.keywords_edit.text() == "veteriner kliniği, pet shop"


def test_choosing_a_listed_profession_keeps_stored_custom_values(qtbot, engine, tmp_path, session):
    set_setting(session, KEY_CUSTOM_PROFESSION_NAME, "Veterinerlik")
    set_setting(session, KEY_CUSTOM_PROFESSION_KEYWORDS, ["veteriner kliniği"])
    session.commit()
    wizard = _make_wizard(qtbot, engine, tmp_path)
    page = _page(wizard)
    page.target_combo.setCurrentIndex(page.target_combo.findData("job"))
    page.profession_combo.setCurrentIndex(page.profession_combo.findData("hukuk"))

    _finish(wizard)

    session.expire_all()
    assert get_setting(session, KEY_PROFESSION) == "hukuk"
    assert get_setting(session, KEY_CUSTOM_PROFESSION_NAME) == "Veterinerlik"  # sonradan "Diğer"e dönülürse kaybolmaz
