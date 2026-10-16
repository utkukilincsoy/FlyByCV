"""Meslek paketleri: `presets/professions/*.json` — PLAN.md Adım 10.5.

Amaç: yeni bir meslek eklemek KOD yazmayı gerektirmesin, yalnızca şemaya uyan bir JSON
dosyası eklemek yetsin (bkz. `presets/README.md`). Bu modül yalnızca YÜKLER ve DOĞRULAR;
hiçbir paket henüz `keyword_provider`/LLM istemi/`tag_matcher`'a BAĞLANMADI (bu, ayrı bir
adımın konusu) — bugünkü yazılım/bilgisayar mühendisliği davranışı (`core/tags.py`,
`llm/keyword_provider.py`, `core/settings_migration.LEGACY_AUTOPILOT_QUERIES`) BU MODÜLDEN
etkilenmeden aynen çalışmaya devam eder.

Bozuk bir paket dosyası (geçersiz JSON, şemaya uymayan alan, dosya adıyla eşleşmeyen `id`,
başka bir paketle çakışan `id`) uygulamayı ÇÖKERTMEZ: o dosya atlanır, `logger.warning` ile
kaydedilir, diğer paketler yüklenmeye devam eder.
"""
import json
import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ValidationError, field_validator, model_validator

from src.core.paths import professions_dir
from src.cv_builder.mail_template import TARGETS, find_unknown_placeholders

logger = logging.getLogger(__name__)

#: Bir paketin sunması GEREKEN hedefler ("hedefe göre varsayılan mail şablonu önerisi") —
#: `src/cv_builder/mail_template.TARGETS` TEK kaynak (Ayarlar'daki "Hedef" seçimiyle aynı 4
#: değer, PLAN.md Adım 10.4).
REQUIRED_TARGETS: tuple[str, ...] = TARGETS

#: "Diğer" meslek seçeneğine ayrılmış kimlik (bkz. `core/custom_profession.py`); bir paket dosyası bunu
#: taşıyamaz, yüklenirken atlanır.
CUSTOM_PROFESSION_ID = "diger"

_ID_PATTERN_MSG ="id yalnızca küçük harf, rakam ve alt çizgi içerebilir (ör. 'ic_mimarlik')"

#: PLAN.md Adım 10.6b: `osm_tags` yalnızca OSM wiki'de GERÇEKTEN belgeli `key=value` çiftlerini
#: içerebilir (uydurma etiket yok) -- bkz. https://wiki.openstreetmap.org/wiki/Key:office,
#: Key:tourism, Key:shop ve Key:craft (her değerin kendi `Tag:` sayfası wiki'de ayrıca doğrulandı,
#: 30.09.2026). Overpass sorgusu 10.6e'de gelecek; `osm_tags` bugün yalnızca şema + veri.
_KNOWN_OSM_TAGS: frozenset[str] = frozenset(
    {
        "office=it",
        "office=lawyer",
        "office=accountant",
        "office=financial_advisor",
        "office=employment_agency",
        "office=logistics",
        "office=advertising_agency",
        "office=graphic_design",
        "office=engineer",
        "office=construction_company",
        "office=architect",
        "office=travel_agent",
        "tourism=hotel",
        "shop=interior_decoration",
        "craft=gardener",
    }
)


class MailTemplateSuggestion(BaseModel):
    """Kullanıcının Ayarlar'da düzenleyebileceği bir ÖNERİ — otomatik gönderilmez, hiçbir
    şirkete özel bilgi UYDURMAZ. Yalnızca `mail_template.PLACEHOLDERS` içindeki yer
    tutucuları kullanabilir (`{hedef}`/`{meslek}` gibi henüz bağlanmamış yer tutucular
    burada da kullanılamaz — kullanılırsa paket reddedilir, bkz. `_check_placeholders`)."""

    subject: str
    body: str

    @field_validator("subject", "body")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("boş olamaz")
        return value


class ProfessionPreset(BaseModel):
    id: str
    name_tr: str
    technical: bool
    search_queries_tr: list[str]
    company_types: list[str]
    default_mail_templates: dict[str, MailTemplateSuggestion]
    notes: Optional[str] = None
    osm_tags: Optional[list[str]] = None

    @field_validator("osm_tags")
    @classmethod
    def osm_tags_known_and_nonempty(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        if not value:
            raise ValueError("osm_tags verilmişse boş liste olamaz (alanı hiç eklemeyin)")
        unknown = [tag for tag in value if tag not in _KNOWN_OSM_TAGS]
        if unknown:
            raise ValueError(
                f"osm_tags yalnızca OSM wiki'de belgeli etiketleri içerebilir, bilinmeyen: {unknown}"
            )
        if len(set(value)) != len(value):
            raise ValueError("osm_tags yinelenen etiket içeremez")
        return value

    @field_validator("id")
    @classmethod
    def id_format(cls, value: str) -> str:
        if not value or not all(c.islower() or c.isdigit() or c == "_" for c in value):
            raise ValueError(_ID_PATTERN_MSG)
        return value

    @field_validator("name_tr")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name_tr boş olamaz")
        return value

    @field_validator("search_queries_tr")
    @classmethod
    def queries_min_and_nonblank(cls, value: list[str]) -> list[str]:
        cleaned = [q.strip() for q in value]
        if len(cleaned) < 3:
            raise ValueError("search_queries_tr en az 3 arama kelimesi içermeli")
        if any(not q for q in cleaned):
            raise ValueError("search_queries_tr boş bir kelime içeremez")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("search_queries_tr yinelenen kelime içeremez")
        return cleaned

    @field_validator("company_types")
    @classmethod
    def company_types_min_and_nonblank(cls, value: list[str]) -> list[str]:
        cleaned = [c.strip() for c in value]
        if len(cleaned) < 3:
            raise ValueError("company_types en az 3 şirket türü içermeli")
        if any(not c for c in cleaned):
            raise ValueError("company_types boş bir tür içeremez")
        return cleaned

    @model_validator(mode="after")
    def check_targets_and_placeholders(self) -> "ProfessionPreset":
        missing = [t for t in REQUIRED_TARGETS if t not in self.default_mail_templates]
        if missing:
            raise ValueError(f"default_mail_templates eksik hedef(ler): {missing}")
        extra = [t for t in self.default_mail_templates if t not in REQUIRED_TARGETS]
        if extra:
            raise ValueError(f"default_mail_templates bilinmeyen hedef(ler): {extra}")
        for target, tmpl in self.default_mail_templates.items():
            unknown = find_unknown_placeholders(tmpl.subject) + find_unknown_placeholders(tmpl.body)
            if unknown:
                raise ValueError(
                    f"default_mail_templates.{target}: desteklenmeyen yer tutucu {unknown}"
                )
        return self


def _load_one(path: Path) -> Optional[ProfessionPreset]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Meslek paketi okunamadı, atlandı: {path.name}: {e}")
        return None
    try:
        preset = ProfessionPreset.model_validate(raw)
    except ValidationError as e:
        logger.warning(f"Meslek paketi şemaya uymuyor, atlandı: {path.name}: {e}")
        return None
    if preset.id == CUSTOM_PROFESSION_ID:
        logger.warning(
            f"Meslek paketi atlandı: '{CUSTOM_PROFESSION_ID}' kimliği \"Diğer\" seçeneğine ayrılmıştır ({path.name})"
        )
        return None
    if preset.id != path.stem:
        logger.warning(
            f"Meslek paketi atlandı: dosya adı '{path.stem}' ile içindeki id "
            f"'{preset.id}' eşleşmiyor ({path.name})"
        )
        return None
    return preset


def load_profession_presets(directory: Optional[Path] = None) -> dict[str, ProfessionPreset]:
    """Tüm geçerli meslek paketlerini `id -> ProfessionPreset` olarak döner.

    `directory` verilmezse `paths.professions_dir()` kullanılır (testler kendi geçici
    klasörünü verebilir). Klasör yoksa veya boşsa (paketleme hatası, silinmiş klasör vb.)
    çökmek yerine uyarı loglayıp boş sözlük döner."""
    base = directory if directory is not None else professions_dir()
    if not base.is_dir():
        logger.warning(f"Meslek paketleri klasörü bulunamadı: {base}")
        return {}

    presets: dict[str, ProfessionPreset] = {}
    for path in sorted(base.glob("*.json")):
        preset = _load_one(path)
        if preset is None:
            continue
        presets[preset.id] = preset

    logger.info(f"{len(presets)} meslek paketi yüklendi: {sorted(presets)}")
    return presets
