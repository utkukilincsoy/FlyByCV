"""Keşif sonuç özeti — PLAN.md Faz 10 Adım 10.6h ("dürüst sonuç mesajı").

Otopilot keşif kaynakları bitince kullanıcıya NE kadar firma bulunduğunu, hangi kaynaktan
geldiğini ve (azsa) ne yapabileceğini söyler. Saf Python: Qt/ağ/veritabanı yok.

"Bulunan" = bu çalıştırmada veritabanına YENİ eklenen firma. Zaten kayıtlı olan ya da eklenmeye
uygun olmayan (siteleri/e-postası olmayan, engellenen) adaylar ayrı sayılır; ikisi karıştırılıp
şişirilmez.
"""
from dataclasses import dataclass, field
from typing import Optional

#: Otomatik kaynaklardan toplam yeni firma bu sayının altındaysa "az buldum" önerisi eklenir
#: (özellikle OSM'de küçük illerde kayıt azdır — PLAN.md 10.6 riski).
LOW_RESULT_THRESHOLD = 20

#: `Company.source` değeri -> özet metnindeki ad.
SOURCE_NAMES_TR = {
    "teknokent": "Teknokent rehberleri",
    "osm": "OpenStreetMap",
    "google": "Google Places",
}


@dataclass
class SourceTally:
    added: int = 0
    skipped: int = 0


@dataclass
class DiscoveryTally:
    """Bir Otopilot çalıştırması boyunca kaynak türü (`Company.source`) başına sayaçlar."""

    city_name: Optional[str] = None
    by_source: dict[str, SourceTally] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    announced: bool = False

    def record(self, company_source: str, *, added: int, skipped: int) -> None:
        tally = self.by_source.setdefault(company_source, SourceTally())
        tally.added += added
        tally.skipped += skipped

    def record_failure(self, company_source: str, reason: str) -> None:
        self.by_source.setdefault(company_source, SourceTally())
        if reason not in self.failures:
            self.failures.append(reason)

    @property
    def total_added(self) -> int:
        return sum(t.added for t in self.by_source.values())

    @property
    def total_skipped(self) -> int:
        return sum(t.skipped for t in self.by_source.values())

    @property
    def has_activity(self) -> bool:
        return bool(self.by_source)


def format_discovery_summary(tally: DiscoveryTally) -> str:
    """Keşif bitince gösterilen Türkçe özet ("Ankara ilinde 37 yeni firma bulundu ...")."""
    where = f"{tally.city_name} ilinde " if tally.city_name else ""
    total = tally.total_added
    text = f"Keşif bitti: {where}{total} yeni firma bulundu"
    parts = [
        f"{SOURCE_NAMES_TR.get(source, source)}: {counts.added}"
        for source, counts in tally.by_source.items()
    ]
    if parts:
        text += f" ({', '.join(parts)})"
    text += "."
    if tally.total_skipped:
        text += f" {tally.total_skipped} aday zaten kayıtlıydı ya da eklenmeye uygun değildi."
    if tally.failures:
        text += (
            " Bazı aramalar tamamlanamadı, bir sonraki açılışta yeniden denenecek: "
            + "; ".join(tally.failures)
            + "."
        )
    if total < LOW_RESULT_THRESHOLD:
        text += " " + _low_result_advice(tally)
    return text


def _low_result_advice(tally: DiscoveryTally) -> str:
    if "google" in tally.by_source:
        return (
            "Az firma bulundu; daha fazlası için CSV ile kendi firma listenizi içe aktarabilirsiniz "
            "(Şirketler sekmesi)."
        )
    return (
        "Az firma bulundu: OpenStreetMap'te küçük illerdeki firmaların kaydı azdır. Daha fazlası için "
        "Ayarlar'dan Google Places anahtarı ekleyebilir (ücretli olabilir) ya da CSV ile kendi firma "
        "listenizi içe aktarabilirsiniz (Şirketler sekmesi)."
    )
