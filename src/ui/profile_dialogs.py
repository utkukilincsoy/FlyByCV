"""Profil sekmesi için madde ekleme/düzenleme diyalogları (Faz 10 Adım 10.3d).

`src/ui/dialogs.py::AddCompanyDialog` ile aynı kalıp: `QDialog` alt sınıfı, `values()` ham
form verisini döner. Buradaki fark -- Kaydet (`Tamam`) butonu doğrudan `accept()`e
BAĞLANMAZ: `_on_accept()` önce zorunlu alanları ve tarih biçimini (`check_year_month`)
kontrol eder, geçmezse diyalog AÇIK kalır (kullanıcı aynı pencerede düzeltir). Etiket
geçerliliği (`TAG_VOCABULARY`) burada KONTROL EDİLMEZ -- `profile_loader.ProfileItem`
kurucusu zaten kontrol ediyor, iki ayrı doğrulama kaynağı olmasın diye (`profile_tab.py`
madde nesnesini kurarken yakalayıp aynı diyalogda gösterir).
"""
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.cv_builder.profile_loader import Education, Experience, Project, Skill, check_year_month
from src.ui.profile_data import TAG_HINT_TR, format_comma_list, format_lines, parse_comma_list, parse_lines


def _tag_hint_label(parent: QWidget) -> QLabel:
    label = QLabel(TAG_HINT_TR, parent)
    label.setWordWrap(True)
    label.setStyleSheet("color: gray;")
    return label


def _valid_date_or_none(text: str) -> Optional[str]:
    """Boşsa `None` ("Devam ediyor"), doluysa `check_year_month` ile doğrulanmış metni döner.

    Raises:
        ValueError: biçim 'YYYY'/'YYYY-MM' değilse (`check_year_month`nin kendi Türkçe mesajı).
    """
    cleaned = text.strip()
    return check_year_month(cleaned) if cleaned else None


class ExperienceDialog(QDialog):
    """Deneyim maddesi ekleme/düzenleme."""

    def __init__(self, initial: Optional[Experience] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Deneyim düzenle" if initial else "Deneyim ekle")

        self._company_edit = QLineEdit(self)
        self._title_edit = QLineEdit(self)
        self._department_edit = QLineEdit(self)
        self._note_edit = QLineEdit(self)
        self._topics_edit = QLineEdit(self)
        self._topics_edit.setPlaceholderText("Virgülle ayırın (ör. Python, REST API)")
        self._start_date_edit = QLineEdit(self)
        self._start_date_edit.setPlaceholderText("YYYY veya YYYY-MM")
        self._end_date_edit = QLineEdit(self)
        self._end_date_edit.setPlaceholderText("Boşsa: devam ediyor")
        self._bullets_edit = QPlainTextEdit(self)
        self._bullets_edit.setPlaceholderText("Her satır bir madde")
        self._tags_edit = QLineEdit(self)

        if initial is not None:
            self._company_edit.setText(initial.company)
            self._title_edit.setText(initial.title)
            self._department_edit.setText(initial.department or "")
            self._note_edit.setText(initial.note or "")
            self._topics_edit.setText(", ".join(initial.topics))
            self._start_date_edit.setText(initial.start_date)
            self._end_date_edit.setText(initial.end_date or "")
            self._bullets_edit.setPlainText(format_lines(initial.bullets))
            self._tags_edit.setText(format_comma_list(initial.tags))

        form = QFormLayout()
        form.addRow("Şirket*", self._company_edit)
        form.addRow("Unvan*", self._title_edit)
        form.addRow("Departman", self._department_edit)
        form.addRow("Not (ör. Tam zamanlı)", self._note_edit)
        form.addRow("Konular", self._topics_edit)
        form.addRow("Başlangıç*", self._start_date_edit)
        form.addRow("Bitiş", self._end_date_edit)
        form.addRow("Maddeler", self._bullets_edit)
        form.addRow("Etiketler", self._tags_edit)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(_tag_hint_label(self))
        layout.addWidget(self._buttons)

    def _on_accept(self) -> None:
        if not self._company_edit.text().strip():
            QMessageBox.warning(self, "Eksik alan", "Şirket boş olamaz.")
            return
        if not self._title_edit.text().strip():
            QMessageBox.warning(self, "Eksik alan", "Unvan boş olamaz.")
            return
        try:
            check_year_month(self._start_date_edit.text().strip())
            _valid_date_or_none(self._end_date_edit.text())
        except ValueError as error:
            QMessageBox.warning(self, "Geçersiz tarih", str(error))
            return
        self.accept()

    def values(self) -> dict:
        return {
            "company": self._company_edit.text().strip(),
            "title": self._title_edit.text().strip(),
            "department": self._department_edit.text().strip() or None,
            "note": self._note_edit.text().strip() or None,
            "topics": parse_comma_list(self._topics_edit.text()),
            "start_date": self._start_date_edit.text().strip(),
            "end_date": _valid_date_or_none(self._end_date_edit.text()),
            "bullets": parse_lines(self._bullets_edit.toPlainText()),
            "tags": parse_comma_list(self._tags_edit.text()),
        }


class EducationDialog(QDialog):
    """Eğitim maddesi ekleme/düzenleme."""

    def __init__(self, initial: Optional[Education] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Eğitim düzenle" if initial else "Eğitim ekle")

        self._school_edit = QLineEdit(self)
        self._field_edit = QLineEdit(self)
        self._degree_edit = QLineEdit(self)
        self._gpa_edit = QLineEdit(self)
        self._gpa_edit.setPlaceholderText("ör. 3.20/4.00")
        self._show_gpa_check = QCheckBox("CV'de göster", self)
        self._show_gpa_check.setChecked(True)
        self._start_date_edit = QLineEdit(self)
        self._start_date_edit.setPlaceholderText("YYYY veya YYYY-MM")
        self._end_date_edit = QLineEdit(self)
        self._end_date_edit.setPlaceholderText("Boşsa: devam ediyor")
        self._expected_check = QCheckBox("Beklenen mezuniyet (henüz alınmadı)", self)
        self._tags_edit = QLineEdit(self)

        if initial is not None:
            self._school_edit.setText(initial.school)
            self._field_edit.setText(initial.field)
            self._degree_edit.setText(initial.degree)
            self._gpa_edit.setText(initial.gpa or "")
            self._show_gpa_check.setChecked(initial.show_gpa)
            self._start_date_edit.setText(initial.start_date)
            self._end_date_edit.setText(initial.end_date or "")
            self._expected_check.setChecked(initial.expected)
            self._tags_edit.setText(format_comma_list(initial.tags))

        form = QFormLayout()
        form.addRow("Okul*", self._school_edit)
        form.addRow("Bölüm*", self._field_edit)
        form.addRow("Derece*", self._degree_edit)
        form.addRow("Not ortalaması", self._gpa_edit)
        form.addRow(self._show_gpa_check)
        form.addRow("Başlangıç*", self._start_date_edit)
        form.addRow("Bitiş", self._end_date_edit)
        form.addRow(self._expected_check)
        form.addRow("Etiketler", self._tags_edit)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(_tag_hint_label(self))
        layout.addWidget(self._buttons)

    def _on_accept(self) -> None:
        for text, label in (
            (self._school_edit.text(), "Okul"),
            (self._field_edit.text(), "Bölüm"),
            (self._degree_edit.text(), "Derece"),
        ):
            if not text.strip():
                QMessageBox.warning(self, "Eksik alan", f"{label} boş olamaz.")
                return
        try:
            check_year_month(self._start_date_edit.text().strip())
            _valid_date_or_none(self._end_date_edit.text())
        except ValueError as error:
            QMessageBox.warning(self, "Geçersiz tarih", str(error))
            return
        self.accept()

    def values(self) -> dict:
        return {
            "school": self._school_edit.text().strip(),
            "field": self._field_edit.text().strip(),
            "degree": self._degree_edit.text().strip(),
            "gpa": self._gpa_edit.text().strip() or None,
            "show_gpa": self._show_gpa_check.isChecked(),
            "start_date": self._start_date_edit.text().strip(),
            "end_date": _valid_date_or_none(self._end_date_edit.text()),
            "expected": self._expected_check.isChecked(),
            "tags": parse_comma_list(self._tags_edit.text()),
        }


class ProjectDialog(QDialog):
    """Proje maddesi ekleme/düzenleme."""

    def __init__(self, initial: Optional[Project] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Proje düzenle" if initial else "Proje ekle")

        self._name_edit = QLineEdit(self)
        self._date_edit = QLineEdit(self)
        self._date_edit.setPlaceholderText("YYYY veya YYYY-MM (opsiyonel)")
        self._bullets_edit = QPlainTextEdit(self)
        self._bullets_edit.setPlaceholderText("Her satır bir madde")
        self._technologies_edit = QLineEdit(self)
        self._technologies_edit.setPlaceholderText("Virgülle ayırın (ör. Python, Pydantic)")
        self._tags_edit = QLineEdit(self)

        if initial is not None:
            self._name_edit.setText(initial.name)
            self._date_edit.setText(initial.date or "")
            self._bullets_edit.setPlainText(format_lines(initial.bullets))
            self._technologies_edit.setText(", ".join(initial.technologies))
            self._tags_edit.setText(format_comma_list(initial.tags))

        form = QFormLayout()
        form.addRow("Ad*", self._name_edit)
        form.addRow("Tarih", self._date_edit)
        form.addRow("Maddeler", self._bullets_edit)
        form.addRow("Teknolojiler", self._technologies_edit)
        form.addRow("Etiketler", self._tags_edit)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(_tag_hint_label(self))
        layout.addWidget(self._buttons)

    def _on_accept(self) -> None:
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Eksik alan", "Ad boş olamaz.")
            return
        try:
            _valid_date_or_none(self._date_edit.text())
        except ValueError as error:
            QMessageBox.warning(self, "Geçersiz tarih", str(error))
            return
        self.accept()

    def values(self) -> dict:
        return {
            "name": self._name_edit.text().strip(),
            "date": _valid_date_or_none(self._date_edit.text()),
            "bullets": parse_lines(self._bullets_edit.toPlainText()),
            "technologies": parse_comma_list(self._technologies_edit.text()),
            "tags": parse_comma_list(self._tags_edit.text()),
        }


class SkillDialog(QDialog):
    """Yetenek maddesi ekleme/düzenleme."""

    def __init__(self, initial: Optional[Skill] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Yetenek düzenle" if initial else "Yetenek ekle")

        self._name_edit = QLineEdit(self)
        self._level_edit = QLineEdit(self)
        self._level_edit.setPlaceholderText("ör. İleri, Orta")
        self._category_edit = QLineEdit(self)
        self._category_edit.setPlaceholderText("ör. Programlama Dilleri")
        self._tags_edit = QLineEdit(self)

        if initial is not None:
            self._name_edit.setText(initial.name)
            self._level_edit.setText(initial.level or "")
            self._category_edit.setText(initial.category or "")
            self._tags_edit.setText(format_comma_list(initial.tags))

        form = QFormLayout()
        form.addRow("Ad*", self._name_edit)
        form.addRow("Seviye", self._level_edit)
        form.addRow("Kategori", self._category_edit)
        form.addRow("Etiketler", self._tags_edit)

        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(_tag_hint_label(self))
        layout.addWidget(self._buttons)

    def _on_accept(self) -> None:
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Eksik alan", "Ad boş olamaz.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "name": self._name_edit.text().strip(),
            "level": self._level_edit.text().strip() or None,
            "category": self._category_edit.text().strip() or None,
            "tags": parse_comma_list(self._tags_edit.text()),
        }
