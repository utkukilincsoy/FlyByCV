"""Profil sekmesi: kişisel bilgiler + deneyim/eğitim/proje/yetenek listeleri (Faz 10 Adım 10.3d).

`src/ui/settings_tab.py` ile aynı ilke: ince bir Qt kabuğu, veri/doğrulama
`src/ui/profile_data.py`de (PySide6 İÇERMEZ) saf Python. Veritabanına hiç dokunmaz --
yalnızca `master_profile.json`ı okur/yazar (`src/cv_builder/profile_loader.py`/
`profile_editor.py`).

CLAUDE.md kural 9 ("Profil metinleri kullanıcının CV'sinden birebir alınır") burada madde
metinlerini (bullets/topics/tags dışındaki serbest metin) KISITLAMAZ -- kural 9'un "önce
liste olarak gösterilir ve onay alınır" şartı Adım 10.3b/c'nin (CV'den otomatik çıkarma,
henüz YAPILMADI) konusu; bu sekmede kullanıcı KENDİ profilini KENDİSİ düzenliyor, ayrı bir
onay adımına gerek yok.
"""
import logging
from pathlib import Path
from typing import Optional

from pydantic import ValidationError
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.cv_builder.profile_editor import write_profile
from src.cv_builder.profile_loader import MasterProfile, PersonalInfo, load_profile
from src.ui.icons import set_icon
from src.ui.profile_data import collect_profile, format_validation_error, personal_info_errors
from src.ui.profile_dialogs import EducationDialog, ExperienceDialog, ProjectDialog, SkillDialog
from src.ui.profile_parts import PersonalInfoGroup, ProfileSectionsPanel

logger = logging.getLogger(__name__)

UNSAVED_CHANGES_TITLE = "Kaydedilmemiş değişiklik var"
UNSAVED_CHANGES_TEXT = "Profilde kaydedilmemiş değişiklikler var. Ne yapmak istersiniz?"
UNSAVED_CHANGES_SAVE = "Kaydet"
UNSAVED_CHANGES_DISCARD = "Değişiklikleri at"
UNSAVED_CHANGES_STAY = "Profilde kal"


class ProfileTab(QWidget):
    """Profil sekmesi: kişisel bilgiler formu + dört madde listesi."""

    profile_saved = Signal()

    def __init__(self, profile_path: Path, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._profile_path = profile_path
        self._loaded_profile: Optional[MasterProfile] = None

        self._path_label = QLabel(self)
        self._path_label.setWordWrap(True)
        self._path_label.setStyleSheet("color: gray;")

        personal_group = self._build_personal_info_group()

        # `lambda **kwargs: ExperienceDialog(**kwargs)` -- `ExperienceDialog` DOĞRUDAN
        # geçirilmez: testler diyalog sınıflarını `src.ui.profile_tab.ExperienceDialog` (bu
        # modülün genel adı) üzerinden monkeypatch'liyor, bu yalnızca her çağrıda adın YENİDEN
        # okunmasıyla işe yarar (bkz. `profile_parts.ListSection` docstring'i).
        self._sections_panel = ProfileSectionsPanel(
            {
                "experience": lambda **kwargs: ExperienceDialog(**kwargs),
                "education": lambda **kwargs: EducationDialog(**kwargs),
                "projects": lambda **kwargs: ProjectDialog(**kwargs),
                "skills": lambda **kwargs: SkillDialog(**kwargs),
            },
            self,
        )
        # Dört bölüm `profile_parts.py`de (sihirbazla ortak); eski adlar testler için kalır.
        self._experience_section = self._sections_panel.experience_section
        self._education_section = self._sections_panel.education_section
        self._projects_section = self._sections_panel.projects_section
        self._skills_section = self._sections_panel.skills_section
        self._sections = self._sections_panel.sections

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(self._path_label)
        content_layout.addWidget(personal_group)
        content_layout.addWidget(self._sections_panel)
        content_layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)

        self._save_button = QPushButton("Kaydet", self)
        set_icon(self._save_button, "save")
        self._reload_button = QPushButton("Yeniden yükle", self)
        self._save_button.clicked.connect(self._on_save)
        self._reload_button.clicked.connect(self._on_reload_clicked)

        button_row = QHBoxLayout()
        button_row.addWidget(self._save_button)
        button_row.addWidget(self._reload_button)
        button_row.addStretch(1)

        outer_layout = QVBoxLayout(self)
        outer_layout.addWidget(scroll)
        outer_layout.addLayout(button_row)

        self.reload()

    def _build_personal_info_group(self) -> PersonalInfoGroup:
        """Grup `profile_parts.py`de (sihirbazla ortak); eski `_xxx_edit` adları testler için kalır."""
        group = PersonalInfoGroup(parent=self)
        self._name_edit = group.name_edit
        self._email_edit = group.email_edit
        self._phone_edit = group.phone_edit
        self._location_edit = group.location_edit
        self._linkedin_edit = group.linkedin_edit
        self._intro_edit = group.intro_edit
        self._personal_group = group
        return group

    # -- yükleme / kaydetme -----------------------------------------------------------------

    def reload(self) -> None:
        """`master_profile.json`ı diskten okur ve tüm formu doldurur."""
        try:
            profile = load_profile(self._profile_path)
        except (FileNotFoundError, ValueError) as error:
            self._loaded_profile = None
            self._path_label.setText(f"{self._profile_path} okunamadı: {error}")
            return

        self._loaded_profile = profile
        self._path_label.setText(str(self._profile_path))
        self._set_personal_info(profile.personal_info)

        self._sections_panel.set_profile(profile)

    def _set_personal_info(self, info: PersonalInfo) -> None:
        self._personal_group.set_info(info)

    def _personal_info_from_form(self) -> PersonalInfo:
        return self._personal_group.to_personal_info()

    def is_dirty(self) -> bool:
        if self._loaded_profile is None:
            return False
        if self._personal_info_from_form() != self._loaded_profile.personal_info:
            return True
        return self._sections_panel.differs_from(self._loaded_profile)

    def _on_save(self) -> None:
        if self._loaded_profile is None:
            QMessageBox.critical(
                self, "Kaydedilemedi", "Profil dosyası okunamadan kaydedilemez."
            )
            return

        errors = personal_info_errors(self._name_edit.text(), self._email_edit.text())
        if errors:
            QMessageBox.critical(self, "Profil kaydedilemedi", "\n".join(errors))
            return

        try:
            profile = collect_profile(
                self._loaded_profile,
                personal_info=self._personal_info_from_form(),
                experience=self._experience_section.items,
                education=self._education_section.items,
                projects=self._projects_section.items,
                skills=self._skills_section.items,
            )
        except ValidationError as error:
            QMessageBox.critical(self, "Profil kaydedilemedi", format_validation_error(error))
            return

        backup_path = write_profile(self._profile_path, profile)
        self._loaded_profile = profile
        self.reload()

        message = "Profil kaydedildi."
        if backup_path is not None:
            message += f"\n\nÖnceki sürüm yedeklendi: {backup_path.name}"
        QMessageBox.information(self, "Kaydedildi", message)
        self.profile_saved.emit()

    def _on_reload_clicked(self) -> None:
        if self.is_dirty():
            answer = QMessageBox.question(
                self,
                "Değişiklikler atılsın mı?",
                "Kaydedilmemiş değişiklikler atılıp dosyadan yeniden yüklenecek. Emin misiniz?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.reload()

    def confirm_leave(self) -> bool:
        """Kaydedilmemiş değişiklik varsa üç seçenek sunar (`SettingsTab.confirm_leave` ile
        aynı kalıp); `False` dönerse sekme değişimi geri alınmalı."""
        if not self.is_dirty():
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(UNSAVED_CHANGES_TITLE)
        box.setText(UNSAVED_CHANGES_TEXT)
        save_button = box.addButton(UNSAVED_CHANGES_SAVE, QMessageBox.AcceptRole)
        discard_button = box.addButton(UNSAVED_CHANGES_DISCARD, QMessageBox.DestructiveRole)
        box.addButton(UNSAVED_CHANGES_STAY, QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()

        if clicked is save_button:
            self._on_save()
            return not self.is_dirty()
        if clicked is discard_button:
            self.reload()
            return True
        return False
