"""\"Diğer\" meslek: listede olmayan meslek için kullanıcının kendi adı ve arama kelimeleri (PLAN.md 10.2g/10.5c).

`KEY_PROFESSION == CUSTOM_PROFESSION_ID` ise meslek paketi dosyadan DEĞİL, iki ayardan (ad + virgülle ayrılmış
arama kelimeleri) çalışma anında bellekte üretilir; hiçbir şey dosyaya yazılmaz. Üretilen paket mevcut
tüketicilere (kaynak seçici, Google Places, OpenStreetMap, mail şablonu) normal bir paket gibi görünür ve
istenen davranışı kendiliğinden verir:
- `technical=False` → teknokent kullanılmaz;
- `osm_tags=None` → OpenStreetMap atlanır (kaynak seçici etiketsiz paketi seçmez);
- `search_queries_tr` = kullanıcının kelimeleri → Google Places aramaları;
- `default_mail_templates={}` → önerilen mail şablonu genel (`presets/mail_templates/`) şablondan gelir;
- `name_tr` = kullanıcının yazdığı ad → `{meslek}` yer tutucusu.

Dosya paketlerindeki "en az 3 arama kelimesi / 3 firma türü" şartı kullanıcı girdisine uygulanmaz (en az 1,
en çok `MAX_KEYWORDS` kelime; Google Places maliyeti kelime × ilçe sayısıyla büyür), bu yüzden paket
`model_construct` ile kurulur. CLAUDE.md kural 1: kelimeleri kullanıcı yazar, hiçbir metin üretilmez.

Tüketiciler `load_profession_presets().get(...)` yerine `resolve_profession(session)` kullanır: tek çözümleyici.
"""
import logging
import re
from typing import Mapping, Optional, Sequence

from sqlmodel import Session

from src.core.professions import CUSTOM_PROFESSION_ID, ProfessionPreset, load_profession_presets
from src.core.settings import (
    KEY_CUSTOM_PROFESSION_KEYWORDS,
    KEY_CUSTOM_PROFESSION_NAME,
    KEY_PROFESSION,
    get_setting,
)

logger = logging.getLogger(__name__)

#: Rezerve meslek kimliği `core/professions.CUSTOM_PROFESSION_ID` ("diger"): bir paket dosyası bu adı
#: TAŞIYAMAZ (yükleyici atlar), çözümleyici de önce bunu kontrol eder.
CUSTOM_PROFESSION_LABEL_TR = "Diğer (kendi mesleğimi yazacağım)"

MAX_NAME_LENGTH = 60
MAX_KEYWORD_LENGTH = 60
MAX_KEYWORDS = 10

_SPLIT_PATTERN = re.compile(r"[,\n]")


def parse_keywords(text: str) -> list[str]:
    """Virgül (ya da satır sonu) ile ayrılmış metni kelime listesine çevirir: kırpar, boşları ve (büyük/küçük
    harf farkı gözetmeden) yinelenenleri atar, sırayı korur."""
    seen: set[str] = set()
    keywords: list[str] = []
    for part in _SPLIT_PATTERN.split(text or ""):
        keyword = " ".join(part.split())
        folded = keyword.casefold()
        if keyword and folded not in seen:
            seen.add(folded)
            keywords.append(keyword)
    return keywords


def validate_custom_profession(name: Optional[str], keywords: Sequence[str]) -> list[str]:
    """Türkçe hata listesi (boş = geçerli)."""
    errors: list[str] = []
    cleaned_name = (name or "").strip()
    if not cleaned_name:
        errors.append("Meslek adını yazın.")
    elif len(cleaned_name) > MAX_NAME_LENGTH:
        errors.append(f"Meslek adı en çok {MAX_NAME_LENGTH} karakter olabilir.")
    elif "{" in cleaned_name or "}" in cleaned_name:
        errors.append("Meslek adında { veya } kullanılamaz.")

    if not keywords:
        errors.append("En az bir arama kelimesi yazın (virgülle ayırın, ör. diş kliniği, ortodonti).")
    elif len(keywords) > MAX_KEYWORDS:
        errors.append(f"En çok {MAX_KEYWORDS} arama kelimesi yazabilirsiniz.")
    elif any(len(keyword) > MAX_KEYWORD_LENGTH for keyword in keywords):
        errors.append(f"Her arama kelimesi en çok {MAX_KEYWORD_LENGTH} karakter olabilir.")
    return errors


def build_custom_preset(name: str, keywords: Sequence[str]) -> ProfessionPreset:
    """Kullanıcının adı + kelimelerinden bellekte meslek paketi (bkz. modül docstring'i). Çağıran önce
    `validate_custom_profession` ile doğrulamalıdır."""
    return ProfessionPreset.model_construct(
        id=CUSTOM_PROFESSION_ID,
        name_tr=name.strip(),
        technical=False,
        search_queries_tr=list(keywords),
        company_types=[],
        default_mail_templates={},
        notes=None,
        osm_tags=None,
    )


def presets_with_custom(
    presets: Mapping[str, ProfessionPreset], name: Optional[str], keywords: Sequence[str]
) -> dict[str, ProfessionPreset]:
    """`presets`in kopyası; ad/kelimeler geçerliyse `diger` girişi de eklenir (arayüz grupları kendi
    paket sözlüklerini bununla tazeler)."""
    result = dict(presets)
    if not validate_custom_profession(name, keywords):
        result[CUSTOM_PROFESSION_ID] = build_custom_preset(name or "", keywords)
    return result


def load_custom_profession_fields(session: Session) -> tuple[Optional[str], list[str]]:
    return (
        get_setting(session, KEY_CUSTOM_PROFESSION_NAME),
        list(get_setting(session, KEY_CUSTOM_PROFESSION_KEYWORDS) or []),
    )


def resolve_profession(
    session: Session, presets: Optional[Mapping[str, ProfessionPreset]] = None
) -> Optional[ProfessionPreset]:
    """Ayarlardaki mesleği pakete çevirir; meslek seçilmemişse, bilinmiyorsa ya da `diger`'in ad/kelimeleri
    geçersizse `None` (çökmez; eski \"meslek seçilmemiş\" davranışı)."""
    profession_id = get_setting(session, KEY_PROFESSION)
    if not profession_id:
        return None
    if profession_id == CUSTOM_PROFESSION_ID:
        name, keywords = load_custom_profession_fields(session)
        errors = validate_custom_profession(name, keywords)
        if errors:
            logger.warning(f"\"Diğer\" meslek ayarı geçersiz, meslek seçilmemiş sayıldı: {errors}")
            return None
        return build_custom_preset(name or "", keywords)
    available = presets if presets is not None else load_profession_presets()
    return available.get(profession_id)
