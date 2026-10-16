"""Ortak keşif kaynağı arayüzü + toplu içe aktarma — PLAN.md Faz 10 Adım 10.6d.

Teknokent (9.2) bugün tek kaynak; OpenStreetMap (10.6e) ve Google Places (10.6f) da aynı
arayüzle Otopilot'a bağlanacak. Bu modül veritabanına yalnızca `import_leads()` ile dokunur, o da
`csv_import.add_company()`'ye devreder (alan adı çözümleme, tekilleştirme, rol adresi tespiti
BURADA TEKRAR YAZILMAZ — CLAUDE.md kural 4/10).
"""
import logging
from typing import Callable, Iterable, Iterator, Optional, Protocol

from sqlmodel import Session

from src.company_finder.csv_import import (
    AddedRow,
    ImportReport,
    RowError,
    RowNote,
    add_company,
)

logger = logging.getLogger(__name__)


class Fetcher(Protocol):
    def __call__(self, url: str) -> str: ...


class DiscoveryLead(Protocol):
    """Bir kaynaktan çıkan aday. `website`/`email` biçim olarak DOĞRULANMAMIŞTIR; doğrulama
    `add_company()`'de yapılır."""

    @property
    def name(self) -> str: ...

    @property
    def website(self) -> Optional[str]: ...

    @property
    def email(self) -> Optional[str]: ...

    @property
    def city(self) -> str: ...


class DiscoverySource(Protocol):
    """Otopilot'un keşif döngüsünün tükettiği kaynak.

    - `name`: kullanıcıya gösterilen Türkçe ad (log/olay metinleri).
    - `state_key`: `Setting.discovery_state` içindeki anahtar (ör. `"teknokent:odtu"`). Biçim
      kalıcıdır — değişirse bitmiş kaynaklar yeniden taranırdı.
    - `company_source`: eklenen `Company.source` değeri (ör. `"teknokent"`).
    - `discover(fetcher=..., sleeper=...)`: adayları tek tek üreten generator; veritabanına
      DOKUNMAZ.
    """

    @property
    def name(self) -> str: ...

    @property
    def state_key(self) -> str: ...

    @property
    def company_source(self) -> str: ...

    def discover(
        self, *, fetcher: Optional[Fetcher] = None, sleeper: Callable[[float], None]
    ) -> Iterator[DiscoveryLead]: ...


def import_leads(
    session: Session,
    leads: Iterable[DiscoveryLead],
    *,
    company_source: str,
    label: str = "",
) -> ImportReport:
    """Toplanan adayları veritabanına ekler; hata/uyarıları `ImportReport`'a toplar.

    `company_source` eklenen her `Company.source` değeridir; `label` yalnızca log içindir.
    COMMIT ETMEZ ("commit çağırana aittir", `src/core/db.py`).
    """
    report = ImportReport()
    for lead in leads:
        report.total_rows += 1
        try:
            company = add_company(
                session,
                name=lead.name,
                website=lead.website,
                email=lead.email,
                city=lead.city,
                source=company_source,
            )
        except RowError as exc:
            report.skipped.append(RowNote(row=report.total_rows, name=lead.name, reason=str(exc)))
            continue
        except Exception:
            logger.exception(f"{label}: {lead.name!r} eklenirken beklenmeyen hata")
            report.skipped.append(
                RowNote(row=report.total_rows, name=lead.name, reason="beklenmeyen hata")
            )
            continue
        report.added.append(
            AddedRow(row=report.total_rows, name=company.name, domain=company.domain)
        )
    return report
