"""\"Firma kaynakları\" grubu: şehir + OpenStreetMap; hangi kaynakların kullanılacağını gösterir.

Özet satırı grubun kendi alanlarından (şehir, OSM) VE başka yerlerden (meslek, Google Places
düğmesi, anahtar kayıtlı mı) hesaplanır; ikincisini grup bilmez, barındıran pencere `context`
çağrısıyla verir. Başka bir alan değişince barındıran pencere `update_hint()`i çağırır.
"""
from dataclasses import dataclass
from typing import Any, Callable, Optional

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGroupBox, QWidget

from src.company_finder.source_selector import SourceSettings, select_sources
from src.core.cities import CityPreset
from src.core.custom_profession import CUSTOM_PROFESSION_ID
from src.core.professions import ProfessionPreset
from src.ui.settings_data import SOURCE_LABELS_TR, SettingsForm
from src.ui.settings_groups.common import hint_label, populate_city_combo

#: OpenStreetMap verisi ODbL lisanslı; arayüzde atıf zorunlu (PLAN.md Adım 10.6 notları).
OSM_ATTRIBUTION_TR = "Harita verisi © OpenStreetMap katkıcıları (ODbL)."


@dataclass(frozen=True)
class SourcesContext:
    """Kaynak özetinin grubun DIŞINDAN okuduğu değerler."""

    profession_id: Optional[str] = None
    use_google_places: bool = False
    has_places_key: bool = False
    #: `profession_id == "diger"` iken kullanıcının ad/kelimelerinden üretilmiş paket (geçersizse `None`).
    custom_profession: Optional[ProfessionPreset] = None


class SourcesGroup(QGroupBox):
    def __init__(
        self,
        cities: dict[str, CityPreset],
        profession_presets: dict[str, ProfessionPreset],
        context: Callable[[], SourcesContext] = SourcesContext,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__("Firma kaynakları", parent)
        self._cities = cities
        self._profession_presets = profession_presets
        self._context = context
        form = QFormLayout()

        self.city_combo = QComboBox(self)
        populate_city_combo(self.city_combo, self._cities)
        self.city_combo.currentIndexChanged.connect(self.update_hint)
        form.addRow("Şehir", self.city_combo)

        self.use_osm_check = QCheckBox(
            "OpenStreetMap'ten firma bul (anahtar gerekmez, ücretsiz)", self
        )
        self.use_osm_check.stateChanged.connect(self.update_hint)
        form.addRow(self.use_osm_check)

        self.sources_hint_label = hint_label("", self)
        form.addRow(self.sources_hint_label)

        attribution = hint_label(OSM_ATTRIBUTION_TR, self, wrap=False)
        form.addRow(attribution)

        self.setLayout(form)

    def update_hint(self, *_args) -> None:
        """Ekrandaki şehir/OSM/meslek seçimiyle kullanılacak kaynakları gösterir (yalnızca bilgi;
        Otopilot'a bağlantı Adım 10.6d'de)."""
        context = self._context()
        if context.profession_id == CUSTOM_PROFESSION_ID:
            profession = context.custom_profession
        else:
            profession = (
                self._profession_presets.get(context.profession_id) if context.profession_id else None
            )
        sources = select_sources(
            profession,
            self.city_combo.currentData(),
            SourceSettings(
                use_osm=self.use_osm_check.isChecked(),
                use_google_places=context.use_google_places,
                has_places_key=context.has_places_key,
            ),
            self._cities,
        )
        names = ", ".join(SOURCE_LABELS_TR[source] for source in sources)
        self.sources_hint_label.setText(
            f"Bu seçimle kullanılacak kaynaklar: {names}."
            if sources
            else "Bu seçimle otomatik kaynak yok (meslek ve şehir seçin). CSV ile içe aktarma her zaman açıktır."
        )

    def set_city(self, city_id: Optional[str]) -> None:
        """Kayıtlı il kodunu combo'da seçer. Kod listede yoksa (il dosyası okunamadı) sessizce
        "(seçilmedi)"ye düşüp bir sonraki Kaydet'te değeri SİLMEMEK için geçici bir satır eklenir."""
        index = self.city_combo.findData(city_id)
        if index < 0 and city_id is not None:
            self.city_combo.addItem(f"{city_id} (il listesi okunamadı)", city_id)
            index = self.city_combo.count() - 1
        self.city_combo.setCurrentIndex(max(index, 0))

    def values(self) -> dict[str, Any]:
        return {
            "autopilot_city": self.city_combo.currentData(),
            "use_osm": self.use_osm_check.isChecked(),
        }

    def set_values(self, form: SettingsForm) -> None:
        self.set_city(form.autopilot_city)
        self.use_osm_check.setChecked(form.use_osm)
