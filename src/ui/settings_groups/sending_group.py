"""\"Gönderim güvenliği\" grubu: test modu, gönderim modu, limitler, bekleme, mesai saati."""
from typing import Any, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QWidget,
)

from src.core.settings import SEND_MODE_LABELS_TR, SEND_MODES
from src.ui.settings_data import SEND_MODE_DESCRIPTIONS_TR, SettingsForm
from src.ui.settings_groups.common import (
    hhmm_from_qtime,
    hint_label,
    qtime_from_hhmm,
    select_index_by_data,
)
from src.ui.test_mode_data import (
    BACK_TO_TEST_BUTTON_TEXT,
    GO_LIVE_BUTTON_TEXT,
    TEST_MODE_OFF_LABEL,
    TEST_MODE_ON_LABEL,
)

TEST_MODE_ON_STYLE = "color: #15803d; font-weight: bold;"
TEST_MODE_OFF_STYLE = "color: #b91c1c; font-weight: bold;"


class SendingGroup(QGroupBox):
    #: Düğmeye basıldı; değer İSTENEN test modu durumu (`True` = "Test moduna dön", `False` =
    #: "Gerçek gönderime geç"). Grup ayarı kendisi DEĞİŞTİRMEZ: onay penceresi, KVKK denetimi ve
    #: yazma `SettingsTab`'ta (`src/ui/test_mode_dialog.py`); sonuç `set_test_mode()` ile döner.
    test_mode_change_requested = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Gönderim güvenliği", parent)
        form = QFormLayout()

        self._test_mode = True
        self.test_mode_label = QLabel(self)
        self.test_mode_label.setWordWrap(True)
        self.test_mode_button = QPushButton(self)
        self.test_mode_button.setAutoDefault(False)
        self.test_mode_button.clicked.connect(self._on_test_mode_button_clicked)
        form.addRow(self.test_mode_label)
        form.addRow(self.test_mode_button)
        self._refresh_test_mode_widgets()

        self.send_mode_combo = QComboBox(self)
        for mode in SEND_MODES:
            self.send_mode_combo.addItem(SEND_MODE_LABELS_TR[mode], mode)
        self.send_mode_combo.currentIndexChanged.connect(self._update_send_mode_description)
        form.addRow("Gönderim modu", self.send_mode_combo)

        self.send_mode_desc_label = hint_label("", self)
        form.addRow(self.send_mode_desc_label)

        self.daily_limit_spin = QSpinBox(self)
        self.daily_limit_spin.setRange(1, 999)
        form.addRow("Günlük limit", self.daily_limit_spin)

        self.hourly_limit_spin = QSpinBox(self)
        self.hourly_limit_spin.setRange(1, 999)
        form.addRow("Saatlik limit", self.hourly_limit_spin)

        self.min_delay_spin = QSpinBox(self)
        self.min_delay_spin.setRange(0, 3600)
        self.min_delay_spin.setSuffix(" sn")
        form.addRow("En az bekleme", self.min_delay_spin)

        self.max_delay_spin = QSpinBox(self)
        self.max_delay_spin.setRange(0, 3600)
        self.max_delay_spin.setSuffix(" sn")
        form.addRow("En çok bekleme", self.max_delay_spin)

        self.working_hours_check = QCheckBox("Sadece mesai saatinde gönder", self)
        form.addRow(self.working_hours_check)

        self.work_start_edit = QTimeEdit(self)
        self.work_start_edit.setDisplayFormat("HH:mm")
        form.addRow("Mesai başlangıcı", self.work_start_edit)

        self.work_end_edit = QTimeEdit(self)
        self.work_end_edit.setDisplayFormat("HH:mm")
        form.addRow("Mesai bitişi", self.work_end_edit)

        self.weekdays_check = QCheckBox("Sadece hafta içi gönder", self)
        form.addRow(self.weekdays_check)

        paused_hint = hint_label("Acil durdur üstteki durum şeridinden yönetilir.", self, wrap=False)
        form.addRow(paused_hint)

        self.setLayout(form)

    def set_test_mode(self, test_mode: bool) -> None:
        self._test_mode = test_mode
        self._refresh_test_mode_widgets()

    def _refresh_test_mode_widgets(self) -> None:
        if self._test_mode:
            self.test_mode_label.setText(TEST_MODE_ON_LABEL)
            self.test_mode_label.setStyleSheet(TEST_MODE_ON_STYLE)
            self.test_mode_button.setText(GO_LIVE_BUTTON_TEXT)
        else:
            self.test_mode_label.setText(TEST_MODE_OFF_LABEL)
            self.test_mode_label.setStyleSheet(TEST_MODE_OFF_STYLE)
            self.test_mode_button.setText(BACK_TO_TEST_BUTTON_TEXT)

    def _on_test_mode_button_clicked(self) -> None:
        self.test_mode_change_requested.emit(not self._test_mode)

    def _update_send_mode_description(self, *_args) -> None:
        mode = self.send_mode_combo.currentData()
        self.send_mode_desc_label.setText(SEND_MODE_DESCRIPTIONS_TR.get(mode, ""))

    def values(self) -> dict[str, Any]:
        return {
            "test_mode": self._test_mode,
            "send_mode": self.send_mode_combo.currentData(),
            "daily_limit": self.daily_limit_spin.value(),
            "hourly_limit": self.hourly_limit_spin.value(),
            "min_delay_seconds": self.min_delay_spin.value(),
            "max_delay_seconds": self.max_delay_spin.value(),
            "working_hours_only": self.working_hours_check.isChecked(),
            "work_start": hhmm_from_qtime(self.work_start_edit.time()),
            "work_end": hhmm_from_qtime(self.work_end_edit.time()),
            "weekdays_only": self.weekdays_check.isChecked(),
        }

    def set_values(self, form: SettingsForm) -> None:
        self.set_test_mode(form.test_mode)
        select_index_by_data(self.send_mode_combo, form.send_mode)
        self.daily_limit_spin.setValue(form.daily_limit)
        self.hourly_limit_spin.setValue(form.hourly_limit)
        self.min_delay_spin.setValue(form.min_delay_seconds)
        self.max_delay_spin.setValue(form.max_delay_seconds)
        self.working_hours_check.setChecked(form.working_hours_only)
        self.work_start_edit.setTime(qtime_from_hhmm(form.work_start))
        self.work_end_edit.setTime(qtime_from_hhmm(form.work_end))
        self.weekdays_check.setChecked(form.weekdays_only)
        self._update_send_mode_description()
