"""Google Places (New) Text Search istemcisi — PLAN.md Faz 10 Adım 10.6f (ertelenmiş 9.3).

Yalnızca `places.id` + `places.websiteUri` (+ sayfalama için `nextPageToken`) istenir; ad/adres
HİÇ istenmez ve saklanmaz (Google şartları yalnızca `place_id`'nin süresiz saklanmasına izin verir,
bkz. PLAN.md Adım 9 notu). Şirket adı, sitenin alan adından `derive_company_name()` ile üretilir.

PARA HARCAYAN KAYNAK — üç fren:
1. `PLACES_HARD_MAX_MONTHLY` (2000): kodda DEĞİŞTİRİLEMEZ üst sınır; ayardaki aylık sınır bunu
   aşarsa sessizce kırpılır ve loglanır (CLAUDE.md kural 5'teki `HARD_MAX_*` kalıbıyla aynı fikir).
2. Aylık istek sayacı: her istekten ÖNCE `reserve_request()` sınırı denetler ve sayacı artırıp
   COMMIT eder (süreç istekten sonra çökse de sayım kaybolmaz). Sınır dolmuşsa istek HİÇ atılmaz.
   Hata dönen istekler de sayılır (temkinli: Google'ın o isteği faturalayıp faturalamadığı
   bizden bilinemez).
3. `PlaceRef`: aynı `place_id` ikinci kez işlenmez.

Sayacın ayı Google'ın fatura ayıdır (Pasifik saati, `timeutil.pacific_month_key`).
"""
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, ContextManager, Iterator, Optional
from urllib.parse import urlparse

import httpx
from sqlmodel import Session, select

from src.company_finder.naming import derive_company_name
from src.company_finder.osm_source import clean_osm_website  # sosyal medya elemesi ortak
from src.company_finder.site_crawler import USER_AGENT
from src.core.cities import CityPreset
from src.core.db import get_session
from src.core.models import Company, PlaceRef, normalize_domain, utcnow
from src.core.settings import (
    DEFAULTS,
    KEY_DISCOVERY_STATE,
    KEY_PLACES_MONTHLY_LIMIT,
    KEY_PLACES_REQUESTS_MONTH_KEY,
    KEY_PLACES_REQUESTS_THIS_MONTH,
    get_setting,
    set_setting,
)
from src.core.timeutil import pacific_month_key

logger = logging.getLogger(__name__)

#: `Company.source` değeri (models.py'deki "google").
COMPANY_SOURCE = "google"
PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
#: Alan maskesi: YALNIZ bunlar. Başka alan eklemek faturayı değiştirir ve şartlara aykırı olabilir.
FIELD_MASK = "places.id,places.websiteUri,nextPageToken"
REGION_CODE = "TR"
LANGUAGE_CODE = "tr"
PAGE_SIZE = 20  # Text Search'ün izin verdiği en yüksek sayfa boyutu
#: Text Search bir arama için en çok 60 sonuç (3 sayfa × 20) döner; maliyet önizlemesi (10.6h)
#: "kelime × ilçe × bu kadar sayfa" diye hesaplar.
MAX_PAGES_PER_QUERY = 3
REQUEST_TIMEOUT_SECONDS = 30.0

#: Kodda sabit aylık istek tavanı (bkz. modül açıklaması, fren 1).
PLACES_HARD_MAX_MONTHLY = 2000

#: Sayaç kontrol + artırma + commit'i tek adımda, süreç genelinde sıralı yapar.
_QUOTA_LOCK = threading.Lock()

SessionFactory = Callable[[], ContextManager[Session]]


class PlacesError(Exception):
    """Places isteği başarısız. Mesaj API anahtarını ASLA içermez."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PlacesQuotaExceeded(PlacesError):
    """Aylık istek sınırı dolu; istek hiç atılmadı."""


# --- Aylık sayaç ----------------------------------------------------------------------------


def monthly_limit(session: Session) -> int:
    """Etkin aylık sınır: `min(ayar, PLACES_HARD_MAX_MONTHLY)`. Geçersiz ayar varsayılana düşer."""
    configured = get_setting(session, KEY_PLACES_MONTHLY_LIMIT)
    if isinstance(configured, bool) or not isinstance(configured, int):
        logger.warning(f"Geçersiz Places aylık sınırı ({configured!r}); varsayılan kullanılıyor")
        configured = DEFAULTS[KEY_PLACES_MONTHLY_LIMIT]
    if configured > PLACES_HARD_MAX_MONTHLY:
        logger.warning(
            f"Places aylık sınırı ({configured}) sabit üst sınırı ({PLACES_HARD_MAX_MONTHLY}) "
            "aşıyor; sabit sınıra kırpıldı"
        )
        return PLACES_HARD_MAX_MONTHLY
    return max(configured, 0)


def requests_this_month(session: Session, now: Optional[datetime] = None) -> int:
    """Bu ay (Pasifik) atılmış istek sayısı; kayıtlı ay farklıysa 0 (ay değişince sıfırlanır)."""
    if get_setting(session, KEY_PLACES_REQUESTS_MONTH_KEY) != pacific_month_key(now or utcnow()):
        return 0
    count = get_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH)
    return count if isinstance(count, int) and count > 0 else 0


def remaining_requests(session: Session, now: Optional[datetime] = None) -> int:
    return max(monthly_limit(session) - requests_this_month(session, now), 0)


def reserve_request(session: Session, now: Optional[datetime] = None) -> bool:
    """Bir istek hakkı ayırır: sınır dolmamışsa sayacı artırıp COMMIT eder ve True döner.

    İstekten ÖNCE çağrılır. Dolmuşsa hiçbir şey yazmaz, False döner. Commit bilerek burada
    (istekten önce): çökme/iptal olsa da sayım kalıcıdır, sayaç asla eksik saymaz.
    """
    moment = now or utcnow()
    with _QUOTA_LOCK:
        used = requests_this_month(session, moment)
        if used >= monthly_limit(session):
            return False
        set_setting(session, KEY_PLACES_REQUESTS_MONTH_KEY, pacific_month_key(moment))
        set_setting(session, KEY_PLACES_REQUESTS_THIS_MONTH, used + 1)
        session.commit()
        return True


# --- PlaceRef -------------------------------------------------------------------------------


def known_place_ids(session: Session, place_ids: list[str]) -> set[str]:
    """Verilenlerden daha önce işlenmiş (PlaceRef'te kayıtlı) olanlar."""
    if not place_ids:
        return set()
    rows = session.exec(select(PlaceRef.place_id).where(PlaceRef.place_id.in_(place_ids))).all()
    return set(rows)


def record_place_ref(session: Session, place_id: str, company_id: Optional[int] = None) -> None:
    """`place_id`'yi işlenmiş olarak kaydeder (varsa `company_id`'yi tamamlar). COMMIT eder."""
    ref = session.get(PlaceRef, place_id)
    if ref is None:
        session.add(PlaceRef(place_id=place_id, company_id=company_id))
    elif company_id is not None and ref.company_id is None:
        ref.company_id = company_id
    session.commit()


# --- İstemci --------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaceHit:
    place_id: str
    website_uri: Optional[str]


@dataclass(frozen=True)
class PlacesPage:
    places: list[PlaceHit]
    next_page_token: Optional[str]


def build_request_body(
    query: str, bbox: tuple[float, float, float, float], page_token: Optional[str]
) -> dict[str, Any]:
    """Bizim bbox [min_lon, min_lat, max_lon, max_lat]; Google `rectangle` low/high ister.
    Sayfalamada `pageToken` dışındaki tüm parametreler ilk çağrıyla AYNI olmalı (Google kuralı)."""
    min_lon, min_lat, max_lon, max_lat = bbox
    body: dict[str, Any] = {
        "textQuery": query,
        "regionCode": REGION_CODE,
        "languageCode": LANGUAGE_CODE,
        "pageSize": PAGE_SIZE,
        "locationBias": {
            "rectangle": {
                "low": {"latitude": min_lat, "longitude": min_lon},
                "high": {"latitude": max_lat, "longitude": max_lon},
            }
        },
    }
    if page_token:
        body["pageToken"] = page_token
    return body


def parse_page(payload: Any) -> PlacesPage:
    """Yanıt gövdesini `PlacesPage`'e çevirir; `id`'si olmayan kayıtlar atlanır."""
    if not isinstance(payload, dict):
        raise PlacesError("Google Places yanıtı beklenen biçimde değil")
    hits: list[PlaceHit] = []
    for item in payload.get("places") or []:  # sonuç yoksa `places` alanı hiç gelmez
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            continue
        website = item.get("websiteUri")
        hits.append(PlaceHit(item["id"], website if isinstance(website, str) else None))
    token = payload.get("nextPageToken")
    return PlacesPage(hits, token if isinstance(token, str) and token else None)


def _error_message(response: httpx.Response) -> str:
    try:
        detail = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        detail = ""
    return f"Google Places HTTP {response.status_code}" + (f": {str(detail)[:200]}" if detail else "")


class GooglePlacesClient:
    """Text Search çağrıları. Her çağrı `reserve_request()` ile aylık sınırı denetler."""

    def __init__(
        self,
        api_key: str,
        session_factory: SessionFactory = get_session,
        *,
        client: Optional[httpx.Client] = None,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._api_key = api_key
        self._session_factory = session_factory
        self._client = client
        self._now = now

    def search_text(
        self,
        query: str,
        bbox: tuple[float, float, float, float],
        page_token: Optional[str] = None,
    ) -> PlacesPage:
        """Bir sayfa sonuç döner. Hata: `PlacesQuotaExceeded` (sınır dolu, istek atılmadı) ya da
        `PlacesError` (ağ/HTTP/biçim)."""
        if not self._api_key:
            raise PlacesError("Google Places API anahtarı yok")
        if not query.strip():
            raise ValueError("Arama metni boş olamaz")
        with self._session_factory() as session:
            if not reserve_request(session, self._now()):
                raise PlacesQuotaExceeded(
                    f"Google Places aylık istek sınırı doldu ({monthly_limit(session)})"
                )

        headers = {
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": FIELD_MASK,
            "User-Agent": USER_AGENT,
        }
        http = self._client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            response = http.post(
                PLACES_SEARCH_URL, json=build_request_body(query, bbox, page_token), headers=headers
            )
        except httpx.HTTPError as exc:
            # `repr(exc)` değil: yalnızca tür adı (anahtar başlıkta, yine de sızdırma riski alma).
            raise PlacesError(f"Google Places isteği başarısız ({type(exc).__name__})") from None
        finally:
            if self._client is None:
                http.close()
        if response.status_code != 200:
            raise PlacesError(_error_message(response), status_code=response.status_code)
        try:
            return parse_page(response.json())
        except ValueError:
            raise PlacesError("Google Places yanıtı JSON değil") from None


# --- Kaynak ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaceLead:
    """Places'ten çıkan aday (`discovery.DiscoveryLead`); `email` hep None."""

    name: str
    website: Optional[str]
    city: str
    place_id: str
    email: Optional[str] = None


class GooglePlacesSource:
    """`discovery.DiscoverySource`: bir arama metni + bir şehir (Otopilot'a 10.6h'de bağlandı).

    Sayfalama kaldığı yerden devam eder: sıradaki `nextPageToken`, bir sayfa tamamen işlendikten
    sonra `discovery_state["google:<sorgu>|<şehir kodu>"]`'na yazılır. `PlaceRef` yalnızca aday
    tüketici tarafından alındıktan SONRA (generator devam ettirildiğinde) yazılır; yarıda kesilen
    bir çalıştırmada kayıt edilmemiş adaylar bir sonraki sefer tekrar sunulur, `add_company()`
    tekilleştirmesi (kural 4) çifte kaydı önler.

    **İlçe bölme:** Text Search arama başına en çok 60 sonuç verir; büyük ilde tek sorgu yetmez.
    `district` verilirse sorgu `"<arama kelimesi> <ilçe> <il>"` olur ve o ilçenin KENDİ
    `discovery_state` anahtarı (`google:<sorgu>|<il kodu>|<ilçe>`) vardır. İlçe listesi olmayan
    ilde `district=None`: sorgu `"<arama kelimesi> <il>"`, anahtar eskisi gibi. Aylık sayaç ve
    2000 tavanı ilçelerden bağımsız, hepsi için ORTAK işler (her istek `reserve_request()` geçer);
    bkz. `places_sources()`.
    """

    #: Bir Places kaynağı hata/sınır yüzünden durursa Otopilot aynı türden kalan kaynakları bu
    #: çalıştırmada DENEMEZ: hata dönen istekler de sayılır/faturalanabilir (403 gibi kalıcı bir
    #: hata 125 ilçe aramasında 125 kez tekrarlanmasın).
    failure_stops_siblings = True

    def __init__(
        self,
        query: str,
        city: CityPreset,
        client: GooglePlacesClient,
        *,
        district: Optional[str] = None,
        session_factory: SessionFactory = get_session,
    ) -> None:
        self._query = query
        self._city = city
        self._district = district
        self._client = client
        self._session_factory = session_factory
        #: Son `discover()` hata/sınır yüzünden erken bittiyse True (Otopilot "bitti" işaretlemez).
        self.failed = False
        self.failure_reason: Optional[str] = None

    @property
    def text_query(self) -> str:
        """Google'a giden metin: `<arama kelimesi> [<ilçe>] <il>`."""
        parts = [self._query, self._district, self._city.name_tr]
        return " ".join(part for part in parts if part)

    @property
    def name(self) -> str:
        place = f"{self._city.name_tr}/{self._district}" if self._district else self._city.name_tr
        return f"Google Places ({place}: {self._query})"

    @property
    def state_key(self) -> str:
        base = f"google:{self._query}|{self._city.id}"
        return f"{base}|{self._district}" if self._district else base

    @property
    def company_source(self) -> str:
        return COMPANY_SOURCE

    def discover(
        self, *, fetcher: Any = None, sleeper: Optional[Callable[[float], None]] = None
    ) -> Iterator[PlaceLead]:
        self.failed = False
        self.failure_reason = None
        token = self._load_token()
        restarted = False
        while True:
            try:
                page = self._client.search_text(self.text_query, self._city.bbox, token)
            except PlacesQuotaExceeded as exc:
                self._fail(str(exc))
                return
            except PlacesError as exc:
                if token is not None and exc.status_code == 400 and not restarted:
                    # Kayıtlı sayfa belirteci geçersizleşmiş olabilir: bir kez baştan dene.
                    logger.info(f"{self.name}: kayıtlı sayfa belirteci reddedildi, baştan başlanıyor")
                    token, restarted = None, True
                    self._save_token(None)
                    continue
                self._fail(str(exc))
                return
            yield from self._process(page)
            token = page.next_page_token
            self._save_token(token)
            if token is None:
                return

    def _process(self, page: PlacesPage) -> Iterator[PlaceLead]:
        with self._session_factory() as session:
            known = known_place_ids(session, [hit.place_id for hit in page.places])
        for hit in page.places:
            if hit.place_id in known:
                continue
            website = clean_osm_website(hit.website_uri)
            if website is None:  # sitesiz/sosyal medya: bu yer için yapılacak iş yok
                self._record(hit.place_id, None)
                continue
            domain = normalize_domain(urlparse(website).netloc)
            yield PlaceLead(
                name=derive_company_name("", domain),
                website=website,
                city=self._city.name_tr,
                place_id=hit.place_id,
            )
            self._record(hit.place_id, domain)

    def _record(self, place_id: str, domain: Optional[str]) -> None:
        with self._session_factory() as session:
            company_id = None
            if domain:
                company_id = session.exec(select(Company.id).where(Company.domain == domain)).first()
            record_place_ref(session, place_id, company_id)

    def _fail(self, reason: str) -> None:
        self.failed = True
        self.failure_reason = reason
        logger.warning(f"{self.name}: keşif durdu: {reason}")

    def _load_token(self) -> Optional[str]:
        with self._session_factory() as session:
            entry = get_setting(session, KEY_DISCOVERY_STATE).get(self.state_key)
        token = entry.get("next_page_token") if isinstance(entry, dict) else None
        return token if isinstance(token, str) and token else None

    def _save_token(self, token: Optional[str]) -> None:
        with self._session_factory() as session:
            state = get_setting(session, KEY_DISCOVERY_STATE)
            entry = state.get(self.state_key)
            entry = dict(entry) if isinstance(entry, dict) else {}
            if token:
                entry["next_page_token"] = token
            else:
                entry.pop("next_page_token", None)
            if entry:
                state[self.state_key] = entry
            else:
                state.pop(self.state_key, None)
            set_setting(session, KEY_DISCOVERY_STATE, state)
            session.commit()


def places_sources(
    query: str,
    city: CityPreset,
    client: GooglePlacesClient,
    *,
    session_factory: SessionFactory = get_session,
) -> list[GooglePlacesSource]:
    """Bir arama kelimesi + il için kaynak listesi: ilin ilçe listesi varsa ilçe başına bir kaynak,
    yoksa tek kaynak. Hepsi AYNI `client`'ı paylaşır, yani aylık sayaç/tavan ortaktır."""
    if not city.districts:
        return [GooglePlacesSource(query, city, client, session_factory=session_factory)]
    return [
        GooglePlacesSource(query, city, client, district=district, session_factory=session_factory)
        for district in city.districts
    ]


# --- Anahtar testi (Ayarlar → "Anahtarı test et", 10.6g) -----------------------------------------

#: Test isteğinin alan maskesi: YALNIZ `places.id`. Google'ın fiyat listesine göre yalnız kimlik
#: isteyen Text Search "Essentials (IDs Only)" sınıfındadır ve ücretsizdir; `websiteUri` gibi bir
#: alan eklemek isteği ücretli Enterprise sınıfına taşırdı (bkz. PLAN.md 10.6 fiyat notu). Bu sabit
#: bilerek `FIELD_MASK`'tan ayrı ve testle sabitlenmiştir.
KEY_TEST_FIELD_MASK = "places.id"
KEY_TEST_QUERY = "kafe Ankara"


def check_api_key(api_key: str, *, client: Optional[httpx.Client] = None) -> Optional[str]:
    """Anahtarı tek, ücretsiz bir istekle dener; başarıda `None`, sorunda Türkçe hata metni döner.

    Aylık sayaca SAYILMAZ: `places.id` maskesi ücretsiz sınıftadır, sayaç ise ücretli
    (`websiteUri`) istekleri sınırlamak içindir. Hata metni anahtarı ASLA içermez.
    """
    if not api_key:
        return "Google Places API anahtarı yok."
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": KEY_TEST_FIELD_MASK,
        "User-Agent": USER_AGENT,
    }
    body = {"textQuery": KEY_TEST_QUERY, "regionCode": REGION_CODE, "languageCode": LANGUAGE_CODE, "pageSize": 1}
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        response = http.post(PLACES_SEARCH_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        return f"Google'a ulaşılamadı ({type(exc).__name__}). İnternet bağlantınızı kontrol edin."
    finally:
        if client is None:
            http.close()
    if response.status_code == 200:
        return None
    detail = _error_message(response)
    if response.status_code == 400:
        return f"Anahtar geçersiz görünüyor. Baştan kopyalayıp yeniden kaydedin. ({detail})"
    if response.status_code == 403:
        return (
            "Anahtar reddedildi: 'Places API (New)' etkin değil, faturalandırma bağlı değil ya da "
            f"anahtarın API kısıtlaması bu API'ye izin vermiyor. ({detail})"
        )
    if response.status_code == 429:
        return f"Google istek kotası dolu (429); biraz sonra yeniden deneyin. ({detail})"
    return detail
