"""Genel (mesleğe özel olmayan) hazır mail şablonları — `presets/mail_templates/*.json`.

PLAN.md Adım 10.4c: hedef x dil (iş/staj/gönüllü staj/yarı zamanlı x TR/EN = 8 şablon).
Meslek paketlerinin (`src/core/professions.py`) `default_mail_templates`'i yalnızca TR'dir
(paketlerde `name_en` bilinçli olarak yok, bkz. `presets/README.md`); kullanıcı Ayarlar'da dil
olarak İngilizce'yi seçtiğinde ya da henüz bir meslek seçmediğinde (`KEY_PROFESSION` boş),
`src/ui/settings_data.py::suggested_mail_template` bu genel şablonlardan birine düşer. Meslek
seçiliyse VE dil TR ise, meslek paketinin kendi (daha isabetli) şablonuna öncelik verilir.

Her dosya `<hedef>_<dil>.json` adında ve `{"subject": "...", "body": "..."}` biçiminde;
yalnızca `src/cv_builder/mail_template.PLACEHOLDERS` içindeki yer tutucular kullanılabilir.
Bozuk/eksik bir dosya uygulamayı ÇÖKERTMEZ: o giriş atlanır, `logger.warning` ile kaydedilir.
"""
import json
import logging
from pathlib import Path
from typing import Optional

from src.core.paths import mail_template_presets_dir
from src.core.professions import MailTemplateSuggestion
from src.cv_builder.mail_template import LANGUAGES, TARGETS, find_unknown_placeholders

logger = logging.getLogger(__name__)


def _load_one(path: Path) -> Optional[MailTemplateSuggestion]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Genel mail şablonu okunamadı, atlandı: {path.name}: {e}")
        return None
    try:
        suggestion = MailTemplateSuggestion.model_validate(raw)
    except Exception as e:
        logger.warning(f"Genel mail şablonu şemaya uymuyor, atlandı: {path.name}: {e}")
        return None
    unknown = find_unknown_placeholders(suggestion.subject) + find_unknown_placeholders(suggestion.body)
    if unknown:
        logger.warning(f"Genel mail şablonu bilinmeyen yer tutucu içeriyor, atlandı: {path.name}: {unknown}")
        return None
    return suggestion


def load_generic_mail_templates(
    directory: Optional[Path] = None,
) -> dict[tuple[str, str], MailTemplateSuggestion]:
    """`(hedef, dil) -> MailTemplateSuggestion` döner; eksik/bozuk girişler sözlükte YOK olur."""
    base = directory if directory is not None else mail_template_presets_dir()
    if not base.is_dir():
        logger.warning(f"Genel mail şablonları klasörü bulunamadı: {base}")
        return {}

    result: dict[tuple[str, str], MailTemplateSuggestion] = {}
    for target in TARGETS:
        for language in LANGUAGES:
            path = base / f"{target}_{language}.json"
            if not path.is_file():
                continue
            suggestion = _load_one(path)
            if suggestion is not None:
                result[(target, language)] = suggestion

    logger.info(f"{len(result)} genel mail şablonu yüklendi")
    return result
