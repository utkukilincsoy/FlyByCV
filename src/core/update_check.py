"""Yeni sürüm var mı? kontrolü — çekirdek (Faz 10 Adım 10.9c). PySide6 İÇERMEZ.

GitHub Releases'in `releases/latest` uç noktasına TEK bir GET atar (`src/core/version.py::
GITHUB_REPO`; `None` iken ağa HİÇ çıkılmaz). Kurallar:

* 5 sn zaman aşımı; ağ/HTTP/JSON dahil HER hatada sessizce `None` (log yalnızca debug) — kontrol
  uygulamayı asla yavaşlatmamalı ya da kullanıcıya hata göstermemeli.
* Otomatik kontrol günde en fazla BİR kez (son denemeden 24 saat dolmadan yenisi yapılmaz; başarısız
  deneme de sayılır, çevrimdışıyken her açılışta tekrar denenmez). `update_check_enabled` ayarıyla
  kapatılabilir. `force=True` ("Şimdi kontrol et", kullanıcının açık isteği) bunların hiçbirine takılmaz.
* "Bu sürümü atla" (`skip_version`): atlanan sürüm otomatik kontrolde bir daha gösterilmez; daha yeni
  bir sürüm çıkarsa yine gösterilir.
* Kullanıcıya gösterilecek BAĞLANTI yanıttan ALINMAZ: `release_page_url()` ile kodda, doğrulanmış
  `kullanici/depo` adından kurulur. Yanıttan yalnızca `tag_name` okunur ve katı `MAJOR.MINOR.PATCH`
  biçimine uymayan her şey (önek/ek içeren etiketler, ön sürümler, taslaklar) yok sayılır.
* Otomatik indirme/kurma YOK; yalnızca "yeni sürüm var" bilgisi döner, indirmeyi kullanıcı yapar.
* Giden istekte kişisel veri yok: yalnızca standart başlıklar (uygulama adı/sürümü içeren User-Agent)
  ve kullanıcının IP adresi GitHub'a görünür (KVKK metninde belirtilecek, Adım 10.9d).
"""
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

import httpx
from sqlmodel import Session

from src.core import version as app_version
from src.core.models import utcnow
from src.core.settings import (
    KEY_UPDATE_CHECK_ENABLED,
    KEY_UPDATE_LAST_CHECK_AT,
    KEY_UPDATE_SKIPPED_VERSION,
    get_setting,
    set_setting,
)

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.github.com"
WEB_BASE_URL = "https://github.com"
REQUEST_TIMEOUT_SECONDS = 5.0
CHECK_INTERVAL = timedelta(days=1)

#: GitHub kullanıcı/depo adlarında geçebilen karakterler; URL'ye girmeden önce doğrulanır.
_REPO_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
#: Yalnızca `1.2.3` ya da `v1.2.3`; `1.2.3-beta`, `1.2`, `v1.2.3.4` KABUL EDİLMEZ.
_TAG_PATTERN = re.compile(r"v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})")

Fetcher = Callable[[str], Optional[str]]


@dataclass(frozen=True)
class UpdateInfo:
    """Kullanıcıya gösterilecek "yeni sürüm var" bilgisi."""

    version: str  # "1.2.0" (`v`siz)
    url: str  # `release_page_url()` ile kodda kurulmuş sürüm sayfası


def is_valid_repo(repo: Optional[str]) -> bool:
    """`"kullanici/depo"` biçiminde mi? `None`/boş/bozuk ise güncelleme kontrolü hiç çalışmaz.

    `fullmatch` (`$` sondaki satır sonunu da kabul eder) ve yalnızca noktalardan oluşan parçalar
    (`..`) reddedilir: ikisi de URL'yi istenmeyen bir yola çevirebilirdi.
    """
    if not isinstance(repo, str) or _REPO_PATTERN.fullmatch(repo) is None:
        return False
    return not any(part.strip(".") == "" for part in repo.split("/"))


def release_page_url(repo: str) -> str:
    """Kullanıcıya gösterilecek sürüm sayfası; her zaman kodda kurulur, yanıttan alınmaz."""
    return f"{WEB_BASE_URL}/{repo}/releases/latest"


def latest_release_api_url(repo: str) -> str:
    return f"{API_BASE_URL}/repos/{repo}/releases/latest"


def parse_version(tag: object) -> Optional[tuple[int, int, int]]:
    """`"v1.2.3"`/`"1.2.3"` -> `(1, 2, 3)`; başka her şey (ön sürüm eki dahil) `None`."""
    if not isinstance(tag, str):
        return None
    match = _TAG_PATTERN.fullmatch(tag.strip())
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def format_version(parsed: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in parsed)


def is_newer(candidate: str, current: str) -> bool:
    """`candidate` (ör. Release etiketi) `current`'tan KESİN olarak yeni mi? Çözülemezse `False`."""
    candidate_parsed = parse_version(candidate)
    current_parsed = parse_version(current)
    if candidate_parsed is None or current_parsed is None:
        return False
    return candidate_parsed > current_parsed


def _build_client() -> httpx.Client:
    app_token = app_version.APP_NAME.replace(" ", "-")
    return httpx.Client(
        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
        headers={
            "User-Agent": f"{app_token}/{app_version.__version__}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        follow_redirects=False,
    )


def fetch_latest_version(repo: str, client: Optional[httpx.Client] = None) -> Optional[str]:
    """`releases/latest`'ten en son yayınlanmış sürümü (`"1.2.0"`) döner.

    Ağ hatası, zaman aşımı, 200 dışı yanıt, bozuk JSON, taslak/ön sürüm, beklenmeyen etiket
    biçimi: hepsinde sessizce `None`. Hiçbir istisna dışarı çıkmaz.
    """
    if not is_valid_repo(repo):
        return None
    http = client if client is not None else _build_client()
    try:
        response = http.get(latest_release_api_url(repo))
        if response.status_code != 200:
            logger.debug(f"Güncelleme kontrolü: HTTP {response.status_code}")
            return None
        data = response.json()
        if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
            return None
        parsed = parse_version(data.get("tag_name"))
        return format_version(parsed) if parsed is not None else None
    except Exception as exc:  # noqa: BLE001 - "her hatada sessiz" kuralı (Adım 10.9c)
        logger.debug(f"Güncelleme kontrolü başarısız (sessiz): {exc!r}")
        return None
    finally:
        if client is None:
            http.close()


def _last_check_at(session: Session) -> Optional[datetime]:
    raw = get_setting(session, KEY_UPDATE_LAST_CHECK_AT)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None  # bozuk kayıt: "hiç kontrol edilmedi" say, kontrol yine de en fazla günde bir olur


def is_check_due(session: Session, now: Optional[datetime] = None) -> bool:
    """Otomatik kontrol şimdi yapılmalı mı? Kapalıysa ya da son denemeden 24 saat geçmediyse `False`.

    Son kontrol zamanı GELECEKTE görünüyorsa (saat geri alınmış) engellemez: aksi halde kontrol
    saat o ana yetişene kadar günlerce susardı.
    """
    if not get_setting(session, KEY_UPDATE_CHECK_ENABLED):
        return False
    last = _last_check_at(session)
    if last is None:
        return True
    elapsed = (now or utcnow()) - last
    return elapsed < timedelta(0) or elapsed >= CHECK_INTERVAL


def check_for_update(
    session: Session,
    *,
    force: bool = False,
    repo: Optional[str] = None,
    current: Optional[str] = None,
    now: Optional[datetime] = None,
    fetch: Optional[Fetcher] = None,
) -> Optional[UpdateInfo]:
    """Yeni bir sürüm varsa `UpdateInfo`, yoksa (ya da herhangi bir sebeple bilinemiyorsa) `None`.

    `repo` verilmezse `src/core/version.py::GITHUB_REPO` kullanılır; geçerli bir depo adı yoksa
    ağa ve veritabanına dokunmadan `None` döner. `force=True` kapalı ayarı, günde bir sınırını ve
    atlanan sürümü yok sayar. Ağ çağrısı DB yazma işlemi AÇIKKEN yapılmaz: yazma (son kontrol zamanı)
    yanıt geldikten sonra, tek commit ile yapılır.
    """
    repo = repo if repo is not None else app_version.GITHUB_REPO
    if not is_valid_repo(repo):
        return None
    moment = now or utcnow()
    if not force and not is_check_due(session, moment):
        return None

    latest = (fetch or fetch_latest_version)(repo)

    # Başarısız deneme de sayılır: çevrimdışıyken her açılışta yeniden denenmez.
    set_setting(session, KEY_UPDATE_LAST_CHECK_AT, moment.isoformat())
    session.commit()

    current_version = current if current is not None else app_version.__version__
    if latest is None or not is_newer(latest, current_version):
        return None
    if not force and get_setting(session, KEY_UPDATE_SKIPPED_VERSION) == latest:
        return None
    return UpdateInfo(version=latest, url=release_page_url(repo))


def skip_version(session: Session, version: str) -> bool:
    """"Bu sürümü atla": `version` otomatik kontrolde bir daha gösterilmez. Geçersiz sürümde `False`."""
    parsed = parse_version(version)
    if parsed is None:
        return False
    set_setting(session, KEY_UPDATE_SKIPPED_VERSION, format_version(parsed))
    session.commit()
    return True
