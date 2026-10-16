"""src/ui/profile_tab.py testleri (qtbot; diyalog pencereleri sahte sınıflarla değiştirilir,
gerçek bir `QDialog.exec()` hiç çalışmaz -- modal pencere testte asla açılmamalı)."""
from PySide6.QtWidgets import QMessageBox

from src.cv_builder.profile_editor import write_profile
from src.cv_builder.profile_loader import load_profile
from src.ui.profile_tab import ProfileTab
from tests.conftest import build_sample_profile


def _make_tab(qtbot, tmp_path, monkeypatch, profile=None) -> tuple[ProfileTab, object]:
    path = tmp_path / "master_profile.json"
    write_profile(path, profile or build_sample_profile())
    tab = ProfileTab(path)
    qtbot.addWidget(tab)
    return tab, path


class _FakeDialog:
    """`ExperienceDialog`/... yerine geçer -- `exec()` gerçek bir modal AÇMAZ."""

    Accepted = 1
    Rejected = 0

    #: Testin `values()`den dönmesini istediği veri -- her test sınıfı kendi alt sınıfını tanımlar.
    result_values: dict = {}
    accepted = True

    def __init__(self, initial=None, parent=None):
        self.initial = initial

    def exec(self):
        return self.Accepted if self.accepted else self.Rejected

    def values(self):
        return dict(self.result_values)


def _fake_dialog_cls(values: dict, accepted: bool = True) -> type:
    return type(
        "_FakeDialog",
        (_FakeDialog,),
        {"result_values": values, "accepted": accepted},
    )


def _deny_all_dialogs(monkeypatch) -> None:
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)


# ---------------------------------------------------------------------------
# Yükleme
# ---------------------------------------------------------------------------


def test_loads_personal_info_and_lists_from_disk(qtbot, tmp_path, monkeypatch):
    tab, _ = _make_tab(qtbot, tmp_path, monkeypatch)

    assert tab._name_edit.text() == "Test Kişi"
    assert tab._email_edit.text() == "test@example.com"
    assert tab._experience_section.list_widget.count() == 2
    assert tab._education_section.list_widget.count() == 2
    assert tab._projects_section.list_widget.count() == 4
    assert tab._skills_section.list_widget.count() == 3
    assert tab.is_dirty() is False


def test_missing_profile_file_shows_message_without_crashing(qtbot, tmp_path):
    tab = ProfileTab(tmp_path / "yok.json")
    qtbot.addWidget(tab)

    assert "okunamadı" in tab._path_label.text()
    assert tab.is_dirty() is False


# ---------------------------------------------------------------------------
# Kişisel bilgiler
# ---------------------------------------------------------------------------


def test_editing_personal_info_marks_dirty_until_saved(qtbot, tmp_path, monkeypatch):
    tab, path = _make_tab(qtbot, tmp_path, monkeypatch)
    _deny_all_dialogs(monkeypatch)

    tab._name_edit.setText("Yeni Ad")
    assert tab.is_dirty() is True

    tab._on_save()

    assert tab.is_dirty() is False
    assert load_profile(path).personal_info.name == "Yeni Ad"


def test_save_rejects_blank_name(qtbot, tmp_path, monkeypatch):
    tab, path = _make_tab(qtbot, tmp_path, monkeypatch)
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: errors.append(a) or QMessageBox.Ok)
    tab._name_edit.setText("   ")

    tab._on_save()

    assert len(errors) == 1
    assert "Ad soyad" in errors[0][2]
    # Dosya değişmemiş olmalı.
    assert load_profile(path).personal_info.name == "Test Kişi"


def test_save_writes_backup_of_previous_version(qtbot, tmp_path, monkeypatch):
    tab, path = _make_tab(qtbot, tmp_path, monkeypatch)
    _deny_all_dialogs(monkeypatch)
    tab._name_edit.setText("Yeni Ad")

    tab._on_save()

    backups = list(tmp_path.glob("master_profile.json.bak-*"))
    assert len(backups) == 1


# ---------------------------------------------------------------------------
# Madde ekleme/düzenleme/silme (Deneyim üzerinden -- diğer üç bölüm aynı `_ListSection`)
# ---------------------------------------------------------------------------


def test_add_experience_item(qtbot, tmp_path, monkeypatch):
    tab, path = _make_tab(qtbot, tmp_path, monkeypatch)
    section = tab._experience_section
    monkeypatch.setattr(
        "src.ui.profile_tab.ExperienceDialog",
        _fake_dialog_cls(
            {
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
        ),
    )

    section._on_add()

    assert section.list_widget.count() == 3
    assert any(item.company == "Yeni Şirket" for item in section.items)
    assert tab.is_dirty() is True

    _deny_all_dialogs(monkeypatch)
    tab._on_save()
    reloaded = load_profile(path)
    assert any(item.company == "Yeni Şirket" for item in reloaded.experience)


def test_add_experience_item_shows_error_on_invalid_tag(qtbot, tmp_path, monkeypatch):
    tab, path = _make_tab(qtbot, tmp_path, monkeypatch)
    section = tab._experience_section
    monkeypatch.setattr(
        "src.ui.profile_tab.ExperienceDialog",
        _fake_dialog_cls(
            {
                "company": "Şirket",
                "title": "Unvan",
                "department": None,
                "note": None,
                "topics": [],
                "start_date": "2024-01",
                "end_date": None,
                "bullets": ["Madde"],
                "tags": ["uydurma-etiket"],
            }
        ),
    )
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    section._on_add()

    assert len(warnings) == 1
    assert section.list_widget.count() == 2  # eklenmedi


def test_edit_experience_item_preserves_id(qtbot, tmp_path, monkeypatch):
    tab, path = _make_tab(qtbot, tmp_path, monkeypatch)
    section = tab._experience_section
    original_id = section.items[0].id
    section.list_widget.setCurrentRow(0)
    monkeypatch.setattr(
        "src.ui.profile_tab.ExperienceDialog",
        _fake_dialog_cls(
            {
                "company": "Güncellenmiş Şirket",
                "title": "Unvan",
                "department": None,
                "note": None,
                "topics": [],
                "start_date": "2020",
                "end_date": None,
                "bullets": ["Açıklama"],
                "tags": ["backend"],
            }
        ),
    )

    section._on_edit()

    assert section.items[0].id == original_id
    assert section.items[0].company == "Güncellenmiş Şirket"


def test_delete_experience_item_requires_confirmation(qtbot, tmp_path, monkeypatch):
    tab, path = _make_tab(qtbot, tmp_path, monkeypatch)
    section = tab._experience_section
    section.list_widget.setCurrentRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)

    section._on_delete()
    assert section.list_widget.count() == 2  # reddedildi, silinmedi

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    section._on_delete()
    assert section.list_widget.count() == 1


def test_no_item_selected_shows_info_message(qtbot, tmp_path, monkeypatch):
    tab, path = _make_tab(qtbot, tmp_path, monkeypatch)
    section = tab._experience_section
    section.list_widget.setCurrentRow(-1)
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a) or QMessageBox.Ok)

    section._on_edit()
    section._on_delete()

    assert len(infos) == 2


# ---------------------------------------------------------------------------
# Yeniden yükle / sekme değişimi
# ---------------------------------------------------------------------------


def test_reload_discards_unsaved_changes_with_confirmation(qtbot, tmp_path, monkeypatch):
    tab, path = _make_tab(qtbot, tmp_path, monkeypatch)
    tab._name_edit.setText("Geçici Ad")
    assert tab.is_dirty() is True

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    tab._on_reload_clicked()

    assert tab._name_edit.text() == "Test Kişi"
    assert tab.is_dirty() is False


def test_confirm_leave_true_when_not_dirty(qtbot, tmp_path, monkeypatch):
    tab, path = _make_tab(qtbot, tmp_path, monkeypatch)
    assert tab.confirm_leave() is True


def test_confirm_leave_discard_resets_form(qtbot, tmp_path, monkeypatch):
    tab, path = _make_tab(qtbot, tmp_path, monkeypatch)
    tab._name_edit.setText("Geçici Ad")

    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)

    def _clicked(self):
        for button in self.buttons():
            if button.text() == "Değişiklikleri at":
                return button
        raise AssertionError("buton bulunamadı")

    monkeypatch.setattr(QMessageBox, "clickedButton", _clicked)

    result = tab.confirm_leave()

    assert result is True
    assert tab._name_edit.text() == "Test Kişi"


def test_confirm_leave_stay_keeps_changes(qtbot, tmp_path, monkeypatch):
    tab, path = _make_tab(qtbot, tmp_path, monkeypatch)
    tab._name_edit.setText("Geçici Ad")

    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)

    def _clicked(self):
        for button in self.buttons():
            if button.text() == "Profilde kal":
                return button
        raise AssertionError("buton bulunamadı")

    monkeypatch.setattr(QMessageBox, "clickedButton", _clicked)

    result = tab.confirm_leave()

    assert result is False
    assert tab._name_edit.text() == "Geçici Ad"
