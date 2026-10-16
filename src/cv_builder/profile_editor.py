"""Profil dosyasını (`master_profile.json`) diske yazma (Faz 10 Adım 10.3d).

Yazmadan ÖNCE her zaman `MasterProfile.model_validate` ile TAM şema doğrulaması yapılmış
olmalı -- bu modül kendi başına doğrulama YAPMAZ, yalnızca zaten doğrulanmış bir
`MasterProfile` örneğini yedekleyip diske yazar (`src/ui/profile_data.py::collect_profile`
doğrulamayı yapan yer). CLAUDE.md kural 1/9: uydurma ya da bozuk bir profil diske asla
yazılmamalı.

Var olan dosya `settings_migration.py`deki `app.db.bak-<tarih>` ile AYNI kalıpla zaman
damgalı bir `.bak-<tarih>` kopyasına alınır -- üzerine yazmadan önce KOPYALANIR, taşınmaz.
"""
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.cv_builder.profile_loader import MasterProfile

logger = logging.getLogger(__name__)


def backup_profile(path: Path) -> Optional[Path]:
    """`path` varsa zaman damgalı bir yedeğini alır ve yedeğin yolunu döner; yoksa `None`."""
    if not path.exists():
        return None
    backup_path = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d%H%M%S}")
    shutil.copy2(path, backup_path)
    logger.info(f"Profil yedeklendi: {path} -> {backup_path}")
    return backup_path


def write_profile(path: Path, profile: MasterProfile) -> Optional[Path]:
    """Var olan dosyayı yedekler (`backup_profile`), sonra `profile`i diske yazar.

    `exclude_none=True`: boş (`None`) alanlar `data/master_profile.example.json` ile aynı
    üslupla dosyaya hiç yazılmaz (`"department": null` yerine anahtar hiç yok).
    """
    backup_path = backup_profile(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = profile.model_dump(mode="json", exclude_none=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info(f"Profil kaydedildi: {path}")
    return backup_path
