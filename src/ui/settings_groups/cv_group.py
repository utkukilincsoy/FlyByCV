"""\"CV\" grubu: CV modu (ATS / kendi PDF'im), PDF yükleme, özelleştirme, en çok proje sayısı.

PDF seç/kaldır kendi başına çalışır (dosyayı hemen `paths.own_cv_path()`e kopyalar, "Kaydet"i
beklemez), bu yüzden grup bunu kendisi yapar; başka bir pencereye konduğunda ek kablolama gerekmez.
"""
from pathlib import Path
from typing import Any, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QWidget,
)

from src.core.settings import CV_MODE_OWN_PDF, CV_MODES
from src.cv_builder.own_cv import own_cv_status_text, remove_own_cv, set_own_cv
from src.ui.settings_data import (
    CV_MODE_DESCRIPTIONS_TR,
    CV_MODE_LABELS_TR,
    MAX_PROJECTS_UNLIMITED,
    SettingsForm,
)
from src.ui.settings_groups.common import hint_label, select_index_by_data


class CvGroup(QGroupBox):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("CV", parent)
        form = QFormLayout()

        self.cv_mode_combo = QComboBox(self)
        for mode in CV_MODES:
            self.cv_mode_combo.addItem(CV_MODE_LABELS_TR[mode], mode)
        self.cv_mode_combo.currentIndexChanged.connect(self._on_cv_mode_changed)
        form.addRow("CV modu", self.cv_mode_combo)

        self.cv_mode_desc_label = hint_label("", self)
        form.addRow(self.cv_mode_desc_label)

        self.own_cv_status_label = QLabel(self)
        own_cv_select_button = QPushButton("PDF seç…", self)
        own_cv_remove_button = QPushButton("Kaldır", self)
        own_cv_select_button.clicked.connect(self.on_select_own_cv)
        own_cv_remove_button.clicked.connect(self.on_remove_own_cv)

        own_cv_row = QHBoxLayout()
        own_cv_row.addWidget(self.own_cv_status_label)
        own_cv_row.addWidget(own_cv_select_button)
        own_cv_row.addWidget(own_cv_remove_button)
        own_cv_row.addStretch(1)
        own_cv_row_widget = QWidget(self)
        own_cv_row_widget.setLayout(own_cv_row)
        form.addRow("Kendi PDF'im", own_cv_row_widget)
        self.own_cv_row_widget = own_cv_row_widget

        self.tailoring_check = QCheckBox("CV'yi şirkete göre özelleştir", self)
        form.addRow(self.tailoring_check)

        self.max_projects_spin = QSpinBox(self)
        self.max_projects_spin.setRange(0, 50)
        self.max_projects_spin.setSpecialValueText("sınırsız (tümü)")
        form.addRow("En çok proje sayısı", self.max_projects_spin)
        self.max_projects_row_label = form.labelForField(self.max_projects_spin)

        self.setLayout(form)

    def _on_cv_mode_changed(self, *_args) -> None:
        own_pdf = self.cv_mode_combo.currentData() == CV_MODE_OWN_PDF
        self.cv_mode_desc_label.setText(CV_MODE_DESCRIPTIONS_TR.get(self.cv_mode_combo.currentData(), ""))
        self.own_cv_row_widget.setVisible(own_pdf)
        self.tailoring_check.setVisible(not own_pdf)
        self.max_projects_spin.setVisible(not own_pdf)
        if self.max_projects_row_label is not None:
            self.max_projects_row_label.setVisible(not own_pdf)

    def refresh_own_cv_status(self) -> None:
        self.own_cv_status_label.setText(own_cv_status_text())

    def on_select_own_cv(self) -> None:
        """Seçilen PDF'i hemen `paths.own_cv_path()`e kopyalar (API anahtarı kaydetme ile
        aynı ilke: dosya işlemi "Kaydet"i beklemez, kullanıcı tetikler tetiklemez olur)."""
        path_str, _ = QFileDialog.getOpenFileName(self, "PDF seç", "", "PDF dosyaları (*.pdf)")
        if not path_str:
            return
        try:
            set_own_cv(Path(path_str))
        except (FileNotFoundError, ValueError) as error:
            QMessageBox.warning(self, "PDF kaydedilemedi", str(error))
            return
        self.refresh_own_cv_status()

    def on_remove_own_cv(self) -> None:
        if not remove_own_cv():
            QMessageBox.information(self, "Kayıt yok", "Kayıtlı bir PDF yoktu.")
        self.refresh_own_cv_status()

    def values(self) -> dict[str, Any]:
        return {
            "cv_mode": self.cv_mode_combo.currentData(),
            "tailoring_enabled": self.tailoring_check.isChecked(),
            "max_projects": self.max_projects_spin.value() or None,
        }

    def set_values(self, form: SettingsForm) -> None:
        select_index_by_data(self.cv_mode_combo, form.cv_mode)
        self._on_cv_mode_changed()
        self.refresh_own_cv_status()

        self.tailoring_check.setChecked(form.tailoring_enabled)
        self.max_projects_spin.setValue(form.max_projects or MAX_PROJECTS_UNLIMITED)
