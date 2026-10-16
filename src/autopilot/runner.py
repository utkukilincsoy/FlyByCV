"""Otopilot yöneticisi: keşif → tara → hazırla → gönder döngüsü (PLAN.md Faz 9 Adım 9.5).

Qt İÇERMEZ (ileride Adım 9.6'nın bir QThread'den çağırması için — `src/ui/jobs.py`'deki
Qt'siz iş katmanıyla aynı ayrım ilkesi, bkz. PLAN Faz 5.1c Not 9). Tek giriş noktası
`run_autopilot()`.

**Ön kontrol (başlamadan ÖNCE):** acil durdur/güvenlik freni açık mı, Gmail hesabı/şifresi
kayıtlı mı, en az bir keşif kaynağı VEYA veritabanında en az bir şirket var mı — herhangi biri
eksikse Otopilot HİÇ başlamaz, Türkçe bir sebeple hemen döner (`_run_preflight_check`).

Başladıktan sonra şu durumlardan biri gerçekleşene kadar sürer:
  * yapılacak iş kalmaz → **"Bugünlük iş bitti"** ile durur (keşfedilecek yeni kaynak /
    taranacak şirket / hazırlanacak başvuru / gönderilecek uygun başvuru hiç yoksa),
  * `should_stop()` True döner (kullanıcı Otopilot'u durdurdu — henüz UI yok, Adım 9.6'ya
    hazır bir kanca, `process_queue`'nun aynı adlı parametresiyle aynı ilke),
  * **acil durdur/güvenlik freni (`sending_paused`) açılır — bu durumda TÜM adımlar (keşif,
    tarama, hazırlama, gönderim) ANINDA durur, yalnızca gönderim değil.** Karar HER ZAMAN
    `rate_limiter.SendWindow.code`'a bakılarak verilir (`CODE_PAUSED`), Türkçe `reason`
    metni ayrıştırılmaz — bkz. PLAN.md Adım 9.4 Not 2 (`SendWindow.code`'un varoluş sebebi).

**Saatlik limit farklı ele alınır:** `CODE_HOURLY_LIMIT` yüzünden gönderim durursa VE hazırlık
da tükenmişse (yapacak başka iş yoksa) ama gönderilmeyi BEKLEYEN bir başvuru varsa, Otopilot
DURMAZ — pencere açılana kadar bekler (o an hazırlık zaten tükendiği için düz bir bekleme olur;
hazırlık tükenmemişse zaten normal akışta "verimli bekleme" ile ilerlenir) ve süre dolunca
göndermeye devam eder. `CODE_DAILY_LIMIT`/`CODE_OUTSIDE_HOURS` bu şekilde beklenmez — hazırlık
tükendiğinde "Bugünlük iş bitti" ile durulur (günlük kota/mesai saati genelde saatlerce sürer,
bunu senkron beklemek pencerenin ömrünü aşabilir; bir sonraki açılışta/turda kaldığı yerden
devam eder).

**Kaynak sırası:** Hangi kaynakların kullanılacağını meslek + şehir + ayarlar belirler
(`resolve_discovery_sources` → `source_selector.select_sources`, Adım 10.6d); kaynaklar ortak
`DiscoverySource` arayüzüyle (`company_finder/discovery.py`) çalışır. Bu adımda yalnızca
teknokent, OSM (10.6e, `osm:<meslek>|<il>` anahtarı, `Company.source="osm"`) ve Google Places
(10.6f/10.6h; meslek paketinin HER arama kelimesi × ilin HER ilçesi için ayrı kaynak,
`google:<kelime>|<il>[|<ilçe>]`, `Company.source="google"`) bağlı. Places yalnızca "Google Places
kullan" düğmesi AÇIK VE anahtar kayıtlıysa seçilir; aksi halde hiç kurulmaz (istemci bile).
Hata veren bir kaynak (`source.failed`) "bitti" diye işaretlenmez; Places kaynağı hata/sınır
yüzünden durursa aynı türden KALAN aramalar bu çalıştırmada denenmez (`failure_stops_siblings`:
hata dönen istekler de sayılır). Başlat'tan önce `build_source_preview()` kaynak özeti ve Places
maliyet önizlemesini üretir; keşif bitince `discovery_summary` dürüst bir sonuç mesajı yayar.

**Pencere kapalıyken loglama:** Gönderim penceresi biliniyorsa (mesai dışı / günlük ya da
saatlik limit) `process_queue()` HİÇ ÇAĞRILMAZ — çağrılsaydı kendi `can_send_now()` kontrolünü
tekrar yapıp aynı "gönderim başlıyor"/"gönderim durdu" çiftini anında, her hazırlık biriminde
yeniden loglardı. Bunun yerine sebep yalnızca DEĞİŞTİĞİNDE bir kez loglanır (`last_blocked_reason`).
Hazırlık yine de ilerler ("verimli bekleme" ruhu korunur) ama her ilerlemeden sonra
`BLOCKED_PREP_PACE_SECONDS` kadar beklenir — ağ maliyeti olmayan bir kaynakta (ör. tek istekle
önbelleğe alınan ODTÜ Teknokent listesi) aksi halde hiç gecikme olmadan saniyede onlarca kez
aynı log satırı üretilebiliyordu (HTTP isteği TEKRARLANMIYOR, yalnızca zaten bellekte duran
liste hızla tüketiliyordu — gerçek üründe 15 ms aralıklı tekrar gözlemlendi).

**Verimli bekleme:** `sender/worker.process_queue()`'nun gönderimler arası zorunlu beklemesi
(`sleeper` kancası) burada sarmalanır (`_make_efficient_sleeper`): gerçekten uyumak yerine
ÖNCE bir "hazırlık birimi" (sırayla: gelen kutusunu kontrol et [30 dk'da bir] → sıradaki
başvuruyu hazırla → sıradaki şirketi tara → sıradaki keşif turunu ilerlet) çalıştırılır, ondan
ARTA KALAN süre kadar gerçekten beklenir; iş beklemeden uzun sürdüyse (yavaş bir ağ isteği) hiç
ek beklenmez. `process_queue` kuyruk tamamen boşken (ör. ilk açılış, hiç şirket yokken)
`sleeper`'ı hiç ÇAĞIRMAZ — bu yüzden `run_autopilot`'un kendi döngüsü her `process_queue`
turundan SONRA da ayrıca bir hazırlık birimi dener; döngü bu şekilde ilerler.

**Olaylar ve durum özeti:** `on_event` verilirse her hazırlık/gönderim adımı için bir
`AutopilotEvent(stage, message)` yayılır (Adım 9.6'nın canlı log paneli için).
`load_autopilot_stats(session)` ise VERİTABANINA HİÇ YAZMADAN o anki durumun saf bir özetini
döner (canlı sayaçlar için) — `run_autopilot()`'un döngüsünden TAMAMEN bağımsız, herhangi bir
zamanda çağrılabilir.

**Kalıcılık (kapanıp yeniden açma):** Bir teknokent kaynağı tamamen tarandığında
`Setting.discovery_state`'e `{"teknokent:<key>": {"done": true}}` yazılır (`_mark_source_done`)
ve bir daha ASLA yeniden taranmaz. Şirket/başvuru düzeyinde tekrar zaten var olan
mekanizmalarla önlenir — bu modül bunların HİÇBİRİNİ TEKRAR YAZMAZ, yalnızca çağırır:
  * `Company.domain` benzersizliği → aynı şirket iki kez eklenmez (`csv_import.add_company`,
    CLAUDE.md kural 4).
  * `Company.crawled_at` / `enricher.is_crawl_due()` → başarıyla taranmış bir şirket bir daha
    taranmaz (Adım 9.1/9.2).
  * `Application.test_sent_at` / `smtp_sender.check_send_allowed()` → test modunda aynı
    başvuru bir daha gönderilmez (Adım 9.1).
"""
import itertools
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

from sqlmodel import Session, select

from src.company_finder.enricher import enrich_company, is_crawl_due
from src.company_finder.mx_check import has_mx_record
from src.company_finder.site_crawler import CrawlResult, crawl_company
from src.company_finder.discovery import DiscoverySource, Fetcher, import_leads
from src.company_finder.discovery_summary import DiscoveryTally, format_discovery_summary
from src.company_finder.google_places import (
    GooglePlacesClient,
    GooglePlacesSource,
    monthly_limit,
    places_sources,
    requests_this_month,
)
from src.company_finder.osm_source import OsmSource
from src.company_finder.places_cost import PlacesCostEstimate, estimate_places_cost, load_places_pricing
from src.company_finder.source_selector import (
    SOURCE_GOOGLE_PLACES,
    SOURCE_OSM,
    SOURCE_TEKNOKENT,
    load_source_settings,
    select_sources,
)
from src.company_finder.teknokent_source import TEKNOKENT_SOURCES
from src.core import secrets
from src.core.cities import CityPreset, load_cities
from src.core.mail_providers import resolve_mail_connection
from src.core.blocklist import is_company_blocked
from src.core.models import Application, Company, STATUS_APPROVED, STATUS_QUEUED
from src.core.db import get_session
from src.core.custom_profession import resolve_profession
from src.core.professions import ProfessionPreset
from src.core.settings import (
    KEY_AUTOPILOT_CITY,
    KEY_DAILY_LIMIT,
    KEY_DISCOVERY_STATE,
    KEY_PAUSE_REASON,
    KEY_SENDING_PAUSED,
    KEY_TEST_MODE,
    MODE_AUTO,
    get_setting,
    set_setting,
)
from src.core.timeutil import istanbul_day_start_utc, now_istanbul, to_naive_utc
from src.cv_builder.application_builder import build_application
from src.cv_builder.profile_loader import MasterProfile
from src.inbox.service import InboxCheckResult, check_inbox
from src.sender.queue import next_application
from src.sender.rate_limiter import CODE_HOURLY_LIMIT, CODE_PAUSED, can_send_now, sent_timestamps_since
from src.sender.smtp_sender import (
    MailAccount,
    Transport,
    load_account,
    resolve_identity,
    smtp_transport,
)
from src.sender.worker import QueueRunResult, process_queue

logger = logging.getLogger(__name__)

#: Google Places API anahtarının kasadaki adı (`src/core/secrets.py`'deki `SERVICE_NAME` altında).
GOOGLE_PLACES_API_KEY_NAME = "google_places"

#: Bir keşif biriminde bir kaynaktan en fazla kaç yeni firma denenir (eklenen + zaten kayıtlı
#: olduğu için atlanan). Küçük tutulur ki "gönderimler arası bekleme" bundan daha uzun sürmesin.
DEFAULT_DISCOVER_BATCH_SIZE = 10

#: Gönderim penceresi kapalıyken (mesai dışı / günlük ya da saatlik limit) hazırlık birimleri
#: ("verimli bekleme" ruhuyla) yine de ilerler -- ama bazı kaynaklarda (ör. ODTÜ Teknokent: tek
#: istekle önbelleğe alınmış bir liste) hiç ağ maliyeti olmadığından hiçbir gecikme olmadan
#: saniyede onlarca kez tekrarlanabilir (gerçek logda 15 ms aralıklı "X firma denendi" tekrarı
#: görüldü). Pencere kapalıyken her ilerlemeden sonra bu kadar beklenir; log ve DB commit hızı
#: makul bir tempoya çekilir, ilerleme durmaz.
BLOCKED_PREP_PACE_SECONDS = 1.0

#: Gelen kutusu + bounce oranı ("2") en fazla bu kadar dakikada bir kontrol edilir. Genel
#: `KEY_INBOX_CHECK_MINUTES` ayarından (Faz 7.7, UI'ın kendi periyodik zamanlayıcısı) BİLİNÇLİ
#: olarak BAĞIMSIZ: o ayar "0 = kapalı" olabilir (kullanıcı UI'ın arka plan taramasını
#: istemeyebilir), ama Otopilot'un KENDİ güvenlik mekanizması (bounce oranı freni, Adım 9.4)
#: kapatılabilir bir şey değil -- ayrı, sabit bir aralık kullanır.
INBOX_CHECK_INTERVAL_MINUTES = 30

#: `_do_one_prep_unit()`'in döndürdüğü tür etiketleri (`AutopilotResult` sayaçları için).
PREP_INBOX_CHECKED = "inbox_checked"
PREP_PREPARED = "prepared"
PREP_CRAWLED = "crawled"
PREP_DISCOVERED = "discovered"

#: `AutopilotEvent.stage` değerleri.
STAGE_DISCOVER = "discover"
STAGE_CRAWL = "crawl"
STAGE_PREPARE = "prepare"
STAGE_INBOX = "inbox"
STAGE_SEND = "send"
STAGE_WAITING = "waiting"
STAGE_PAUSED = "paused"
STAGE_STOPPED = "stopped"
STAGE_IDLE = "idle"

#: Tüm iş tükendiğinde ("Bugünlük iş bitti") kullanılan Türkçe metnin ön eki -- testler ve
#: (ileride) Adım 9.6'nın arayüzü bu ifadeyi arayabilsin diye sabit tutulur.
IDLE_MESSAGE_PREFIX = "Bugünlük iş bitti"


@dataclass(frozen=True)
class AutopilotEvent:
    """`run_autopilot(on_event=...)`'e yayılan tek bir olay.

    `sender.worker.QueueEvent` ile aynı ilke: `message` ilgili `logger.info/warning`
    çağrısıyla AYNI Türkçe dizedir — arayüz (Adım 9.6) ve terminal hiçbir zaman farklı bir
    cümle görmemeli.
    """

    stage: str
    message: str


AutopilotEventCallback = Callable[[AutopilotEvent], None]


def _emit(on_event: Optional[AutopilotEventCallback], stage: str, message: str) -> None:
    if on_event is not None:
        on_event(AutopilotEvent(stage, message))


def _is_source_done(session: Session, source: DiscoverySource) -> bool:
    state = get_setting(session, KEY_DISCOVERY_STATE)
    return bool(state.get(source.state_key, {}).get("done"))


def _mark_source_done(session: Session, source: DiscoverySource) -> None:
    state = get_setting(session, KEY_DISCOVERY_STATE)  # get_setting derin kopya döner (bkz. Adım 9.1)
    state[source.state_key] = {"done": True}
    set_setting(session, KEY_DISCOVERY_STATE, state)


def resolve_discovery_sources(session: Session) -> list[DiscoverySource]:
    """Ayarlardaki meslek + şehirden bu çalıştırmada kullanılacak keşif kaynaklarını kurar.

    Seçim `source_selector.select_sources()`'undur (saf fonksiyon); burası yalnızca seçilen
    kimlikleri somut kaynaklara çevirir: teknokent → `TEKNOKENT_SOURCES`, osm → `OsmSource`
    (meslek + şehir başına tek kaynak), google_places → `GooglePlacesSource` listesi (meslek
    paketinin her arama kelimesi × ilin her ilçesi; ortak istemci = ortak aylık sayaç/tavan).

    Meslek ya da şehir HİÇ seçilmemişse (Ayarlar'a hiç dokunulmamış taze kurulum) seçici
    çalıştırılmaz ve eski davranış korunur: tüm teknokent kaynakları. Seçim yapılmışsa seçici
    belirler; hiçbir kaynak çıkmazsa boş liste döner (ön kontrol, veritabanında şirket varsa
    yine başlar).
    """
    profession = resolve_profession(session)
    city = get_setting(session, KEY_AUTOPILOT_CITY) or None
    if profession is None or city is None:
        logger.info("Meslek/şehir seçilmemiş; keşif eskisi gibi tüm teknokent kaynaklarını kullanıyor")
        return list(TEKNOKENT_SOURCES)
    selected = select_sources(profession, city, load_source_settings(session), load_cities())

    sources: list[DiscoverySource] = []
    for source_id in selected:
        if source_id == SOURCE_TEKNOKENT:
            sources.extend(TEKNOKENT_SOURCES)
        elif source_id == SOURCE_OSM:
            sources.append(OsmSource(profession, load_cities()[city]))
        elif source_id == SOURCE_GOOGLE_PLACES:
            sources.extend(_build_places_sources(session, profession, load_cities()[city]))
        else:
            logger.info(f"Keşif kaynağı {source_id!r} bilinmiyor, atlandı")
    return sources


def _build_places_sources(
    session: Session, profession: ProfessionPreset, city: CityPreset
) -> list[DiscoverySource]:
    """Meslek paketinin her arama kelimesi için (× ilin ilçeleri) Google Places kaynakları.

    Anahtar yoksa boş döner (seçici zaten anahtarsız seçmez; bu ikinci emniyet). Tüm kaynaklar
    AYNI `GooglePlacesClient`'ı paylaşır; istemcinin kendi oturumları çalıştırmanın motoruna bağlıdır.
    """
    api_key = secrets.get_api_key(GOOGLE_PLACES_API_KEY_NAME)
    if not api_key:
        logger.warning("Google Places seçili ama API anahtarı yok; bu kaynak atlandı")
        return []
    engine = session.get_bind()

    def session_factory():
        return get_session(engine)

    client = GooglePlacesClient(api_key, session_factory)
    built: list[DiscoverySource] = []
    for query in profession.search_queries_tr:  # kelime-öncelikli: önce ilk kelime tüm ilçelerde
        built.extend(places_sources(query, city, client, session_factory=session_factory))
    return built


@dataclass(frozen=True)
class SourcePreview:
    """"Başlat"tan önce gösterilen kaynak özeti (`build_source_preview`)."""

    profession_name: Optional[str]
    city_name: Optional[str]
    #: `source_selector` kimlikleri (teknokent / osm / google_places); meslek/şehir seçilmemişse
    #: eski davranış olan `[teknokent]`.
    source_ids: list[str]
    #: Yalnızca Google Places seçiliyse dolu.
    places: Optional[PlacesCostEstimate] = None


def build_source_preview(session: Session) -> SourcePreview:
    """Otopilot'un şu ayarlarla hangi kaynakları kullanacağını ve (Places açıksa) en kötü durum
    istek sayısı/maliyetini hesaplar. VERİTABANINA YAZMAZ, ağa ÇIKMAZ, Places istemcisi KURMAZ.

    Seçim `resolve_discovery_sources` ile AYNI kurallarla yapılır (meslek/şehir yoksa eski
    davranış: tüm teknokentler). Places'te "bekleyen arama" = henüz tamamlanmamış (kelime × ilçe)
    sayısı; bir kez tamamlanan arama `discovery_state`'te "done" olduğu için bir daha sayılmaz.
    """
    profession = resolve_profession(session)
    city_id = get_setting(session, KEY_AUTOPILOT_CITY) or None
    cities = load_cities()
    city = cities.get(city_id) if city_id else None
    if profession is None or city_id is None:
        return SourcePreview(
            profession.name_tr if profession else None,
            city.name_tr if city else None,
            [SOURCE_TEKNOKENT],
        )

    selected = select_sources(profession, city_id, load_source_settings(session), cities)
    places: Optional[PlacesCostEstimate] = None
    if SOURCE_GOOGLE_PLACES in selected and city is not None:
        idle_client = GooglePlacesClient("")  # hiçbir istek atılmaz: yalnızca kaynak listesi için
        searches = [
            source
            for query in profession.search_queries_tr
            for source in places_sources(query, city, idle_client)
        ]
        pending = [source for source in searches if not _is_source_done(session, source)]
        places = estimate_places_cost(
            keyword_count=len(profession.search_queries_tr),
            district_count=max(len(city.districts), 1),
            pending_searches=len(pending),
            used_this_month=requests_this_month(session),
            monthly_limit=monthly_limit(session),
            pricing=load_places_pricing(),
        )
    return SourcePreview(profession.name_tr, city.name_tr if city else None, selected, places)


def _warn_if_places_key_unused(discovery_sources: Sequence[DiscoverySource]) -> None:
    """Anahtar kayıtlı ama Google Places kaynağı seçilmediyse bunu loglar.

    Davranışı DEĞİŞTİRMEZ: Places yalnızca "Google Places kullan" düğmesi açıkken kullanılır; bu
    yalnızca "anahtarım var, neden kullanılmıyor" diye şaşırmasınlar diye bir log satırı.
    """
    if any(isinstance(source, GooglePlacesSource) for source in discovery_sources):
        return
    if secrets.get_api_key(GOOGLE_PLACES_API_KEY_NAME):
        logger.info(
            "Google Places API anahtarı kayıtlı ama Google Places bu çalıştırmada kullanılmıyor "
            "('Google Places kullan' kapalı ya da meslek/şehir seçilmemiş)."
        )


def _selected_city_name(session: Session) -> Optional[str]:
    city_id = get_setting(session, KEY_AUTOPILOT_CITY) or None
    city = load_cities().get(city_id) if city_id else None
    return city.name_tr if city else None


def _has_any_company(session: Session) -> bool:
    return session.exec(select(Company.id).limit(1)).first() is not None


def _can_send_now(session: Session, now: datetime):
    """`rate_limiter.can_send_now()`'ın test-modu-farkında kabuğu.

    `test_mode=get_setting(...)` geçilir: `can_send_now` o zaman günlük/saatlik sayımı
    `Application.sent_at` yerine `Application.test_sent_at`'ten yapar (bkz. `rate_limiter.py`) —
    bu sütun her gönderimden hemen sonra COMMIT edildiği için (`worker.py`, "(B) sonucu kalıcı
    yaz") dış döngü, `process_queue()`'nun ÖNCEKİ turlarında (hatta önceki `run_autopilot()`
    çalıştırmalarında) yapılmış test gönderimlerini de görür. `worker.process_queue()`'nun
    kendisi de AYNI parametreyle çağrılır — iki döngü hep aynı sayıyı görür, çift sayım olmaz.
    Gerçek modda (`test_mode=False`) `test_sent_at` sayıma hiç KARIŞMAZ: geçmiş test
    gönderimleri gerçek kotayı etkilemez (kural 5 yalnızca "her iki modda da zorunlu" der, modlar
    arası karışım gerektirmez).
    """
    test_mode = bool(get_setting(session, KEY_TEST_MODE))
    return can_send_now(session, now, test_mode=test_mode)


def _has_pending_send(session: Session) -> bool:
    """Otomatik modda gönderilmeyi BEKLEYEN bir başvuru var mı?

    Yalnızca SORAR — `sender/queue.next_application()` salt okunur bir SELECT'tir, hiçbir şeyi
    seçip işaretlemez/göndermez. Otopilot'un kendi seçim mantığı YOKTUR, tek doğru kaynak
    budur (CLAUDE.md kural 10, `sender/queue.is_auto_eligible`).
    """
    return next_application(session, auto_mode=True) is not None


def _run_preflight_check(
    session: Session,
    discovery_sources: Sequence[DiscoverySource],
    account: Optional[MailAccount],
) -> Optional[str]:
    """Otopilot'a başlamadan ÖNCE üç ön koşulu kontrol eder.

    Sıra bilinçli — en ucuz VE en kesin engelden en gevşeğine: acil durdur/güvenlik freni →
    Gmail hesabı → en az bir kaynak. Herhangi biri eksikse Türkçe sebebi döner (Otopilot
    HİÇ başlamaz, tek bir keşif/tarama/hazırlama denemesi bile yapmaz); hepsi tamamsa `None`.

    "En az bir kaynak": keşif kaynağı listesi boş OLABİLİR (ör. testler ya da ileride tek
    tek kaynak seçimi) — bu tek başına bir engel değildir, veritabanında ZATEN en az bir
    şirket kaydı varsa (tarama/hazırlama/gönderim için hâlâ iş var demektir) Otopilot yine de
    başlar.
    """
    if get_setting(session, KEY_SENDING_PAUSED):
        reason = get_setting(session, KEY_PAUSE_REASON) or "acil durdur/güvenlik freni açık"
        return f"Otopilot başlatılamadı — gönderim duraklatılmış: {reason}"
    if account is None:
        return (
            "Otopilot başlatılamadı — Mail uygulama şifresi kayıtlı değil "
            "(python scripts/set_mail_password.py)"
        )
    if not discovery_sources and not _has_any_company(session):
        return (
            "Otopilot başlatılamadı — keşfedilecek bir kaynak yok ve veritabanında hiç "
            "şirket kaydı yok"
        )
    return None


@dataclass
class _SourceCursor:
    """Bir keşif kaynağının BU ÇALIŞTIRMA içindeki canlı ilerleyişi (yalnızca bellekte).

    `iterator` süreç kapanınca kaybolur — BİLİNÇLİ bir sadeleştirme: bugünkü (teknokent)
    kaynakların (Google Places'in aksine) sayfa belirteci (page token) yok, kaldığı YERDEN devam etmenin
    bir yolu yok. Yarıda kalan bir kaynak bir sonraki açılışta BAŞTAN taranır; zaten eklenmiş
    şirketler `add_company()`'nin alan adı tekilleştirmesiyle (CLAUDE.md kural 4) sessizce
    atlanır — veri tekrarı YOKTUR, yalnızca bir miktar ağ isteği tekrarlanmış olur. Kaynak
    TAMAMEN bitirilirse (`done=True`, bkz. `_mark_source_done`) bu KALICIDIR, bir daha
    KENDİLİĞİNDEN denenmez.
    """

    source: DiscoverySource
    fetcher: Optional[Fetcher]
    sleeper: Callable[[float], None]
    iterator: Optional[Iterator] = None
    exhausted: bool = False


def _build_cursors(
    session: Session,
    sources: Sequence[DiscoverySource],
    *,
    fetcher: Optional[Fetcher],
    sleeper: Callable[[float], None],
) -> list[_SourceCursor]:
    """Henüz bitmemiş kaynaklar için bir imleç listesi kurar (bitmiş olanlar hiç eklenmez —
    `_advance_discovery` onlara asla dokunmaz, `fetcher` bile çağrılmaz)."""
    return [
        _SourceCursor(source=source, fetcher=fetcher, sleeper=sleeper)
        for source in sources
        if not _is_source_done(session, source)
    ]


def _advance_discovery(
    session: Session,
    cursors: list[_SourceCursor],
    batch_size: int,
    on_event: Optional[AutopilotEventCallback] = None,
    tally: Optional[DiscoveryTally] = None,
) -> bool:
    """Listedeki İLK bitmemiş kaynağı bir birim ilerletir. İlerleme oldu mu (True/False) döner.

    `tally` verilirse bulunan/atlanan firma sayıları tutulur ve TÜM kaynaklar tükenince tek bir
    "Keşif bitti: ..." özeti yayılır (`discovery_summary`).

    Round-robin YAPILMAZ — sıradaki her zaman listenin başındaki bitmemiş kaynaktır; kaynak
    sayısı küçük (bugün üç teknokent), bir kaynak bitene kadar ona devam etmek daha basit
    ve öngörülebilir.
    """
    for cursor in cursors:
        if cursor.exhausted:
            continue
        if cursor.iterator is None:
            cursor.iterator = cursor.source.discover(fetcher=cursor.fetcher, sleeper=cursor.sleeper)
        leads = list(itertools.islice(cursor.iterator, batch_size))
        added = skipped = 0
        if leads:
            report = import_leads(
                session,
                leads,
                company_source=cursor.source.company_source,
                label=cursor.source.name,
            )
            session.commit()
            added, skipped = report.added_count, report.skipped_count
        if tally is not None:
            tally.record(cursor.source.company_source, added=added, skipped=skipped)
        message = f"{cursor.source.name}: {len(leads)} firma denendi"
        if len(leads) < batch_size:
            # Üretici batch_size'dan az (ya da hiç) eleman verdi: tükendi.
            cursor.exhausted = True
            if getattr(cursor.source, "failed", False):
                # Kaynak hata verdi (ör. Overpass 504): bu çalıştırmada bırakılır ama "bitti"
                # yazılmaz; bir sonraki açılışta yeniden denenir.
                message = f"{cursor.source.name}: keşif başarısız oldu, sonra yeniden denenecek"
                reason = getattr(cursor.source, "failure_reason", None)
                if tally is not None:
                    tally.record_failure(
                        cursor.source.company_source,
                        f"{cursor.source.name} ({reason})" if reason else cursor.source.name,
                    )
                if getattr(cursor.source, "failure_stops_siblings", False):
                    siblings = [
                        other
                        for other in cursors
                        if other is not cursor
                        and not other.exhausted
                        and type(other.source) is type(cursor.source)
                    ]
                    for other in siblings:
                        other.exhausted = True  # "bitti" YAZILMAZ: sonraki açılışta denenir
                    if siblings:
                        message += f"; aynı türden kalan {len(siblings)} arama bu çalıştırmada atlandı"
            else:
                _mark_source_done(session, cursor.source)
                session.commit()
                message = f"{cursor.source.name}: keşif tamamlandı ({len(leads)} firma ile)"
        logger.info(message)
        _emit(on_event, STAGE_DISCOVER, message)
        if tally is not None and not tally.announced and all(c.exhausted for c in cursors):
            tally.announced = True
            summary = format_discovery_summary(tally)
            logger.info(summary)
            _emit(on_event, STAGE_DISCOVER, summary)
        return True
    return False


def _next_crawl_candidate(session: Session) -> Optional[Company]:
    """Sıradaki taranacak şirketi seçer: önce hiç taranmamışlar, sonra kotası dolan geçici
    arızalar arasında `is_crawl_due()` (3 günlük soğuma) geçenler. Bkz. `enricher.py`."""
    # Engelli alan adları (Adım 10.8b) taranmaz: o şirketle hiçbir etkileşim olmamalı.
    never_crawled = session.exec(
        select(Company).where(Company.crawled_at.is_(None)).order_by(Company.created_at)
    ).all()
    for candidate in never_crawled:
        if not is_company_blocked(session, candidate):
            return candidate

    retry_candidates = session.exec(
        select(Company).where(Company.crawl_failed.is_(True)).order_by(Company.crawled_at)
    ).all()
    for candidate in retry_candidates:
        if is_crawl_due(candidate) and not is_company_blocked(session, candidate):
            return candidate
    return None


def _crawl_one(
    session: Session,
    crawler: Callable[[str], CrawlResult],
    mx: Callable[..., Optional[bool]],
    on_event: Optional[AutopilotEventCallback] = None,
) -> bool:
    company = _next_crawl_candidate(session)
    if company is None:
        return False
    enrich_company(session, company, crawler=crawler, mx=mx)
    session.commit()
    message = f"{company.domain}: Otopilot taraması tamamlandı"
    logger.info(message)
    _emit(on_event, STAGE_CRAWL, message)
    return True


def _next_prepare_candidate(session: Session, exclude_ids: set[int]) -> Optional[Company]:
    """E-postası olan ve henüz başvurusu olmayan bir şirket seçer (`prepare_applications.py`
    ile aynı sorgu); `exclude_ids` bu ÇALIŞTIRMADA zaten denenip başarısız olanları dışlar."""
    statement = (
        select(Company)
        .where(Company.email.is_not(None))
        .where(Company.id.not_in(select(Application.company_id)))
        .order_by(Company.created_at)
    )
    if exclude_ids:
        statement = statement.where(Company.id.not_in(exclude_ids))
    # Engelli alan adları (Adım 10.8b) için başvuru HAZIRLANMAZ (boşa PDF/mail üretilmesin).
    for company in session.exec(statement):
        if not is_company_blocked(session, company):
            return company
    return None


def _prepare_one(
    session: Session,
    profile: MasterProfile,
    output_dir: Optional[Path],
    failed_ids: set[int],
    on_event: Optional[AutopilotEventCallback] = None,
) -> bool:
    company = _next_prepare_candidate(session, failed_ids)
    if company is None:
        return False
    try:
        build_application(session, company, profile, output_dir=output_dir)
        session.commit()
    except Exception:
        # Aynı şirkette sonsuz döngüye girmemek için (ör. bozuk profil -> her seferinde aynı
        # hata) bu çalıştırma boyunca bir daha DENENMEZ; bir sonraki `run_autopilot()`
        # çağrısında (bellek içi küme sıfırlanır) tekrar denenir.
        logger.exception(f"{company.domain}: başvuru hazırlanamadı")
        session.rollback()
        failed_ids.add(company.id)
        message = f"{company.domain}: başvuru hazırlanamadı"
    else:
        message = f"{company.domain}: başvuru hazırlandı"
        logger.info(message)
    _emit(on_event, STAGE_PREPARE, message)
    return True


@dataclass
class _InboxCheckGate:
    """Bu ÇALIŞTIRMA içinde gelen kutusunun en son NE ZAMAN denendiğini tutar (başarılı/
    başarısız fark etmeksizin) -- `src/ui/replies_tab.py::RepliesTab._last_auto_attempt_at`
    ile AYNI ilke (PLAN Faz 7.7 notu): kalıcı `Setting.inbox_last_check_at` yalnızca
    `check_inbox()` BAŞARIYLA bittiğinde yazılır; bir bağlantı hatasını her hazırlık biriminde
    yeniden denemekten (ve "verimli bekleme"yi boşa harcamaktan) korunmak için ayrı, bellek içi
    bir izleyici gerekir. Uygulama yeniden başlatılınca (yeni bir `run_autopilot()` çağrısı)
    sıfırlanır — bu BİLİNÇLİDİR, kalıcı olması gerekmiyor: taze bir açılış hemen bir kontrol
    hak ediyor.
    """

    last_attempt: Optional[datetime] = None

    def is_due(self, now: datetime) -> bool:
        return self.last_attempt is None or (now - self.last_attempt) >= timedelta(
            minutes=INBOX_CHECK_INTERVAL_MINUTES
        )

    def mark_attempted(self, now: datetime) -> None:
        self.last_attempt = now


def _check_inbox_one(
    session: Session,
    profile: MasterProfile,
    gate: _InboxCheckGate,
    now_utc: datetime,
    check_inbox_fn: Callable[[Session, MasterProfile], InboxCheckResult],
    on_event: Optional[AutopilotEventCallback] = None,
) -> bool:
    """Vakti geldiyse (30 dk'da bir) gelen kutusunu kontrol eder — bounce oranı güvenlik freni
    bunun İÇİNDE, otomatik olarak çalışır (`inbox.service.check_inbox` ->
    `sender.safety_brake.check_bounce_rate`, Adım 9.4), burada AYRICA çağrılmaz.

    Henüz vakti gelmediyse, hiç gönderim yapılmamışsa (check_inbox zaten bunu anlayıp
    `no_applications_sent=True` ile sessizce döner) ya da bağlantı hatası olursa `False`
    döner -- bu bir "iş kalmadı" sinyali DEĞİLDİR, yalnızca bu birimde somut bir ilerleme
    olmadığı anlamına gelir; hazırlık zinciri normal şekilde diğer adımlara devam eder.
    """
    if not gate.is_due(now_utc):
        return False
    gate.mark_attempted(now_utc)

    try:
        result = check_inbox_fn(session, profile)
    except Exception:
        logger.exception("Otopilot: gelen kutusu kontrolü başarısız oldu")
        return False

    if result.no_applications_sent:
        return False

    message = (
        f"Gelen kutusu kontrol edildi: {result.new_replies} yeni cevap, "
        f"{result.candidates} aday, {len(result.bounced_domains)} bounce"
    )
    logger.info(message)
    _emit(on_event, STAGE_INBOX, message)

    if result.bounce_rate_paused:
        logger.warning(result.bounce_rate_paused)
        _emit(on_event, STAGE_PAUSED, result.bounce_rate_paused)

    return True


def _do_one_prep_unit(
    session: Session,
    profile: MasterProfile,
    output_dir: Optional[Path],
    cursors: list[_SourceCursor],
    crawler: Callable[[str], CrawlResult],
    mx: Callable[..., Optional[bool]],
    discover_batch_size: int,
    failed_prepare_ids: set[int],
    inbox_gate: _InboxCheckGate,
    now_utc: datetime,
    check_inbox_fn: Callable[[Session, MasterProfile], InboxCheckResult],
    on_event: Optional[AutopilotEventCallback] = None,
    tally: Optional[DiscoveryTally] = None,
) -> Optional[str]:
    """Tek bir hazırlık birimi dener: gelen kutusu (vakti geldiyse) → hazırla → tara → keşfet.

    Sıra BİLİNÇLİ: gelen kutusu/bounce güvenlik kontrolü en yüksek öncelikte — bol miktarda
    şirket varken (yüzlerce keşif/tarama biriminin arkasında) yine de 30 dakikada bir mutlaka
    çalışması gerekiyor, sonraki iş yüküne bakılmaksızın. Sonra pipeline'ın gönderime en yakın
    ucu gelir: bekleyen bir şirketi hazırlamak yeni bir şirket eklemekten daha değerlidir (bir
    sonraki gönderim turunda hemen aday olur). Hiçbiri yapılamazsa `None` (pipeline tamamen
    boş).

    **Acil durdur/güvenlik freni açıksa HİÇBİR ŞEY yapmaz** (CLAUDE.md kural 5 — "anında
    durur", yalnızca gönderim değil, hazırlık da). Bu kontrol burada doğrudan `sending_paused`
    ayarını okur (`can_send_now()`'ın kendisinin de yaptığı gibi) — her hazırlık birimi için
    tarih/limit hesaplamak gereksiz maliyet olurdu; asıl "hangi ANDA tam durdurma yapılsın"
    kararı `run_autopilot()`'un ana döngüsünde `SendWindow.code`'a bakılarak verilir.
    """
    if get_setting(session, KEY_SENDING_PAUSED):
        return None
    if _check_inbox_one(session, profile, inbox_gate, now_utc, check_inbox_fn, on_event):
        return PREP_INBOX_CHECKED
    if _prepare_one(session, profile, output_dir, failed_prepare_ids, on_event):
        return PREP_PREPARED
    if _crawl_one(session, crawler, mx, on_event):
        return PREP_CRAWLED
    if _advance_discovery(session, cursors, discover_batch_size, on_event, tally):
        return PREP_DISCOVERED
    return None


def _make_efficient_sleeper(
    prep_unit: Callable[[], Optional[str]],
    real_sleeper: Callable[[float], None],
    monotonic: Callable[[], float],
) -> Callable[[float], None]:
    """`process_queue(sleeper=...)`'a verilir: uyumak yerine ÖNCE bir hazırlık birimi
    çalıştırır, ondan ARTA KALAN süre kadar gerçekten bekler ("verimli bekleme").

    İş, beklemeden uzun sürdüyse (yavaş bir ağ isteği) hiç ek beklenmez — o süre zaten
    "boşa geçmeyecek" şekilde kullanılmış oldu.
    """

    def _sleeper(seconds: float) -> None:
        started = monotonic()
        prep_unit()
        elapsed = monotonic() - started
        remaining = seconds - elapsed
        if remaining > 0:
            real_sleeper(remaining)

    return _sleeper


@dataclass
class AutopilotResult:
    """`run_autopilot()`'un döndürdüğü özet (Adım 9.6'nın canlı sayaçları için hazırlanmıştır)."""

    discovery_rounds: int = 0
    crawled: int = 0
    prepared: int = 0
    inbox_checks: int = 0
    sent_count: int = 0
    refused_count: int = 0
    failed_count: int = 0
    sent_domains: list[str] = field(default_factory=list)
    #: True yalnızca acil durdur/güvenlik freni yüzünden durulduysa (`SendWindow.code ==
    #: CODE_PAUSED`) — kullanıcı durdurma veya "iş kalmadı" bu alanı DEĞİŞTİRMEZ.
    paused: bool = False
    stop_reason: Optional[str] = None

    def _absorb_prep(self, kind: Optional[str]) -> None:
        if kind == PREP_CRAWLED:
            self.crawled += 1
        elif kind == PREP_PREPARED:
            self.prepared += 1
        elif kind == PREP_DISCOVERED:
            self.discovery_rounds += 1
        elif kind == PREP_INBOX_CHECKED:
            self.inbox_checks += 1

    def _absorb_send(self, send_result: QueueRunResult) -> None:
        self.sent_count += send_result.sent_count
        self.refused_count += send_result.refused_count
        self.failed_count += send_result.failed_count
        self.sent_domains.extend(send_result.sent_domains)


@dataclass(frozen=True)
class AutopilotStats:
    """`load_autopilot_stats()`'in döndürdüğü anlık özet (Adım 9.6'nın canlı sayaçları için).

    `run_autopilot()`'un döngüsünden TAMAMEN bağımsız — hiçbir yan etkisi yok, veritabanına
    hiç YAZMAZ, herhangi bir anda (Otopilot çalışırken de, çalışmazken de) çağrılabilir.
    """

    companies_found: int
    companies_with_email: int
    role_address_count: int
    person_address_count: int
    sent_today: int
    daily_limit: int
    queued_count: int
    #: Şu an gönderim yapılabiliyorsa `now` (verilen `clock()`); yapılamıyorsa bir sonraki
    #: pencerenin tahmini başlangıcı. Acil durdur/güvenlik freni açıksa `None` (elle müdahale
    #: gerekir, bir "süre" hesaplanamaz) — bkz. `next_send_reason`.
    next_send_at: Optional[datetime]
    #: Şu an gönderilebiliyorsa `None`; değilse Türkçe sebep (`rate_limiter.SendWindow.reason`).
    next_send_reason: Optional[str] = None


def load_autopilot_stats(
    session: Session, *, clock: Callable[[], datetime] = now_istanbul
) -> AutopilotStats:
    """Otopilot'un o anki durumunu özetler: bulunan şirket, e-postası olan, rol/kişi adresli,
    bugün giden/limit, kuyrukta bekleyen, sıradaki gönderim zamanı."""
    companies = session.exec(select(Company)).all()
    companies_with_email = sum(1 for company in companies if company.email)
    role_address_count = sum(1 for company in companies if company.email_is_role is True)
    person_address_count = sum(
        1 for company in companies if company.email and company.email_is_role is False
    )

    now = clock()
    test_mode = bool(get_setting(session, KEY_TEST_MODE))
    sent_today = len(
        sent_timestamps_since(session, istanbul_day_start_utc(now), test_mode=test_mode)
    )
    daily_limit = get_setting(session, KEY_DAILY_LIMIT)

    queued_count = len(
        session.exec(
            select(Application.id).where(Application.status.in_((STATUS_QUEUED, STATUS_APPROVED)))
        ).all()
    )

    window = _can_send_now(session, now)
    if window.allowed:
        next_send_at: Optional[datetime] = now
        next_send_reason = None
    elif window.code == CODE_PAUSED:
        next_send_at = None
        next_send_reason = window.reason
    else:
        next_send_at = now + timedelta(seconds=window.wait_seconds)
        next_send_reason = window.reason

    return AutopilotStats(
        companies_found=len(companies),
        companies_with_email=companies_with_email,
        role_address_count=role_address_count,
        person_address_count=person_address_count,
        sent_today=sent_today,
        daily_limit=daily_limit,
        queued_count=queued_count,
        next_send_at=next_send_at,
        next_send_reason=next_send_reason,
    )


def run_autopilot(
    session: Session,
    profile: MasterProfile,
    *,
    output_dir: Optional[Path] = None,
    discovery_sources: Optional[Sequence[DiscoverySource]] = None,
    discover_batch_size: int = DEFAULT_DISCOVER_BATCH_SIZE,
    fetcher: Optional[Fetcher] = None,
    discovery_sleeper: Callable[[float], None] = time.sleep,
    crawler: Callable[[str], CrawlResult] = crawl_company,
    mx: Callable[..., Optional[bool]] = has_mx_record,
    check_inbox_fn: Callable[[Session, MasterProfile], InboxCheckResult] = check_inbox,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = now_istanbul,
    rng=None,
    transport: Transport = smtp_transport,
    account: Optional[MailAccount] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    on_event: Optional[AutopilotEventCallback] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> AutopilotResult:
    """Otopilot'un TEK çalıştırması: ön kontrol, sonra keşif → tara → hazırla → gönder,
    sırayla ve tekrar tekrar.

    `discovery_sources` verilmezse ayarlardaki meslek + şehire göre `resolve_discovery_sources()`
    ile seçilir (testler/çağıranlar açıkça bir liste — boş olabilir — vererek bunu atlayabilir).

    Durma sebepleri (bkz. modül docstring'i): ön koşullardan biri eksik (hiç başlamaz),
    yapılacak iş kalmadı ("Bugünlük iş bitti"), `should_stop()` True döndü, ya da acil
    durdur/güvenlik freni açıldı (`AutopilotResult.paused=True`).

    **Gönderim HER ZAMAN otomatik moddadır (`MODE_AUTO`)** — genel `send_mode` ayarı (Onay
    Kuyruğu sekmesinin "Gönderimi başlat" düğmesinin kullandığı ayar) buradan ETKİLENMEZ, iki
    akış birbirinden BAĞIMSIZDIR: Otopilot'un tanımı zaten "tek 'Başlat' ile uçtan uca" (PLAN.md
    Faz 9 girişi), manuel onay adımı içermez. `MODE_AUTO` demek `sender/queue.is_auto_eligible()`
    (rol adresi VE MX kaydı doğrulanmış) geçen `queued` başvurular + kullanıcının Onay
    Kuyruğu'ndan ayrıca elle onayladığı (`approved`) başvurular gönderilir demektir (CLAUDE.md
    kural 10, `src/sender/queue.py`) — bu seçim burada YENİDEN YAZILMAZ, doğrudan
    `process_queue()`'ya devredilir.

    `account` verilmezse Gmail şifresi kasadan bir kez okunur (döngüden ÖNCE, her gönderimde
    kasa açmamak için — `process_queue` ile aynı gerekçe).
    """
    result = AutopilotResult()

    if account is None:
        identity = resolve_identity(session, profile)
        account = load_account(identity, connection=resolve_mail_connection(session))

    if discovery_sources is None:
        discovery_sources = resolve_discovery_sources(session)

    preflight_reason = _run_preflight_check(session, discovery_sources, account)
    if preflight_reason is not None:
        logger.warning(preflight_reason)
        result.stop_reason = preflight_reason
        result.paused = get_setting(session, KEY_SENDING_PAUSED)
        _emit(on_event, STAGE_STOPPED, preflight_reason)
        return result

    _warn_if_places_key_unused(discovery_sources)
    cursors = _build_cursors(session, discovery_sources, fetcher=fetcher, sleeper=discovery_sleeper)
    failed_prepare_ids: set[int] = set()
    inbox_gate = _InboxCheckGate()
    tally = DiscoveryTally(city_name=_selected_city_name(session))

    def _prep_unit() -> Optional[str]:
        now_utc = to_naive_utc(clock())
        kind = _do_one_prep_unit(
            session,
            profile,
            output_dir,
            cursors,
            crawler,
            mx,
            discover_batch_size,
            failed_prepare_ids,
            inbox_gate,
            now_utc,
            check_inbox_fn,
            on_event,
            tally,
        )
        result._absorb_prep(kind)
        return kind

    logger.info("Otopilot başlıyor")

    # Pencere kapalıyken (mesai dışı / günlük ya da saatlik limit) `process_queue()` yine de
    # çağrılırsa kendi `can_send_now()` kontrolünü YENİDEN yapıp anında aynı "gönderim
    # başlıyor"/"gönderim durdu" çiftini loglar -- pencere değişmediği sürece bu, her hazırlık
    # biriminde (saniyede onlarca kez) tekrarlanırdı. Pencere zaten kapalı olduğu BİLİNİYORSA
    # `process_queue()` hiç çağrılmaz; aynı sebep tekrar etmediği sürece tek satır yeter.
    last_blocked_reason: Optional[str] = None

    while True:
        window = _can_send_now(session, clock())
        if window.code == CODE_PAUSED:
            result.stop_reason = window.reason
            result.paused = True
            _emit(on_event, STAGE_PAUSED, window.reason)
            break
        if should_stop is not None and should_stop():
            result.stop_reason = "kullanıcı Otopilot'u durdurdu"
            _emit(on_event, STAGE_STOPPED, result.stop_reason)
            break

        if window.allowed:
            queue_on_event = (
                (lambda event: _emit(on_event, STAGE_SEND, event.message)) if on_event is not None else None
            )
            send_result = process_queue(
                session,
                profile,
                mode=MODE_AUTO,
                max_count=None,
                sleeper=_make_efficient_sleeper(_prep_unit, sleeper, monotonic),
                clock=clock,
                rng=rng,
                transport=transport,
                account=account,
                should_stop=should_stop,
                on_event=queue_on_event,
            )
            result._absorb_send(send_result)
            last_blocked_reason = None
            send_stop_reason = send_result.stop_reason

            # process_queue kendi içinde de duraklatmış olabilir (art arda hata / kota — Adım
            # 9.4); bir sonraki hazırlık biriminden ÖNCE fark edilmeli, "anında durur" (metne
            # değil `code`'a bakılarak, bkz. modül docstring'i).
            window = _can_send_now(session, clock())
            if window.code == CODE_PAUSED:
                result.stop_reason = window.reason
                result.paused = True
                _emit(on_event, STAGE_PAUSED, window.reason)
                break
            if should_stop is not None and should_stop():
                result.stop_reason = "kullanıcı Otopilot'u durdurdu"
                _emit(on_event, STAGE_STOPPED, result.stop_reason)
                break
        else:
            # Pencerenin kapalı olduğu ZATEN biliniyor (yukarıda hesaplandı) -- process_queue()
            # çağrılırsa yalnızca aynı sonucu tekrar hesaplayıp aynı satırı tekrar loglardı.
            if window.reason != last_blocked_reason:
                message = f"gönderim bekliyor — {window.reason}"
                logger.info(message)
                _emit(on_event, STAGE_WAITING, message)
                last_blocked_reason = window.reason
            send_stop_reason = window.reason

        progressed = _prep_unit() is not None
        if progressed:
            if not window.allowed:
                # Pencere kapalıyken hazırlığın ilerlemesi iyi ("verimli bekleme"), ama ağ
                # maliyeti olmayan bir kaynakta (ör. ODTÜ Teknokent) hiç gecikme olmadan
                # saniyede onlarca kez tekrarlanabilir -- bkz. `BLOCKED_PREP_PACE_SECONDS`.
                sleeper(BLOCKED_PREP_PACE_SECONDS)
            continue  # ilerleme oldu (keşif/tara/hazırla/gelen kutusu) -- tekrar dene

        # Hazırlık da bir iş bulamadı. Saatlik limit yüzünden engellenmişsek VE gönderilmeyi
        # bekleyen bir başvuru varsa (yapacak başka bir şey kalmadığı için) pencere açılana
        # kadar BEKLE, sonra devam et -- günlük kota/mesai saati dışında bu YAPILMAZ (saatlerce
        # sürebilir, senkron beklemek yerine "Bugünlük iş bitti" ile durulur).
        if window.code == CODE_HOURLY_LIMIT and _has_pending_send(session):
            message = (
                f"saatlik gönderim limiti doldu, hazırlık da tükendi; pencere açılana kadar "
                f"bekleniyor — {window.reason}"
            )
            logger.info(message)
            _emit(on_event, STAGE_WAITING, message)
            # +1 sn: rate_limiter'ın saatlik pencere kontrolü sınırda KAPSAYICI ("stamp >=
            # hour_start_naive") -- tam `wait_seconds` kadar beklemek pencerenin TAM sınırına
            # düşürür, bir sonraki kontrol hâlâ "dolu" görür ve `wait_seconds` o noktada 0'a
            # yuvarlanır (sonsuz, hiç ilerlemeyen bir döngü). Bir saniyelik pay sınırı kesin
            # olarak geçer.
            sleeper(window.wait_seconds + 1)
            continue

        result.stop_reason = f"{IDLE_MESSAGE_PREFIX} — {send_stop_reason}"
        _emit(on_event, STAGE_IDLE, result.stop_reason)
        break

    logger.info(
        f"Otopilot durdu — {result.stop_reason} (gelen kutusu: {result.inbox_checks}, keşif "
        f"turu: {result.discovery_rounds}, taranan: {result.crawled}, hazırlanan: "
        f"{result.prepared}, gönderilen: {result.sent_count})"
    )
    return result
