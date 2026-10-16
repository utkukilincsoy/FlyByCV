"""\"Mail şablonu\" grubu: hedef / meslek / mail dili, konu, gövde, yer tutucular, önizleme.

Hedef/meslek/dil değişince önerilen şablon (`settings_data.suggested_mail_template`) YALNIZCA
kullanıcı onaylarsa ekrandaki metnin üzerine yazılır (CLAUDE.md kural 1/9). Meslek seçimi başka
yerleri de ilgilendirir (kaynak özeti); bunun için `profession_combo.currentIndexChanged`
dışarıdan bağlanır.
"""
from pathlib import Path
from typing import Any, Optional

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.custom_profession import CUSTOM_PROFESSION_ID, presets_with_custom
from src.core.professions import MailTemplateSuggestion, ProfessionPreset
from src.cv_builder.mail_template import LANGUAGES, PLACEHOLDERS, fill_mail_template
from src.cv_builder.profile_loader import load_profile
from src.ui.settings_data import (
    LLM_TEST_COMPANY,
    MAIL_LANGUAGE_LABELS_TR,
    SettingsForm,
    suggested_mail_template,
)
from src.ui.settings_groups.custom_profession_fields import CustomProfessionFields
from src.ui.settings_groups.common import (
    hint_label,
    populate_profession_combo,
    populate_target_combo,
    select_index_by_data,
)


class MailTemplateGroup(QGroupBox):
    def __init__(
        self,
        profession_presets: dict[str, ProfessionPreset],
        generic_templates: dict[tuple[str, str], MailTemplateSuggestion],
        profile_path: Path,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__("Mail şablonu", parent)
        self._base_presets = profession_presets
        self._generic_mail_templates = generic_templates
        self._profile_path = profile_path
        layout = QVBoxLayout()

        context_form = QFormLayout()

        self.target_combo = QComboBox(self)
        populate_target_combo(self.target_combo)
        self.target_combo.currentIndexChanged.connect(self._on_template_context_changed)
        context_form.addRow("Hedef", self.target_combo)

        self.profession_combo = QComboBox(self)
        populate_profession_combo(self.profession_combo, self._base_presets)
        self.profession_combo.currentIndexChanged.connect(self._update_custom_fields_visible)
        self.profession_combo.currentIndexChanged.connect(self._on_template_context_changed)
        context_form.addRow("Meslek", self.profession_combo)

        self.mail_language_combo = QComboBox(self)
        for language in LANGUAGES:
            self.mail_language_combo.addItem(MAIL_LANGUAGE_LABELS_TR[language], language)
        self.mail_language_combo.currentIndexChanged.connect(self._on_template_context_changed)
        context_form.addRow("Mail dili", self.mail_language_combo)

        layout.addLayout(context_form)

        self.custom_fields = CustomProfessionFields(self)
        layout.addWidget(self.custom_fields)

        context_hint = hint_label(
            "Hedef/meslek/dil seçimi, aşağıdaki şablon için bir öneri sunar — mevcut metnin "
            "üzerine yalnızca onayınızla yazılır.",
            self,
        )
        layout.addWidget(context_hint)

        layout.addWidget(QLabel("Konu", self))
        self.mail_subject_edit = QLineEdit(self)
        layout.addWidget(self.mail_subject_edit)

        layout.addWidget(QLabel("Gövde", self))
        self.mail_body_edit = QPlainTextEdit(self)
        self.mail_body_edit.setMinimumHeight(160)
        layout.addWidget(self.mail_body_edit)

        placeholders_hint = hint_label(
            "Yer tutucular (profilden otomatik doldurulur): " + ", ".join(PLACEHOLDERS), self
        )
        layout.addWidget(placeholders_hint)

        self.preview_button = QPushButton("Önizle", self)
        self.preview_button.clicked.connect(self.on_preview)
        layout.addWidget(self.preview_button)

        self.setLayout(layout)

    def _is_custom_selected(self) -> bool:
        return self.profession_combo.currentData() == CUSTOM_PROFESSION_ID

    def _update_custom_fields_visible(self, *_args) -> None:
        self.custom_fields.set_active(self._is_custom_selected())

    def custom_preset(self) -> Optional[ProfessionPreset]:
        """\"Diğer\" seçiliyse ekrandaki ad/kelimelerden bellekte paket (geçersizse `None`), değilse `None`."""
        return self.custom_fields.preset() if self._is_custom_selected() else None

    def custom_profession_errors(self) -> list[str]:
        """\"Diğer\" seçiliyken ad/kelime hataları; başka bir meslek seçiliyse boş."""
        return self.custom_fields.errors() if self._is_custom_selected() else []

    def _presets(self) -> dict[str, ProfessionPreset]:
        """Paket sözlüğü + (geçerliyse) ekrandaki \"Diğer\" mesleği."""
        custom = self.custom_fields.values()
        return presets_with_custom(
            self._base_presets, custom["custom_profession_name"], custom["custom_profession_keywords"]
        )

    def _on_template_context_changed(self, *_args) -> None:
        """Hedef/Meslek/Mail dili combo'larından biri değişince çağrılır (bkz. `offer_suggestion`)."""
        self.offer_suggestion()

    def offer_suggestion(self) -> None:
        """Ekrandaki hedef/meslek/dil için önerilen şablonu, ONAYLA yazar.

        Kurulum sihirbazı da (şablon sayfası ilk açıldığında) bunu çağırır. Bir öneri varsa VE ekrandaki (henüz kaydedilmemiş) konu/gövdeden FARKLIYSA onay ister;
        onaylanmazsa ekrandaki metne DOKUNULMAZ (CLAUDE.md kural 1/9 — kullanıcının kendi
        metni sormadan ezilmez). Yalnızca combo'ları doldurmak için (`set_values`) tetiklendiğinde
        de aynı yoldan geçer, bu yüzden `reload()` sırasında veritabanından gelen combo değeri
        ekrandaki mevcut şablonla zaten eşleşiyorsa hiçbir şey OLMAZ (fark yoksa onay sorulmaz).
        """
        target = self.target_combo.currentData()
        profession_id = self.profession_combo.currentData()
        language = self.mail_language_combo.currentData()

        suggestion = suggested_mail_template(
            target, profession_id, language, self._presets(), self._generic_mail_templates
        )
        if suggestion is None:
            return

        current_subject = self.mail_subject_edit.text()
        current_body = self.mail_body_edit.toPlainText()
        if suggestion.subject == current_subject and suggestion.body == current_body:
            return

        answer = QMessageBox.question(
            self,
            "Şablon önerisi",
            "Hedef/meslek/dil seçiminize göre önerilen bir mail şablonu var. Şu anki mail "
            "konusu/gövdesinin üzerine yazılsın mı? (Elle düzenlediyseniz kaybolur.)\n\n"
            f"Konu: {suggestion.subject}\n\nGövde:\n{suggestion.body}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.mail_subject_edit.setText(suggestion.subject)
        self.mail_body_edit.setPlainText(suggestion.body)

    def on_preview(self) -> None:
        """Ekrandaki (henüz kaydedilmemiş) şablonu örnek bir şirket adıyla doldurup gösterir.

        Veritabanına/ağa dokunmaz; yalnızca profili okur (kişisel bilgiler gerçek olsun diye).
        """
        try:
            profile = load_profile(self._profile_path)
        except (FileNotFoundError, ValueError) as error:
            QMessageBox.warning(self, "Önizleme yapılamadı", f"Profil okunamadı: {error}")
            return

        profession_id = self.profession_combo.currentData()
        profession = self._presets().get(profession_id) if profession_id else None
        subject, body = fill_mail_template(
            self.mail_subject_edit.text(),
            self.mail_body_edit.toPlainText(),
            profile,
            LLM_TEST_COMPANY,
            target=self.target_combo.currentData(),
            language=self.mail_language_combo.currentData(),
            profession_name=profession.name_tr if profession else None,
        )
        QMessageBox.information(self, "Mail önizleme", f"Konu: {subject}\n\n{body}")

    def values(self) -> dict[str, Any]:
        return {
            "mail_template_subject": self.mail_subject_edit.text(),
            "mail_template_body": self.mail_body_edit.toPlainText(),
            "application_target": self.target_combo.currentData(),
            "profession": self.profession_combo.currentData(),
            "mail_language": self.mail_language_combo.currentData(),
            **self.custom_fields.values(),
        }

    def set_values(self, form: SettingsForm) -> None:
        self.mail_subject_edit.setText(form.mail_template_subject)
        self.mail_body_edit.setPlainText(form.mail_template_body)
        self.custom_fields.set_values(form)

        # Combo'ları programatik doldururken `_on_template_context_changed` TETİKLENMEMELİ --
        # aksi halde her `reload()`da (ör. sekmeyi her açışta) kaydedilmiş şablon, o anki
        # hedef/meslek/dil ile üretilen bir öneriden FARKLIYSA kullanıcıya sormadan bir onay
        # penceresi çıkardı (kaydedilmiş şablon zaten kullanıcının kendi metni olabilir).
        # Aynı engel, bu combo'lara dışarıdan bağlanan sinyalleri (ör. kaynak özeti) de kapsar.
        combos = (self.target_combo, self.profession_combo, self.mail_language_combo)
        for combo in combos:
            combo.blockSignals(True)
        select_index_by_data(self.target_combo, form.application_target)
        select_index_by_data(self.profession_combo, form.profession)
        select_index_by_data(self.mail_language_combo, form.mail_language)
        for combo in combos:
            combo.blockSignals(False)
        self._update_custom_fields_visible()  # combo sinyalleri susturulmuşken değişti
