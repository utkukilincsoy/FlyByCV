"""Anahtarsız (API anahtarı/kredi kartı GEREKTİRMEYEN) teknokent firma listesi kaynakları
(Faz 9.2 — bkz. PLAN.md "Kaynak sırası bilinçli" notu: bu kaynaklar Google Places'ten (9.3)
önce gelir, kullanıcı hiçbir hesap açmadan Otopilot'u kullanabilsin diye).

Ankara'daki teknokentlerin genel firma rehberi sayfalarını tarar. Her kaynağın HTML yapısı
farklı olduğu için ayrı bir `iter_*` üretici (generator) fonksiyonu var; ortak paydaları
`TeknokentLead`. Bu modül veritabanına hiç DOKUNMAZ (site_crawler.py ile aynı ilke) —
yalnızca (ad, site, e-posta) üçlülerini toplar. Veritabanına yazmak `import_teknokent_leads()`
işi, o da mevcut `csv_import.add_company()` doğrulama/tekilleştirme çekirdeğini yeniden
kullanır (alan adı çözümleme, rol adresi tespiti, aynı alan adı kontrolü BURADA TEKRAR
YAZILMAZ).

Gerçek sitelerde incelenip 2026-09-29'da ÇALIŞTIĞI doğrulanan üç kaynak:
* **ODTÜ Teknokent** — tek sayfalık statik tablo, her satırda ad + site. En basit/en
  güvenilir kaynak: TEK istekle ~400+ firma (ad+site birlikte) döner.
* **OSTİM Teknopark** — liste sayfası (ad + detay linki, TEK istek, ~180 firma) + firma
  başına bir detay sayfası (opsiyonel site alanı, Cloudflare ile gizlenmiş e-posta —
  `email_extractor.decode_cfemail()` ile çözülür).
* **Hacettepe Teknokent** — sektöre göre 15 ayrı liste sayfası (sabit URL listesi, bu
  modülde `_HACETTEPE_CATEGORY_SLUGS`) + firma başına bir detay sayfası (düz metin e-posta,
  site alanı doldurulmamışsa "d-none" ile gizleniyor).

OSTİM/Hacettepe'de bir firmanın e-postası da yoksa (nadir) `TeknokentLead.email` ve
`.website` ikisi de `None` kalır; `import_teknokent_leads()` böyle bir kaydı `add_company()`
üzerinden "site veya e-posta gerekli" diyerek atlar — burada AYRICA elenmez, tek bir kural
kaynağı (`csv_import._build_company`) kalsın diye.

Denenip ÇALIŞMADIĞI için EKLENMEYEN kaynaklar (bkz. PLAN.md Adım 9.2 notu):
* **Bilkent Cyberpark** — TLS sertifika zinciri eksik (sunucu ara sertifikayı göndermiyor,
  `openssl s_client`: "unable to verify the first certificate"); ayrıca firma listesi
  JavaScript ile dolduruluyor, statik HTML boş.
* **Ankara (Üniversitesi) Teknokent** (firmarehberi.ankarateknokent.com) — firma rehberi
  tamamen JavaScript/AJAX aramasıyla dolduruluyor, filtre uygulanmadan "There are no
  listings matching your search" gösteriyor.
* **Gazi Teknopark** — site tek sayfalık bir JavaScript uygulaması (SPA), statik HTML'de
  hiç firma verisi yok.
* **TOBB ETÜ Teknokent** — tahmin edilen alan adı (`tobbteknokent.com.tr`) çözümlenmiyor
  (DNS hatası); doğru adres bulunamadı, eklenmedi.

`fetcher`/`sleeper` enjekte edilebilir (`site_crawler.py` ile aynı kalıp): testler ağa hiç
çıkmadan sahte HTML verir. Varsayılan HTTP istemcisi `site_crawler`'daki ile aynı nazik
gecikmeyi (`MIN_DELAY_SECONDS`–`MAX_DELAY_SECONDS`) ve `USER_AGENT`'ı kullanır — bu
sitelerin robots.txt'si YOK (404) ya da her şeye izin veriyor (kontrol edildi), ama yine de
istekler arasında rastgele beklenir; onlarca (Hacettepe'de yüzlerce) firma detay sayfası tek
seferde art arda istenmeyecek kadar kibar davranılmalı.
"""
import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, Iterator, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from sqlmodel import Session

from src.company_finder.csv_import import SOURCE_TEKNOKENT, ImportReport
from src.company_finder.discovery import Fetcher, import_leads
from src.company_finder.email_extractor import decode_cfemail
from src.company_finder.site_crawler import MAX_DELAY_SECONDS, MIN_DELAY_SECONDS, USER_AGENT

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 15.0

#: Hacettepe Teknokent "Firma Rehberi" menüsündeki sabit sektör kategorileri (site
#: içeriğinden alındı, 2026-09-29). Sayfada bu listeyi üreten bir API yok; teknokentin kendi
#: menü yapısı olduğu için nadiren değişir. `firma_rehberi/<slug>` yoluna eklenir.
_HACETTEPE_CATEGORY_SLUGS: tuple[str, ...] = (
    "bilgisayar_ve_iletisim_teknolojileri-16",
    "danismanlik-18",
    "diger-30",
    "elektronik-17",
    "enerji-19",
    "gida_sanayi-20",
    "insaat_muhendislik_mimarlik-21",
    "kimya_kozmetik_temizlik-22",
    "madencilik-23",
    "medya_iletisim-24",
    "otomotiv_makine-25",
    "saglik_ilac_medikal-26",
    "savunma_sanayi_havacilik-27",
    "telekomunikasyon-28",
    "yazilim-29",
)

ODTU_LIST_URL = "https://www.odtuteknokent.com.tr/tr/firmalar/tum-firmalar.php"
OSTIM_LIST_URL = "https://ostimteknopark.com.tr/tr/firmalar-119"
HACETTEPE_BASE_URL = "https://www.hacettepeteknokent.com.tr"


@dataclass(frozen=True)
class TeknokentLead:
    """Bir teknokent firma rehberinden çıkarılan tek bir aday.

    `website`/`email` biçim olarak DOĞRULANMAMIŞTIR (ham HTML metni) —
    `import_teknokent_leads` bunları `csv_import.add_company()`'ye olduğu gibi geçirir, biçim
    doğrulaması/normalizasyonu orada (tek kaynaktan) yapılır.
    """

    name: str
    website: Optional[str] = None
    email: Optional[str] = None
    city: str = "Ankara"
    teknokent: str = ""  # Türkçe görünen ad, rapor/log için


class _HttpxTextFetcher:
    """`site_crawler._HttpxFetcher` ile aynı fikir: bir üretici fonksiyonun tüm istekleri
    boyunca TEK bir bağlantı havuzu kullanılır (yüzlerce ayrı detay sayfası isteği için
    her seferinde yeni bir istemci açmak gereksiz yavaş olurdu)."""

    def __init__(self) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
        )

    def __call__(self, url: str) -> str:
        response = self._client.get(url)
        response.raise_for_status()
        return response.text

    def close(self) -> None:
        self._client.close()


class _PoliteFetcher:
    """`site_crawler._RequestBudget` ile aynı fikir: SADECE ilk istekten önce beklemez, ondan
    sonraki HER istekten (liste sayfası -> ilk detay sayfası dahil) önce rastgele bekler.

    Bu, her `iter_*` üreticisinin TEK bir örneğini oluşturup TÜM isteklerinde (liste + her
    detay sayfası) kullanması gerektiği anlamına gelir -- döngü başına "ilk mi" sayması YETMEZ,
    çünkü listeleme isteği zaten önce yapılmış oluyor (bkz. gerçek testte bulunan hata: liste
    sayfası ile TEK firmanın detay sayfası arasında hiç bekleme olmuyordu).
    """

    def __init__(self, fetcher: Fetcher, sleeper: Callable[[float], None]) -> None:
        self._fetcher = fetcher
        self._sleeper = sleeper
        self._made_request = False

    def __call__(self, url: str) -> str:
        if self._made_request:
            self._sleeper(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
        self._made_request = True
        return self._fetcher(url)


def iter_odtu_teknokent(
    *, fetcher: Optional[Fetcher] = None, sleeper: Callable[[float], None] = time.sleep
) -> Iterator[TeknokentLead]:
    """ODTÜ Teknokent'in tek sayfalık "Tüm Firmalar" tablosunu tarar (TEK istek).

    Her satır zaten (ad, site) çifti olarak geliyor -- ikinci bir sayfa ziyareti gerekmiyor,
    bu üç kaynağın en ucuzu/en güvenilir olanı.
    """
    own_fetcher = _HttpxTextFetcher() if fetcher is None else None
    budget = _PoliteFetcher(own_fetcher or fetcher, sleeper)
    try:
        html = budget(ODTU_LIST_URL)
    except Exception as exc:
        logger.warning(f"ODTÜ Teknokent firma listesi alınamadı: {exc!r}")
        return
    finally:
        if own_fetcher is not None:
            own_fetcher.close()

    soup = BeautifulSoup(html, "html.parser")
    for row in soup.select("table tbody tr"):
        cells = row.find_all("td")
        if len(cells) < 1:
            continue
        name = cells[0].get_text(strip=True)
        if not name:
            continue
        website = None
        if len(cells) >= 2:
            link = cells[1].find("a", href=True)
            if link is not None:
                website = link["href"].strip() or None
        yield TeknokentLead(name=name, website=website, teknokent="ODTÜ Teknokent")


def iter_ostim_teknopark(
    *, fetcher: Optional[Fetcher] = None, sleeper: Callable[[float], None] = time.sleep
) -> Iterator[TeknokentLead]:
    """OSTİM Teknopark: liste sayfasından (ad, detay linki) toplar, sonra HER firmanın detay
    sayfasını ziyaret edip opsiyonel site alanını ve Cloudflare ile gizlenmiş e-postayı çözer.

    Liste sayfası kendisi site/e-posta VERMİYOR (yalnızca ad + detay linki) -- bu yüzden detay
    sayfası ziyareti burada, tek sayfalık ODTÜ kaynağının aksine, ZORUNLU.
    """
    own_fetcher = _HttpxTextFetcher() if fetcher is None else None
    budget = _PoliteFetcher(own_fetcher or fetcher, sleeper)
    try:
        try:
            listing_html = budget(OSTIM_LIST_URL)
        except Exception as exc:
            logger.warning(f"OSTİM Teknopark firma listesi alınamadı: {exc!r}")
            return

        soup = BeautifulSoup(listing_html, "html.parser")
        seen_urls: set[str] = set()
        cards: list[tuple[str, str]] = []  # (ad, detay_url)
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "/company/" not in href:
                continue
            absolute = urljoin(OSTIM_LIST_URL, href)
            if absolute in seen_urls:
                continue
            title_tag = link.find(class_="title")
            name = (title_tag or link).get_text(strip=True)
            if not name:
                continue
            seen_urls.add(absolute)
            cards.append((name, absolute))

        for name, detail_url in cards:
            try:
                detail_html = budget(detail_url)
            except Exception as exc:
                logger.debug(f"{detail_url}: OSTİM firma detayı alınamadı ({exc!r}), atlandı")
                continue
            website, email = _parse_ostim_detail(detail_html)
            yield TeknokentLead(
                name=name, website=website, email=email, teknokent="OSTİM Teknopark"
            )
    finally:
        if own_fetcher is not None:
            own_fetcher.close()


def _parse_ostim_detail(html: str) -> tuple[Optional[str], Optional[str]]:
    """OSTİM firma detay sayfasından (site, e-posta) çıkarır.

    İkisi de `div.location` içindeki ilgili ikonun (`bi-envelope` / `bi-link-45deg`) EBEVEYN
    `<li>`'sine göre bulunur -- ikon sınıfı yerine "ilk data-cfemail" ya da "ilk http linki"
    almak yanlış olurdu: bazı firmalar site alanına yanlışlıkla bir e-posta girmiş
    (`https://team@example.com` gibi), o da ayrı bir data-cfemail üretiyor.
    """
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("div", class_="location") or soup

    website: Optional[str] = None
    website_icon = container.find("i", class_="bi-link-45deg")
    if website_icon is not None:
        item = website_icon.find_parent("li")
        link = item.find("a", href=True) if item else None
        if link is not None:
            candidate = link["href"].strip()
            # Gerçek örnek: bir firma site alanına yanlışlıkla e-posta girmiş
            # ("https://team@example.com") -- normal bir site URL'sinde "@" olmaz. Bu geçseydi
            # `_resolve_domain()` (csv_import.py) alan adı yerine "team@example.com" üretip
            # firmayı -- geçerli e-postası olsa bile -- "alan adı çözülemedi" diye reddederdi.
            website = candidate if candidate and "@" not in candidate else None

    email: Optional[str] = None
    email_icon = container.find("i", class_="bi-envelope")
    if email_icon is not None:
        item = email_icon.find_parent("li")
        cf_tag = item.find(attrs={"data-cfemail": True}) if item else None
        if cf_tag is not None:
            email = decode_cfemail(cf_tag["data-cfemail"])

    return website, email


def iter_hacettepe_teknokent(
    *, fetcher: Optional[Fetcher] = None, sleeper: Callable[[float], None] = time.sleep
) -> Iterator[TeknokentLead]:
    """Hacettepe Teknokent: 15 sabit sektör kategorisi sayfasından (ad, detay linki) toplar
    (bir firma birden çok kategoride görünebilir -- detay linkine göre teklenir), sonra HER
    firmanın detay sayfasını ziyaret edip düz metin e-postayı/opsiyonel siteyi okur.
    """
    own_fetcher = _HttpxTextFetcher() if fetcher is None else None
    budget = _PoliteFetcher(own_fetcher or fetcher, sleeper)
    try:
        seen_urls: set[str] = set()
        cards: list[tuple[str, str]] = []  # (ad, detay_url)
        for slug in _HACETTEPE_CATEGORY_SLUGS:
            category_url = f"{HACETTEPE_BASE_URL}/tr/firma_rehberi/{slug}"
            try:
                category_html = budget(category_url)
            except Exception as exc:
                logger.warning(f"{category_url}: Hacettepe Teknokent kategori sayfası alınamadı ({exc!r}), atlandı")
                continue

            soup = BeautifulSoup(category_html, "html.parser")
            for link in soup.select("div.firma_adi a[href]"):
                absolute = urljoin(HACETTEPE_BASE_URL, link["href"])
                if absolute in seen_urls:
                    continue
                name = link.get_text(strip=True)
                if not name:
                    continue
                seen_urls.add(absolute)
                cards.append((name, absolute))

        for name, detail_url in cards:
            try:
                detail_html = budget(detail_url)
            except Exception as exc:
                logger.debug(f"{detail_url}: Hacettepe firma detayı alınamadı ({exc!r}), atlandı")
                continue
            website, email = _parse_hacettepe_detail(detail_html)
            yield TeknokentLead(
                name=name, website=website, email=email, teknokent="Hacettepe Teknokent"
            )
    finally:
        if own_fetcher is not None:
            own_fetcher.close()


def _parse_hacettepe_detail(html: str) -> tuple[Optional[str], Optional[str]]:
    """Hacettepe firma detay sayfasından (site, e-posta) çıkarır.

    Her alan `<div class="firma_detay_bilgi ..."><i class="fa-solid fa-ICON"></i> METİN</div>`
    biçiminde; alan boşsa div `d-none` sınıfı taşıyor (gizli). Site ikonu `fa-arrow-pointer`,
    e-posta ikonu `fa-square-envelope`.
    """

    def _field(soup: BeautifulSoup, icon_class: str) -> Optional[str]:
        icon = soup.find("i", class_=icon_class)
        if icon is None:
            return None
        row = icon.find_parent("div", class_="firma_detay_bilgi")
        if row is None or "d-none" in (row.get("class") or []):
            return None
        text = row.get_text(strip=True)
        return text or None

    soup = BeautifulSoup(html, "html.parser")
    website = _field(soup, "fa-arrow-pointer")
    email = _field(soup, "fa-square-envelope")
    return website, email


#: `Setting.discovery_state` anahtar öneki. Biçim KALICIDIR (`teknokent:<key>`): değişirse
#: bitmiş kaynaklar yeniden taranırdı.
DISCOVERY_STATE_PREFIX = "teknokent:"


@dataclass(frozen=True)
class TeknokentSource:
    """`discovery.DiscoverySource` arayüzünü karşılar (Adım 10.6d)."""

    key: str
    name: str
    discover: Callable[..., Iterator[TeknokentLead]]

    @property
    def state_key(self) -> str:
        return f"{DISCOVERY_STATE_PREFIX}{self.key}"

    @property
    def company_source(self) -> str:
        return SOURCE_TEKNOKENT


#: Yalnızca gerçek sitede ÇALIŞTIĞI doğrulanmış kaynaklar (modül docstring'ine bakınız).
TEKNOKENT_SOURCES: tuple[TeknokentSource, ...] = (
    TeknokentSource("odtu", "ODTÜ Teknokent", iter_odtu_teknokent),
    TeknokentSource("ostim", "OSTİM Teknopark", iter_ostim_teknopark),
    TeknokentSource("hacettepe", "Hacettepe Teknokent", iter_hacettepe_teknokent),
)


def import_teknokent_leads(session: Session, leads: Iterator[TeknokentLead]) -> ImportReport:
    """Teknokent adaylarını veritabanına ekler — `discovery.import_leads()`'in ince kabuğu.

    COMMIT ETMEZ ("commit çağırana aittir", `src/core/db.py`).
    """
    return import_leads(session, leads, company_source=SOURCE_TEKNOKENT, label="Teknokent")
