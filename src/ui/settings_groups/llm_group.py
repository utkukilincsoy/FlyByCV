"""\"Yapay zekâ\" grubu: sağlayıcı, model adları, sağlayıcı başına API anahtarı, bağlantı testi.

Anahtar kaydet/sil grubun içinde; ağa çıkan "Bağlantıyı test et" yalnızca `test_requested` yayar
(işi barındıran pencere arka planda çalıştırır, CLAUDE.md kural 8).
"""
from functools import partial
from typing import Any, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from src.core.secrets import get_api_key
from src.llm.providers import API_KEY_PROVIDERS, LLM_PROVIDER_NAMES, PROVIDER_NONE
from src.ui.jobs import LlmTestJobResult
from src.ui.settings_data import SettingsForm, format_secret_status
from src.ui.settings_groups.common import add_key_row, hint_label, select_index_by_data
from src.ui.settings_groups.secret_prompts import delete_api_key_with_notice, prompt_save_api_key

#: `llm_provider` combo'sunda gösterilecek kısa Türkçe etiketler; ayar değeri (`currentData()`)
#: her zaman İngilizce ("none"/"claude"/"gemini"/"groq") kalır.
LLM_PROVIDER_LABELS_TR = {
    "none": "Yok (anahtar kelime, ücretsiz)",
    "claude": "Claude (Anthropic)",
    "gemini": "Gemini (Google)",
    "groq": "Groq",
}


class LlmGroup(QGroupBox):
    test_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Yapay zekâ", parent)
        #: Sağlayıcı adı -> "kayıtlı ✓ / kayıtlı değil" etiketi.
        self.key_status_labels: dict[str, QLabel] = {}
        form = QFormLayout()

        self.llm_provider_combo = QComboBox(self)
        for name in LLM_PROVIDER_NAMES:
            self.llm_provider_combo.addItem(LLM_PROVIDER_LABELS_TR.get(name, name), name)
        form.addRow("Sağlayıcı", self.llm_provider_combo)

        self.llm_model_claude_edit = QLineEdit(self)
        form.addRow("Claude model adı", self.llm_model_claude_edit)

        self.llm_model_gemini_edit = QLineEdit(self)
        form.addRow("Gemini model adı", self.llm_model_gemini_edit)

        self.llm_model_groq_edit = QLineEdit(self)
        form.addRow("Groq model adı", self.llm_model_groq_edit)

        for provider in API_KEY_PROVIDERS:
            self.key_status_labels[provider] = add_key_row(
                form,
                self,
                f"{provider} API anahtarı",
                on_save=partial(self.save_api_key, provider),
                on_delete=partial(self.delete_api_key, provider),
            )

        self.llm_test_button = QPushButton("Bağlantıyı test et", self)
        self.llm_test_button.clicked.connect(lambda: self.test_requested.emit())
        form.addRow(self.llm_test_button)

        llm_test_hint = hint_label(
            "Gerçek bir ağ çağrısı yapar; çok küçüktür, ücretsiz kotanız varsa ücret alınmaz.",
            self,
        )
        form.addRow(llm_test_hint)

        self.setLayout(form)

    def refresh_key_status(self, provider: str) -> None:
        self.key_status_labels[provider].setText(format_secret_status(bool(get_api_key(provider))))

    def refresh_all_key_statuses(self) -> None:
        for provider in API_KEY_PROVIDERS:
            self.refresh_key_status(provider)

    def save_api_key(self, provider: str, title: Optional[str] = None) -> None:
        if prompt_save_api_key(self, provider, title):
            self.refresh_key_status(provider)

    def delete_api_key(self, provider: str, label: Optional[str] = None) -> None:
        delete_api_key_with_notice(
            self, provider, partial(self.refresh_key_status, provider), label
        )

    def values(self) -> dict[str, Any]:
        return {
            "llm_provider": self.llm_provider_combo.currentData(),
            "llm_model_claude": self.llm_model_claude_edit.text().strip(),
            "llm_model_gemini": self.llm_model_gemini_edit.text().strip(),
            "llm_model_groq": self.llm_model_groq_edit.text().strip(),
        }

    def set_values(self, form: SettingsForm) -> None:
        select_index_by_data(self.llm_provider_combo, form.llm_provider)
        self.llm_model_claude_edit.setText(form.llm_model_claude)
        self.llm_model_gemini_edit.setText(form.llm_model_gemini)
        self.llm_model_groq_edit.setText(form.llm_model_groq)


def show_llm_test_result(parent: QWidget, result: LlmTestJobResult) -> None:
    """\"Bağlantıyı test et\" işinin sonucunu kullanıcıya gösterir."""
    if result.provider == PROVIDER_NONE:
        QMessageBox.information(
            parent,
            "Bağlantı testi",
            "Sağlayıcı 'Yok' seçili: ağa hiç çıkılmadı, anahtar kelime sonucu denendi.\n\n"
            f"Etiketler: {', '.join(result.tags) or '(etiket bulunamadı)'}",
        )
        return
    if result.no_key:
        QMessageBox.warning(
            parent,
            "Anahtar kayıtlı değil",
            f"{result.provider} için kayıtlı bir API anahtarı yok. Önce anahtarı kaydedin.",
        )
        return
    if result.error:
        QMessageBox.warning(
            parent, "Bağlantı testi başarısız", f"{result.provider}: {result.error}"
        )
        return
    tags_text = ", ".join(result.tags) if result.tags else "(etiket bulunamadı)"
    QMessageBox.information(
        parent,
        "Bağlantı testi başarılı",
        f"Sağlayıcı: {result.provider}\nÖrnek etiketler: {tags_text}",
    )
