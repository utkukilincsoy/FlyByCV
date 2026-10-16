"""Profil sekmesinin yeniden kullanılabilir parçaları (PLAN.md Adım 10.2f/10.2h).

`ProfileTab` ve kurulum sihirbazı (kişisel bilgi + CV sayfaları) AYNI düzenleyicileri kullanır;
kopya yok. Burası yalnızca Qt kabuğudur: doğrulama/birleştirme `src/ui/profile_data.py`de, dosyaya
yazma `src/cv_builder/profile_editor.py`de, veritabanına hiç dokunulmaz.
"""
from typing import Callable, Mapping, Optional, TypeVar

from pydantic import ValidationError
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.cv_builder.profile_loader import (
    Education,
    Experience,
    MasterProfile,
    PersonalInfo,
    Project,
    Skill,
)
from src.ui.profile_data import (
    all_item_ids,
    education_summary,
    experience_summary,
    format_validation_error,
    generate_item_id,
    project_summary,
    skill_summary,
)
from src.ui.profile_dialogs import EducationDialog, ExperienceDialog, ProjectDialog, SkillDialog

T = TypeVar("T")

INTRO_PLACEHOLDER = 'Mailde "Ben {ad}, ...` cümlesinde kullanılır'

#: `dialog_factory(parent=...)` / `dialog_factory(initial=..., parent=...)` -> diyalog.
DialogFactory = Callable[..., QDialog]


class PersonalInfoGroup(QGroupBox):
    """"Kişisel bilgiler" grubu. `show_extras=False` (sihirbaz) Şehir/konum ve Kısa tanıtım
    satırlarını gizler; bu alanlar yine de `set_info`/`to_personal_info` ile AYNEN korunur, yani
    sihirbaz profildeki var olan konum/tanıtım metnini silmez."""

    def __init__(self, show_extras: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__("Kişisel bilgiler", parent)
        form = QFormLayout()

        self.name_edit = QLineEdit(self)
        self.email_edit = QLineEdit(self)
        self.phone_edit = QLineEdit(self)
        self.location_edit = QLineEdit(self)
        self.linkedin_edit = QLineEdit(self)
        self.intro_edit = QLineEdit(self)
        self.intro_edit.setPlaceholderText(INTRO_PLACEHOLDER)

        form.addRow("Ad soyad*", self.name_edit)
        form.addRow("E-posta*", self.email_edit)
        form.addRow("Telefon", self.phone_edit)
        form.addRow("Şehir/konum", self.location_edit)
        form.addRow("LinkedIn", self.linkedin_edit)
        form.addRow("Kısa tanıtım", self.intro_edit)
        self.setLayout(form)

        if not show_extras:
            for edit in (self.location_edit, self.intro_edit):
                edit.setVisible(False)
                label = form.labelForField(edit)
                if label is not None:
                    label.setVisible(False)

    def set_info(self, info: PersonalInfo) -> None:
        self.name_edit.setText(info.name)
        self.email_edit.setText(info.email)
        self.phone_edit.setText(info.phone or "")
        self.location_edit.setText(info.location or "")
        self.linkedin_edit.setText(info.linkedin or "")
        self.intro_edit.setText(info.intro or "")

    def to_personal_info(self) -> PersonalInfo:
        return PersonalInfo(
            name=self.name_edit.text().strip(),
            email=self.email_edit.text().strip(),
            phone=self.phone_edit.text().strip() or None,
            location=self.location_edit.text().strip() or None,
            linkedin=self.linkedin_edit.text().strip() or None,
            intro=self.intro_edit.text().strip() or None,
        )


class ListSection:
    """Bir madde listesinin (Deneyim/Eğitim/Proje/Yetenek) widget'ları + davranışı.

    Dört bölüm de AYNI iskeleti paylaşıyor (liste + Ekle/Düzenle/Sil); tek fark diyalog
    sınıfı ve özet fonksiyonu -- bu yüzden dört ayrı `QGroupBox` alt sınıfı yerine tek bir
    yardımcı nesne parametreleştiriliyor.
    """

    def __init__(
        self,
        category: str,
        title: str,
        parent: QWidget,
        dialog_factory: DialogFactory,
        summary_fn: Callable[[T], str],
        item_cls: type,
    ) -> None:
        self.category = category
        #: Diyalog sınıfını DOĞRUDAN saklamak yerine bir fabrika fonksiyonu tutulur -- testler
        #: `src.ui.profile_tab.ExperienceDialog` gibi modül adını monkeypatch'liyor, bu ancak
        #: her çağrıda modül genel adı yeniden okunursa (kapanış/closure ile) işe yarar.
        self._dialog_factory = dialog_factory
        self._summary_fn = summary_fn
        self._item_cls = item_cls
        self.items: list[T] = []

        self.box = QGroupBox(title, parent)
        self.list_widget = QListWidget(parent)

        add_button = QPushButton("Ekle", parent)
        edit_button = QPushButton("Düzenle", parent)
        delete_button = QPushButton("Sil", parent)
        add_button.clicked.connect(self._on_add)
        edit_button.clicked.connect(self._on_edit)
        delete_button.clicked.connect(self._on_delete)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._on_edit())

        button_row = QHBoxLayout()
        button_row.addWidget(add_button)
        button_row.addWidget(edit_button)
        button_row.addWidget(delete_button)
        button_row.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(self.list_widget)
        layout.addLayout(button_row)
        self.box.setLayout(layout)

        #: Yeni id üretirken çarpışmayı önlemek için -- `set_items()` ile birlikte güncellenir.
        self._existing_ids: set[str] = set()

    def set_items(self, items: list[T], existing_ids: set[str]) -> None:
        self.items = list(items)
        self._existing_ids = set(existing_ids)
        self._refresh_list()

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        for item in self.items:
            self.list_widget.addItem(QListWidgetItem(self._summary_fn(item)))

    def _selected_index(self) -> Optional[int]:
        row = self.list_widget.currentRow()
        return row if row >= 0 else None

    def _on_add(self) -> None:
        dialog = self._dialog_factory(parent=self.box)
        if dialog.exec() != dialog.Accepted:
            return
        new_id = generate_item_id(self.category, self._existing_ids)
        try:
            item = self._item_cls(id=new_id, **dialog.values())
        except ValidationError as error:
            QMessageBox.warning(self.box, "Madde eklenemedi", format_validation_error(error))
            return
        self._existing_ids.add(new_id)
        self.items.append(item)
        self._refresh_list()
        self.list_widget.setCurrentRow(len(self.items) - 1)

    def _on_edit(self) -> None:
        index = self._selected_index()
        if index is None:
            QMessageBox.information(self.box, "Madde seçilmedi", "Önce listeden bir madde seçin.")
            return
        current = self.items[index]
        dialog = self._dialog_factory(initial=current, parent=self.box)
        if dialog.exec() != dialog.Accepted:
            return
        try:
            item = self._item_cls(id=current.id, **dialog.values())
        except ValidationError as error:
            QMessageBox.warning(self.box, "Madde kaydedilemedi", format_validation_error(error))
            return
        self.items[index] = item
        self._refresh_list()
        self.list_widget.setCurrentRow(index)

    def _on_delete(self) -> None:
        index = self._selected_index()
        if index is None:
            QMessageBox.information(self.box, "Madde seçilmedi", "Önce listeden bir madde seçin.")
            return
        removed = self.items[index]
        answer = QMessageBox.question(
            self.box,
            "Madde silinsin mi?",
            f"'{self._summary_fn(removed)}' silinecek. Emin misiniz?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._existing_ids.discard(removed.id)
        del self.items[index]
        self._refresh_list()


class ProfileSectionsPanel(QWidget):
    """Deneyim / Eğitim / Projeler / Yetenekler: dört madde listesi alt alta.

    `dialog_factories` (`{"experience": fabrika, ...}`) verilmezse `profile_dialogs`taki gerçek
    diyaloglar kullanılır; `ProfileTab` kendi modül adlarını (`src.ui.profile_tab.ExperienceDialog`)
    verir, çünkü testler diyalogları orada değiştiriyor (bkz. `ListSection`).
    """

    def __init__(
        self,
        dialog_factories: Optional[Mapping[str, DialogFactory]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        factories = {
            "experience": lambda **kwargs: ExperienceDialog(**kwargs),
            "education": lambda **kwargs: EducationDialog(**kwargs),
            "projects": lambda **kwargs: ProjectDialog(**kwargs),
            "skills": lambda **kwargs: SkillDialog(**kwargs),
            **(dialog_factories or {}),
        }
        self.experience_section = ListSection(
            "experience", "Deneyim", self, factories["experience"], experience_summary, Experience
        )
        self.education_section = ListSection(
            "education", "Eğitim", self, factories["education"], education_summary, Education
        )
        self.projects_section = ListSection(
            "projects", "Projeler", self, factories["projects"], project_summary, Project
        )
        self.skills_section = ListSection(
            "skills", "Yetenekler", self, factories["skills"], skill_summary, Skill
        )
        self.sections = (
            self.experience_section,
            self.education_section,
            self.projects_section,
            self.skills_section,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for section in self.sections:
            layout.addWidget(section.box)

    def set_profile(self, profile: MasterProfile) -> None:
        existing_ids = all_item_ids(profile)
        for section in self.sections:
            section.set_items(list(getattr(profile, section.category)), existing_ids)

    def differs_from(self, profile: MasterProfile) -> bool:
        return any(
            section.items != getattr(profile, section.category) for section in self.sections
        )
