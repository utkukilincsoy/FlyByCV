"""src/ui/onboarding_pages/cv.py testleri (PLAN.md Adım 10.2h; qtbot).

Sihirbazın içinde, yalnızca CV sayfası kayıtlıyken (tek sayfa = son sayfa, "Bitir"). Kendi PDF'i her
testte `tmp_path` altına kopyalanır (`FLYBYCV_HOME`), gerçek `data/own_cv.pdf`e DOKUNULMAZ.
"""
import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox, QWizard

from src.core.models import Setting
from src.core.settings import (
    CV_MODE_ATS,
    CV_MODE_OWN_PDF,
    KEY_CV_MODE,
    KEY_MAX_PROJECTS,
    KEY_ONBOARDING_COMPLETED,
    KEY_TAILORING_ENABLED,
    get_setting,
    set_setting,
)
from src.cv_builder.own_cv import has_own_cv
from src.cv_builder.profile_editor import write_profile
from src.cv_builder.profile_loader import load_profile
from src.ui import onboarding_data as od
from src.ui.onboarding_pages import PAGE_FACTORIES
from src.ui.onboarding_pages.cv import CvPage
from src.ui.onboarding_wizard import OnboardingWizard
from tests.conftest import build_sample_profile


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path / "home"))


@pytest.fixture
def warnings(monkeypatch):
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(text) or 0
    )
    return shown


@pytest.fixture
def profile_path(tmp_path):
    return tmp_path / "master_profile.json"


@pytest.fixture
def pick_pdf(tmp_path, monkeypatch):
    """"PDF seç…" penceresi yerine geçen sahte seçim; seçilen dosyanın yolunu döner."""
    source = tmp_path / "benim_cv.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(source), "PDF"))
    return source


class _FakeExperienceDialog:
    Accepted = 1

    def __init__(self, initial=None, parent=None):
        pass

    def exec(self):
        return self.Accepted

    def values(self):
        return {
            "company": "Yeni Şirket",
            "title": "Stajyer",
            "department": None,
            "note": None,
            "topics": [],
            "start_date": "2024-01",
            "end_date": None,
            "bullets": ["Bir şeyler yaptım."],
            "tags": [],
        }


def _make_wizard(qtbot, engine, profile_path, *, reopen=False) -> OnboardingWizard:
    wizard = OnboardingWizard(
        engine, profile_path, reopen=reopen, page_factories={od.PAGE_CV: CvPage}
    )
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


def _page(wizard) -> CvPage:
    return wizard.page(od.PAGE_ORDER.index(od.PAGE_CV))


def _choose_mode(page, mode) -> None:
    combo = page.cv_group.cv_mode_combo
    combo.setCurrentIndex(combo.findData(mode))


def _finish(wizard) -> None:
    wizard.button(QWizard.FinishButton).click()


# --- açılış hâli --------------------------------------------------------------------------------


def test_cv_page_is_registered():
    assert PAGE_FACTORIES[od.PAGE_CV] is CvPage


def test_fresh_install_recommends_and_preselects_own_pdf_and_hides_the_editors(
    qtbot, engine, profile_path
):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    combo = page.cv_group.cv_mode_combo

    assert combo.currentData() == CV_MODE_OWN_PDF
    assert combo.itemText(combo.findData(CV_MODE_OWN_PDF)).endswith("(önerilen)")
    assert "önerilen" not in combo.itemText(combo.findData(CV_MODE_ATS))
    assert page.editors_box.isHidden() is True
    assert page.cv_group.own_cv_row_widget.isHidden() is False


def test_ats_mode_shows_the_profile_editors_with_the_existing_items(qtbot, engine, profile_path):
    write_profile(profile_path, build_sample_profile())
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)

    _choose_mode(page, CV_MODE_ATS)

    assert page.editors_box.isHidden() is False
    assert page.sections_panel.experience_section.list_widget.count() == 2
    assert page.sections_panel.projects_section.list_widget.count() == 4
    assert page.cv_group.tailoring_check.isHidden() is False  # CvGroup'un ATS ayarları da orada

    _choose_mode(page, CV_MODE_OWN_PDF)

    assert page.editors_box.isHidden() is True


# --- kendi PDF'i ---------------------------------------------------------------------------------


def test_own_pdf_without_a_file_is_blocked_and_nothing_is_saved(
    qtbot, engine, profile_path, session, warnings
):
    wizard = _make_wizard(qtbot, engine, profile_path)

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert "PDF" in warnings[0]
    session.expire_all()
    assert session.get(Setting, KEY_CV_MODE) is None


def test_own_pdf_with_a_file_is_saved(qtbot, engine, profile_path, session, pick_pdf):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)

    page.cv_group.on_select_own_cv()
    assert has_own_cv() is True
    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    session.expire_all()
    assert get_setting(session, KEY_CV_MODE) == CV_MODE_OWN_PDF


def test_own_pdf_mode_does_not_need_or_touch_a_profile(qtbot, engine, profile_path, pick_pdf):
    wizard = _make_wizard(qtbot, engine, profile_path)
    _page(wizard).cv_group.on_select_own_cv()

    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    assert not profile_path.exists()


# --- ATS CV + Profil düzenleyicileri ----------------------------------------------------------------


def test_ats_mode_saves_settings_without_requiring_a_pdf(qtbot, engine, profile_path, session):
    write_profile(profile_path, build_sample_profile())
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    _choose_mode(page, CV_MODE_ATS)
    page.cv_group.tailoring_check.setChecked(False)
    page.cv_group.max_projects_spin.setValue(3)

    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    session.expire_all()
    assert get_setting(session, KEY_CV_MODE) == CV_MODE_ATS
    assert get_setting(session, KEY_TAILORING_ENABLED) is False
    assert get_setting(session, KEY_MAX_PROJECTS) == 3


def test_items_added_in_the_editors_are_written_to_the_profile(
    qtbot, engine, profile_path, monkeypatch
):
    original = build_sample_profile()
    write_profile(profile_path, original)
    monkeypatch.setattr("src.ui.profile_parts.ExperienceDialog", _FakeExperienceDialog)
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    _choose_mode(page, CV_MODE_ATS)

    page.sections_panel.experience_section._on_add()
    _finish(wizard)

    saved = load_profile(profile_path)
    assert wizard.result() == QDialog.Accepted
    assert len(saved.experience) == 3
    assert saved.experience[-1].company == "Yeni Şirket"
    assert saved.experience[:2] == original.experience
    assert saved.personal_info == original.personal_info
    assert saved.summaries == original.summaries
    assert saved.achievements == original.achievements
    assert len(list(profile_path.parent.glob("master_profile.json.bak-*"))) == 1


def test_untouched_editors_do_not_rewrite_the_profile(qtbot, engine, profile_path):
    write_profile(profile_path, build_sample_profile())
    before = profile_path.read_bytes()
    wizard = _make_wizard(qtbot, engine, profile_path)
    _choose_mode(_page(wizard), CV_MODE_ATS)

    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    assert profile_path.read_bytes() == before
    assert list(profile_path.parent.glob("master_profile.json.bak-*")) == []


def test_ats_mode_without_a_profile_file_is_blocked(qtbot, engine, profile_path, warnings):
    wizard = _make_wizard(qtbot, engine, profile_path)
    page = _page(wizard)
    _choose_mode(page, CV_MODE_ATS)

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert "profil dosyası okunamadı" in warnings[0]
    assert page.profile_status_label.isHidden() is False
    assert page.sections_panel.isEnabled() is False


def test_corrupt_profile_blocks_ats_mode_and_is_never_overwritten(
    qtbot, engine, profile_path, warnings
):
    profile_path.write_text("{ bozuk", encoding="utf-8")
    wizard = _make_wizard(qtbot, engine, profile_path)
    _choose_mode(_page(wizard), CV_MODE_ATS)

    _finish(wizard)

    assert wizard.result() != QDialog.Accepted
    assert profile_path.read_text(encoding="utf-8") == "{ bozuk"


def test_corrupt_profile_does_not_block_own_pdf_mode_and_stays_untouched(
    qtbot, engine, profile_path, pick_pdf
):
    profile_path.write_text("{ bozuk", encoding="utf-8")
    wizard = _make_wizard(qtbot, engine, profile_path)
    _page(wizard).cv_group.on_select_own_cv()

    _finish(wizard)

    assert wizard.result() == QDialog.Accepted
    assert profile_path.read_text(encoding="utf-8") == "{ bozuk"


# --- var olan kullanıcının seçimi sessizce değişmez -----------------------------------------------


def test_onboarded_user_with_default_ats_mode_sees_ats_not_the_recommendation(
    qtbot, engine, profile_path, session
):
    write_profile(profile_path, build_sample_profile())
    set_setting(session, KEY_ONBOARDING_COMPLETED, True)  # CV modu satırı hiç yazılmamış
    session.commit()

    wizard = _make_wizard(qtbot, engine, profile_path, reopen=True)

    assert _page(wizard).cv_group.cv_mode_combo.currentData() == CV_MODE_ATS
    _finish(wizard)
    session.expire_all()
    assert get_setting(session, KEY_CV_MODE) == CV_MODE_ATS


def test_user_who_already_chose_ats_during_this_wizard_keeps_it_when_returning(
    qtbot, engine, profile_path, session
):
    write_profile(profile_path, build_sample_profile())
    set_setting(session, KEY_CV_MODE, CV_MODE_ATS)  # sihirbaz sürüyor (tamamlanmadı)
    session.commit()

    wizard = _make_wizard(qtbot, engine, profile_path)

    assert _page(wizard).cv_group.cv_mode_combo.currentData() == CV_MODE_ATS
