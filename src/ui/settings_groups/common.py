"""Ayar gruplarının (ve ileride kurulum sihirbazı sayfalarının) ortak küçük parçaları.

Hiçbiri `SettingsTab`'a bağlı değil: bir grup tek başına başka bir pencereye de konabilsin diye
ortak olan her şey burada, grup dosyaları birbirini içe aktarmıyor.
"""
from datetime import time
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from src.core.cities import CityPreset
from src.core.custom_profession import CUSTOM_PROFESSION_ID, CUSTOM_PROFESSION_LABEL_TR
from src.core.professions import ProfessionPreset
from src.core.timeutil import parse_hhmm
from src.cv_builder.mail_template import TARGETS
from src.ui.settings_data import APPLICATION_TARGET_LABELS_TR

HINT_STYLE = "color: gray;"


def hint_label(text: str, parent: QWidget, *, wrap: bool = True) -> QLabel:
    """Gri açıklama satırı. `wrap=False` yalnızca kısa, tek satırlık notlar için."""
    label = QLabel(text, parent)
    if wrap:
        label.setWordWrap(True)
    label.setStyleSheet(HINT_STYLE)
    return label


def qtime_from_hhmm(text: str) -> QTime:
    """`"09:00"` gibi bir metni `QTime`'a çevirir; bozuksa 09:00'a düşer (widget hiçbir zaman
    geçersiz bir saat göstermemeli)."""
    try:
        parsed = parse_hhmm(text)
    except ValueError:
        parsed = time(9, 0)
    return QTime(parsed.hour, parsed.minute)


def hhmm_from_qtime(value: QTime) -> str:
    return f"{value.hour():02d}:{value.minute():02d}"


def add_key_row(
    form: QFormLayout,
    parent: QWidget,
    label: str,
    *,
    on_save: Callable[[], None],
    on_delete: Callable[[], None],
    save_text: str = "Anahtar kaydet",
    delete_text: str = "Anahtarı sil",
) -> QLabel:
    """Bir "durum + kaydet + sil" satırı ekler ve durum etiketini döner (çağıran saklar)."""
    status_label = QLabel(parent)
    save_button = QPushButton(save_text, parent)
    delete_button = QPushButton(delete_text, parent)
    save_button.clicked.connect(on_save)
    delete_button.clicked.connect(on_delete)

    row = QHBoxLayout()
    row.addWidget(status_label)
    row.addWidget(save_button)
    row.addWidget(delete_button)
    row.addStretch(1)
    row_widget = QWidget(parent)
    row_widget.setLayout(row)

    form.addRow(label, row_widget)
    return status_label


def select_index_by_data(combo, value: Optional[object]) -> None:
    """`combo.findData(value)`; bulunamazsa ilk satır (`max(..., 0)` kalıbı her yerde aynıydı)."""
    combo.setCurrentIndex(max(combo.findData(value), 0))


def populate_city_combo(combo: QComboBox, cities: dict[str, CityPreset]) -> None:
    """"(seçilmedi)" + illeri ada göre sıralı ekler; ayar değeri il plaka kodudur ("06").
    Ayarlar'ın "Firma kaynakları" grubu ve kurulum sihirbazı aynı listeyi kullanır."""
    combo.addItem("(seçilmedi)", None)
    for city_id, city in sorted(cities.items(), key=lambda item: item[1].name_tr):
        combo.addItem(f"{city.name_tr} ({city_id})", city_id)


def populate_target_combo(combo: QComboBox) -> None:
    """"(seçilmedi)" + dört hedef (iş, staj, gönüllü staj, yarı zamanlı); ayar değeri `TARGETS`'tan biri.
    Ayarlar'ın "Mail şablonu" grubu ve kurulum sihirbazı aynı listeyi kullanır."""
    combo.addItem("(seçilmedi)", None)
    for target in TARGETS:
        combo.addItem(APPLICATION_TARGET_LABELS_TR[target], target)


def populate_profession_combo(
    combo: QComboBox, professions: dict[str, ProfessionPreset]
) -> None:
    """"(seçilmedi)" + meslek paketlerini ada göre sıralı + en sonda "Diğer"; ayar değeri paket kimliğidir
    (Diğer için `CUSTOM_PROFESSION_ID`, bkz. `src/core/custom_profession.py`)."""
    combo.addItem("(seçilmedi)", None)
    for profession_id, preset in sorted(professions.items(), key=lambda item: item[1].name_tr):
        combo.addItem(preset.name_tr, profession_id)
    combo.addItem(CUSTOM_PROFESSION_LABEL_TR, CUSTOM_PROFESSION_ID)


def make_searchable(combo: QComboBox) -> None:
    """Combo'ya yazarak arama ekler (81 il gibi uzun listeler için). Yazılan metin listedeki bir
    satırla eşleşmedikçe seçim DEĞİŞMEZ (`NoInsert`), yani `currentData()` hep listedeki bir değerdir."""
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    completer = combo.completer()
    completer.setFilterMode(Qt.MatchContains)
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    completer.setCompletionMode(QCompleter.PopupCompletion)
