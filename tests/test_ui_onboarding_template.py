"""src/ui/onboarding_pages/template.py testleri (PLAN.md Adım 10.2k; qtbot, GERÇEK meslek/şablon paketleriyle).

Sihirbazın içinde, yalnızca şablon sayfası kayıtlıyken (tek sayfa = son sayfa, "Bitir").
"""
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox, QWizard

from src.core.mail_template_presets import load_generic_mail_templates
from src.core.professions import load_profession_presets
from src.core.settings import (
    DEFAULT_MAIL_TEMPLATE_BODY,
    DEFAULT_MAIL_TEMPLATE_SUBJECT,
    KEY_APPLICATION_TARGET,
    KEY_MAIL_LANGUAGE,
    KEY_MAIL_TEMPLATE_BODY,
    KEY_MAIL_TEMPLATE_SUBJECT,
    KEY_PROFESSION,
    get_setting,
    set_setting,
)
from src.cv_builder.profile_editor import write_profile
from src.ui import onboarding_data as od
from src.ui.onboarding_pages import PAGE_FACTORIES
from src.ui.onboarding_pages.template import TemplatePage
from src.ui.onboarding_wizard import OnboardingWizard
from tests.conftest import build_sample_profile


@pytest.fixture
def warnings(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text) or 0
    )
    return shown


@pytest.fixture
def infos(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(text) or 0
    )
    return shown


@pytest.fixture
def asked(monkeypatch):
    """`QMessageBox.question` çağrıları: `asked` metinleri, `asked.answer` cevabı tutar."""

    class Asked(list):
        answer = QMessageBox.Yes

    shown = Asked()

    def _question(parent, title, text, *args, **kwargs):
        shown.append(text)
        return shown.answer

    monkeypatch.setattr(QMessageBox, "question", _question)
    return shown


def _choose(session, target="internship", profession="hukuk", language="tr"):
    set_setting(session, KEY_APPLICATION_TARGET, target)
    set_setting(session, KEY_PROFESSION, profession)
    set_setting(session, KEY_MAIL_LANGUAGE, language)
    session.commit()


def _suggestion(target="internship", profession="hukuk"):
    return load_profession_presets()[profession].default_mail_templates[target]


def _make_wizard(qtbot, engine, tmp_path) -> OnboardingWizard:
    wizard = OnboardingWizard(
        engine, tmp_path / "master_profile.json", page_factories={od.PAGE_TEMPLATE: TemplatePage}
    )
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


def _page(wizard) -> TemplatePage:
    return wizard.page(od.PAGE_ORDER.index(od.PAGE_TEMPLATE))


def _finish(wizard) -> None:
    wizard.button(QWizard.FinishButton).click()


def test_template_page_is_registered():
    assert PAGE_FACTORIES[od.PAGE_TEMPLATE] is TemplatePage


def test_untouched_default_gets_a_suggestion_and_yes_fills_it(qtbot, engine, tmp_path, session, asked):
    _choose(session)

    wizard = _make_wizard(qtbot, engine, tmp_path)
    group = _page(wizard).template_group

    suggestion = _suggestion()
    assert len(asked) == 1
    assert suggestion.subject in asked[0] and suggestion.body in asked[0]
    assert group.mail_subject_edit.text() == suggestion.subject
    assert group.mail_body_edit.toPlainText() == suggestion.body


def test_answering_no_keeps_the_default_text(qtbot, engine, tmp_path, session, asked):
    _choose(session)
    asked.answer = QMessageBox.No

    wizard = _make_wizard(qtbot, engine, tmp_path)
    group = _page(wizard).template_group

    assert len(asked) == 1
    assert group.mail_subject_edit.text() == DEFAULT_MAIL_TEMPLATE_SUBJECT
    assert group.mail_body_edit.toPlainText() == DEFAULT_MAIL_TEMPLATE_BODY


def test_suggestion_is_offered_only_once_per_page(qtbot, engine, tmp_path, session, asked):
    _choose(session)
    asked.answer = QMessageBox.No
    wizard = _make_wizard(qtbot, engine, tmp_path)

    _page(wizard).load()  # İleri/Geri ile sayfaya yeniden girmek

    assert len(asked) == 1


def test_users_own_text_is_never_overwritten_or_asked_about(qtbot, engine, tmp_path, session, asked):
    _choose(session)
    set_setting(session, KEY_MAIL_TEMPLATE_SUBJECT, "Benim konum")
    set_setting(session, KEY_MAIL_TEMPLATE_BODY, "Benim metnim {sirket_adi}")
    session.commit()

    wizard = _make_wizard(qtbot, engine, tmp_path)
    group = _page(wizard).template_group

    assert asked == []
    assert group.mail_subject_edit.text() == "Benim konum"
    assert group.mail_body_edit.toPlainText() == "Benim metnim {sirket_adi}"


def test_no_target_means_no_suggestion(qtbot, engine, tmp_path, asked):
    wizard = _make_wizard(qtbot, engine, tmp_path)

    assert asked == []
    assert _page(wizard).template_group.mail_subject_edit.text() == DEFAULT_MAIL_TEMPLATE_SUBJECT


def test_english_uses_the_generic_template(qtbot, engine, tmp_path, session, asked):
    _choose(session, language="en")

    wizard = _make_wizard(qtbot, engine, tmp_path)
    group = _page(wizard).template_group

    generic = load_generic_mail_templates()[("internship", "en")]
    assert group.mail_subject_edit.text() == generic.subject


def test_finish_saves_template_and_context(qtbot, engine, tmp_path, session, asked):
    _choose(session)
    wizard = _make_wizard(qtbot, engine, tmp_path)
    group = _page(wizard).template_group
    group.mail_subject_edit.setText("Staj başvurusu – {sirket_adi}")

    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    session.expire_all()
    assert get_setting(session, KEY_MAIL_TEMPLATE_SUBJECT) == "Staj başvurusu – {sirket_adi}"
    assert get_setting(session, KEY_MAIL_TEMPLATE_BODY) == _suggestion().body
    assert get_setting(session, KEY_APPLICATION_TARGET) == "internship"
    assert get_setting(session, KEY_PROFESSION) == "hukuk"


def test_empty_subject_is_blocked(qtbot, engine, tmp_path, session, asked, warnings):
    _choose(session)
    wizard = _make_wizard(qtbot, engine, tmp_path)
    _page(wizard).template_group.mail_subject_edit.setText("   ")

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert warnings == ["Mail konusu şablonu boş olamaz."]
    session.expire_all()
    assert get_setting(session, KEY_MAIL_TEMPLATE_SUBJECT) == DEFAULT_MAIL_TEMPLATE_SUBJECT


def test_unknown_placeholder_is_blocked(qtbot, engine, tmp_path, asked, warnings):
    wizard = _make_wizard(qtbot, engine, tmp_path)
    _page(wizard).template_group.mail_body_edit.setPlainText("Merhaba {yok_boyle_bir_sey}")

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert "yok_boyle_bir_sey" in warnings[0]


def test_preview_fills_placeholders_from_the_profile(qtbot, engine, tmp_path, asked, infos):
    write_profile(tmp_path / "master_profile.json", build_sample_profile())
    wizard = _make_wizard(qtbot, engine, tmp_path)
    group = _page(wizard).template_group
    group.mail_subject_edit.setText("{ad_soyad} – {sirket_adi}")

    group.preview_button.click()

    assert len(infos) == 1
    assert "Test Kişi" in infos[0] and "{ad_soyad}" not in infos[0]


def test_preview_without_a_profile_warns_instead_of_crashing(qtbot, engine, tmp_path, asked, warnings):
    wizard = _make_wizard(qtbot, engine, tmp_path)

    _page(wizard).template_group.preview_button.click()

    assert len(warnings) == 1 and "Profil okunamadı" in warnings[0]


@pytest.mark.parametrize(
    "subject,body,fragment",
    [("", "x", "konusu"), ("x", " ", "gövdesi"), ("x", "{yok}", "bilinmeyen yer tutucu")],
)
def test_validate_template_errors(subject, body, fragment):
    errors = od.validate_template(subject, body)
    assert len(errors) == 1 and fragment in errors[0]


def test_validate_template_accepts_known_placeholders():
    assert od.validate_template("{sirket_adi} – Başvuru", "Merhaba {ad_soyad}") == []


def test_untouched_default_detection():
    assert od.is_untouched_default_template(DEFAULT_MAIL_TEMPLATE_SUBJECT, DEFAULT_MAIL_TEMPLATE_BODY)
    assert not od.is_untouched_default_template(DEFAULT_MAIL_TEMPLATE_SUBJECT, "başka")
    assert not od.is_untouched_default_template("başka", DEFAULT_MAIL_TEMPLATE_BODY)
