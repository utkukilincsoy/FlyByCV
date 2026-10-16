"""OpenStreetMap (Overpass API) firma kaynağı — PLAN.md Faz 10 Adım 10.6e.

Şehrin bbox'ı (10.6a) + meslek paketinin `osm_tags`'ı (10.6b) ile TEK bir Overpass sorgusu kurar,
web sitesi (`website`/`contact:website`/`url`) ya da e-postası (`email`/`contact:email`) olan
kayıtları `OsmLead` olarak üretir. Bu modül veritabanına DOKUNMAZ (`discovery.import_leads()`
yazar); OSM e-postası da `add_company()`'den geçer, rol adresi değilse otomatik gönderim olmaz
(kural 4/10). Sitesi olmayan kayıtta alan adı e-postadan çıkar (ücretsiz sağlayıcılar hariç).

Overpass kullanım kurallarına uyum:
* Tanımlayıcı `User-Agent` (`site_crawler.USER_AGENT`).
* Aynı anda TEK istek: süreç genelinde tek bir `threading.Lock`.
* 429 / 504'te bekleyip tekrar dener (`Retry-After` varsa ona uyar), en fazla `MAX_RETRIES` kez.
* Ana sunucu (overpass-api.de) bu denemelerden sonra hâlâ başarısızsa (ya da bağlantı kurulamazsa)
  yedek sunucuya geçilir: OSM wiki'nin "Public Overpass API instances" listesindeki Private.coffee
  örneği ("Feel free to use our service in any project, there is no rate limit in place" —
  wiki, 30.09.2026). Sorguda kişisel veri yoktur (yalnız etiketler + şehir kutusu).
* Paket+şehir başına sonuç 7 gün dosya önbelleğinde tutulur (`CACHE_TTL`); önbellek anahtarı
  SORGUNUN KENDİSİNİN (etiketler + bbox) özetini içerir — etiketler ya da şehir kutusu değişirse
  eski önbellek kullanılmaz.

Veri ODbL lisanslıdır: arayüzde "© OpenStreetMap katkıcıları" atfı gösterilir (Ayarlar, 10.6c).
"""
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from urllib.parse import urlparse

import httpx

from src.company_finder.csv_import import ImportReport
from src.company_finder.discovery import Fetcher, import_leads
from src.company_finder.email_extractor import normalize_email
from src.company_finder.free_mail import is_free_mail_email
from src.company_finder.naming import derive_company_name
from src.company_finder.site_crawler import USER_AGENT
from src.core.cities import CityPreset
from src.core.models import normalize_domain
from src.core.paths import data_dir
from src.core.professions import ProfessionPreset

logger = logging.getLogger(__name__)

SOURCE_OSM = "osm"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
#: Ana sunucu sık 504 verdiğinde kullanılan yedek (OSM wiki'de listeli, anahtarsız, limitsiz).
OVERPASS_BACKUP_URL = "https://overpass.private.coffee/api/interpreter"
OVERPASS_URLS = (OVERPASS_URL, OVERPASS_BACKUP_URL)
#: Overpass sunucu tarafı zaman aşımı (sorgu içinde) ve istemci zaman aşımı (biraz daha uzun).
QUERY_TIMEOUT_SECONDS = 90
REQUEST_TIMEOUT_SECONDS = 120.0
CACHE_TTL = timedelta(days=7)
#: Bekleyip tekrar denenen durum kodları: 429 (çok istek), 504 (sunucu meşgul/zaman aşımı).
RETRY_STATUS_CODES = frozenset({429, 504})
MAX_RETRIES = 3
DEFAULT_RETRY_WAIT_SECONDS = 30.0
MAX_RETRY_WAIT_SECONDS = 120.0

#: Sosyal medya/bağlantı sayfaları şirketin kendi sitesi DEĞİLDİR; alan adı olarak kaydedilirse
#: tüm firmalar "facebook.com" diye tekilleşir ve hiçbirine ulaşılamazdı.
SOCIAL_DOMAINS = frozenset(
    {
        "facebook.com", "fb.com", "fb.me", "instagram.com", "linkedin.com", "twitter.com",
        "x.com", "youtube.com", "youtu.be", "tiktok.com", "pinterest.com", "wa.me",
        "t.me", "linktr.ee", "sahibinden.com",
    }
)

#: Web sitesi ya da e-posta taşıyabilen etiketler; kayıt bunlardan en az birine sahipse alınır.
WEBSITE_KEYS = ("website", "contact:website", "url")
EMAIL_KEYS = ("email", "contact:email")
CONTACT_KEY_REGEX = "^(" + "|".join(WEBSITE_KEYS + EMAIL_KEYS) + ")$"

#: Aynı anda TEK Overpass isteği (kullanım kuralı) — süreç genelinde paylaşılır.
_REQUEST_LOCK = threading.Lock()


@dataclass(frozen=True)
class OsmLead:
    """OSM'den çıkan aday. `discovery.DiscoveryLead` arayüzünü karşılar; `email` hep None."""

    name: str
    website: Optional[str]
    city: str
    email: Optional[str] = None


# --- Sorgu ----------------------------------------------------------------------------------


def build_query(tags: list[str], bbox: tuple[float, float, float, float]) -> str:
    """Overpass QL: her etiket için `website`'i olan düğüm/yol/ilişki.

    Bizim bbox sırası [min_lon, min_lat, max_lon, max_lat]; Overpass (güney, batı, kuzey, doğu)
    yani (min_lat, min_lon, max_lat, max_lon) bekler.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    area = f"({min_lat},{min_lon},{max_lat},{max_lon})"
    clauses = []
    for tag in tags:
        key, _, value = tag.partition("=")
        clauses.append(f'  nwr["{key}"="{value}"][~"{CONTACT_KEY_REGEX}"~"."]{area};')
    body = "\n".join(clauses)
    return f"[out:json][timeout:{QUERY_TIMEOUT_SECONDS}];\n(\n{body}\n);\nout tags;"


# --- Önbellek -------------------------------------------------------------------------------


def cache_dir() -> Path:
    return data_dir() / "cache" / "osm"


def _cache_path(profession_id: str, city_id: str, query: str, directory: Path) -> Path:
    """Anahtar sorgunun kendisinin özeti: etiket ya da bbox değişirse dosya adı da değişir."""
    digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:8]
    return directory / f"{profession_id}_{city_id}_{digest}.json"


def _read_cache(path: Path, now: datetime) -> Optional[list[dict[str, Any]]]:
    """Taze önbellek varsa eleman listesini döner; yok/bayat/bozuksa None (çökme yok)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(raw["fetched_at"])
        elements = raw["elements"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(elements, list) or now - fetched_at >= CACHE_TTL:
        return None
    return elements


def _write_cache(path: Path, elements: list[dict[str, Any]], now: datetime) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": now.isoformat(), "elements": elements}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning(f"OSM önbelleği yazılamadı ({path}): {exc!r}")


# --- İstek ----------------------------------------------------------------------------------


def _retry_wait(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("Retry-After", "")
    if header.strip().isdigit():
        return min(float(header), MAX_RETRY_WAIT_SECONDS)
    return min(DEFAULT_RETRY_WAIT_SECONDS * (attempt + 1), MAX_RETRY_WAIT_SECONDS)


def _post_with_retries(
    http: httpx.Client, url: str, query: str, sleeper: Callable[[float], None]
) -> httpx.Response:
    """Tek sunucuya gönderir; 429/504'te `MAX_RETRIES` kez bekleyip dener. Başarısızsa
    `httpx.HTTPStatusError` (son yanıtla) ya da `httpx.TransportError` fırlatır."""
    for attempt in range(MAX_RETRIES + 1):
        response = http.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT})
        if response.status_code in RETRY_STATUS_CODES and attempt < MAX_RETRIES:
            wait = _retry_wait(response, attempt)
            logger.info(
                f"Overpass ({urlparse(url).netloc}) {response.status_code} döndü; {wait:.0f} sn "
                f"beklenip tekrar denenecek ({attempt + 1}/{MAX_RETRIES})"
            )
            sleeper(wait)
            continue
        break
    response.raise_for_status()
    return response


def _should_try_backup(exc: Exception) -> bool:
    """Sunucu tarafı/ağ sorunu (yedek işe yarayabilir) mı, yoksa bizim sorgumuz mu bozuk (yaramaz)?"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code in RETRY_STATUS_CODES
    return isinstance(exc, httpx.TransportError)


def fetch_overpass(
    query: str,
    *,
    client: Optional[httpx.Client] = None,
    sleeper: Callable[[float], None] = time.sleep,
    urls: tuple[str, ...] = OVERPASS_URLS,
) -> list[dict[str, Any]]:
    """Sorguyu Overpass'a gönderir, `elements` listesini döner.

    Her sunucuda 429/504'te `MAX_RETRIES` kez bekleyip yeniden dener; ana sunucu sunucu/ağ
    hatasıyla vazgeçerse sıradaki (yedek) sunucuya geçer. Hepsi başarısızsa ya da bizim sorgumuz
    reddedilirse (4xx) `httpx.HTTPError`/`ValueError` fırlatır (çağıran yakalar). İstek süresince
    süreç genelindeki kilit tutulur — aynı anda ikinci bir Overpass isteği atılmaz.
    """
    own_client = client is None
    http = client or httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS
    )
    try:
        with _REQUEST_LOCK:
            response = None
            for index, url in enumerate(urls):
                try:
                    response = _post_with_retries(http, url, query, sleeper)
                    break
                except httpx.HTTPError as exc:
                    if index == len(urls) - 1 or not _should_try_backup(exc):
                        raise
                    logger.warning(
                        f"Overpass ({urlparse(url).netloc}) başarısız ({exc!r}); yedek sunucuya "
                        f"({urlparse(urls[index + 1]).netloc}) geçiliyor"
                    )
            payload = response.json()
    finally:
        if own_client:
            http.close()
    elements = payload.get("elements") if isinstance(payload, dict) else None
    if not isinstance(elements, list):
        raise ValueError("Overpass yanıtında 'elements' listesi yok")
    return elements


# --- Kayıt -> aday --------------------------------------------------------------------------


def clean_osm_website(value: Optional[str]) -> Optional[str]:
    """OSM `website` değerini kullanılabilir bir siteye çevirir; uygun değilse None.

    - Boş ya da birden çok değer (`a.com;b.com`): ilki alınır.
    - Sosyal medya/bağlantı sayfaları (facebook/instagram/linkedin vb.) elenir.
    - Şeması yoksa `https://` eklenir.
    """
    if not value:
        return None
    first = value.replace("\xa0", " ").split(";")[0].strip()
    if not first:
        return None
    if "://" not in first:
        first = f"https://{first}"
    host = normalize_domain(urlparse(first).netloc or first)
    if not host or "." not in host:
        return None
    if any(host == d or host.endswith(f".{d}") for d in SOCIAL_DOMAINS):
        return None
    return first


def _first_website(tags: dict[str, Any]) -> Optional[str]:
    """`website`, `contact:website`, `url` sırasıyla ilk kullanılabilir site."""
    for key in WEBSITE_KEYS:
        website = clean_osm_website(tags.get(key))
        if website is not None:
            return website
    return None


def clean_osm_email(value: Optional[str]) -> Optional[str]:
    """OSM `email` değerinden ilk geçerli adresi alır (`normalize_email`: `mailto:`, `;` listesi);
    geçersizse None. Rol adresi kararı burada DEĞİL, `add_company()`'de verilir (kural 10)."""
    return normalize_email(value) if value else None


def _first_email(tags: dict[str, Any]) -> Optional[str]:
    for key in EMAIL_KEYS:
        email = clean_osm_email(tags.get(key))
        if email is not None:
            return email
    return None


def leads_from_elements(elements: list[dict[str, Any]], city_name: str) -> list[OsmLead]:
    """Ham Overpass elemanlarından adayları çıkarır.

    Site `website`/`contact:website`/`url`'den, e-posta `email`/`contact:email`'den gelir. Siteyi
    olmayan kayıt, e-postası ücretsiz sağlayıcıdaysa elenir (alan adı kimlik olamaz); değilse
    alan adı e-postadan çıkar. Sosyal medya siteleri elenir, aynı alan adı yalnız bir kez.
    Rol adresi kararı `add_company()`'de (kural 10) verilir.
    """
    leads: list[OsmLead] = []
    seen: set[str] = set()
    for element in elements:
        tags = element.get("tags") if isinstance(element, dict) else None
        if not isinstance(tags, dict):
            continue
        website = _first_website(tags)
        email = _first_email(tags)
        if website is not None:
            domain = normalize_domain(urlparse(website).netloc)
        elif email is not None and not is_free_mail_email(email):
            domain = normalize_domain(email.rpartition("@")[2])
        else:
            continue
        if domain in seen:
            continue
        seen.add(domain)
        # OSM `name` varsa doğrudan o; yoksa alan adından üretilir (sayfa indirilmez).
        name = str(tags.get("name") or "").strip() or derive_company_name("", domain)
        leads.append(OsmLead(name=name, website=website, city=city_name, email=email))
    return leads


# --- Kaynak ---------------------------------------------------------------------------------


class OsmSource:
    """`discovery.DiscoverySource` arayüzünü karşılar (bir meslek + bir şehir için)."""

    def __init__(
        self,
        profession: ProfessionPreset,
        city: CityPreset,
        *,
        client: Optional[httpx.Client] = None,
        cache_directory: Optional[Path] = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        if not profession.osm_tags:
            raise ValueError(f"{profession.id!r} paketinde osm_tags yok")
        self._profession = profession
        self._city = city
        self._client = client
        self._cache_directory = cache_directory
        self._now = now
        #: Son `discover()` Overpass hatasıyla boş döndüyse True — Otopilot bu durumda kaynağı
        #: "bitti" diye KALICI işaretlemez (geçici bir sunucu hatası kaynağı sonsuza dek kapatmasın).
        self.failed = False

    @property
    def name(self) -> str:
        return f"OpenStreetMap ({self._city.name_tr})"

    @property
    def state_key(self) -> str:
        return f"osm:{self._profession.id}|{self._city.id}"

    @property
    def company_source(self) -> str:
        return SOURCE_OSM

    def discover(
        self,
        *,
        fetcher: Optional[Fetcher] = None,  # arayüz uyumu; Overpass POST'u kendi istemcisini kullanır
        sleeper: Callable[[float], None] = time.sleep,
    ) -> Iterator[OsmLead]:
        self.failed = False
        elements = self._load_elements(sleeper)
        if elements is None:
            self.failed = True
            return
        yield from leads_from_elements(elements, self._city.name_tr)

    def _load_elements(self, sleeper: Callable[[float], None]) -> Optional[list[dict[str, Any]]]:
        tags = list(self._profession.osm_tags or [])
        query = build_query(tags, self._city.bbox)
        directory = self._cache_directory if self._cache_directory is not None else cache_dir()
        path = _cache_path(self._profession.id, self._city.id, query, directory)
        now = self._now()
        cached = _read_cache(path, now)
        if cached is not None:
            logger.info(f"{self.name}: önbellekten {len(cached)} kayıt (7 gün içinde)")
            return cached
        try:
            elements = fetch_overpass(query, client=self._client, sleeper=sleeper)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(f"{self.name}: Overpass sorgusu başarısız: {exc!r}")
            return None
        _write_cache(path, elements, now)
        return elements


def import_osm_leads(session, leads: Iterator[OsmLead]) -> ImportReport:
    """OSM adaylarını veritabanına ekler — `discovery.import_leads()`'in ince kabuğu.

    COMMIT ETMEZ ("commit çağırana aittir", `src/core/db.py`)."""
    return import_leads(session, leads, company_source=SOURCE_OSM, label="OSM")
