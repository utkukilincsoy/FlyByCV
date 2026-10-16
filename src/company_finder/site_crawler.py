"""Bir şirket sitesini nazikçe tarar: İletişim / Kariyer / Hakkımızda sayfalarını bulur,
her sayfadan e-posta çıkarır, şirket tanıtım metnini toplar.

İlan sitesi TARANMAZ, form DOLDURULMAZ (CLAUDE.md). Yalnızca verilen `website` alan adı (ve
alt alan adları — kariyer sayfaları Türkiye'de sık sık alt alan adında oluyor) içindeki
İletişim/Kariyer/Hakkımızda linkleri takip edilir; başka bir alan adına giden link asla
istenmez. robots.txt'ye HER HOST için ayrı ayrı uyulur (bir alt alan adının robots.txt'si ana
alan adınınkinden bağımsızdır). İstekler arasında rastgele 1–3 sn beklenir.

`fetcher`/`sleeper`/`browser_fetcher` enjekte edilebilir (bkz. `ClaudeProvider(..., client=...)`
kalıbı, `src/llm/claude_provider.py`): testler üçüncü parti modülün iç adlarına dokunmadan
sahte verir, kazara ağa çıkma ihtimali kalmaz.
"""
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from src.company_finder.email_extractor import (
    PAGE_RANK_ABOUT,
    PAGE_RANK_HOME,
    PAGE_RANK_PRIORITY,
    FoundEmail,
    extract_emails,
    rank_emails,
)
from src.company_finder.url_safety import BlockedAddressError, Resolver, ensure_public_host
from src.core.models import normalize_domain
from src.core.text import build_word_pattern, fold_turkish, matches_any
from src.llm.prompts import MAX_ABOUT_CHARS

logger = logging.getLogger(__name__)

#: Dürüst kimlik: kendimizi gizlemiyoruz, site sahibi kim olduğumuzu görebilsin.
USER_AGENT = "FlybyCV/1.0 (kisisel is basvurusu araci)"
REQUEST_TIMEOUT_SECONDS = 15.0
MAX_PAGES = 6
MIN_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 3.0
#: Bu kadar görünür metin çıkmayan sayfa büyük olasılıkla JS ile kuruluyor -> Playwright.
MIN_PAGE_TEXT_CHARS = 400
#: about_text için bir adayın "yeterli" sayılması için gereken en az uzunluk. Altındaysa
#: (örn. ana sayfadaki "buraya tıklayın" gibi bir yönlendirme metni) sıradaki adaya geçilir.
ABOUT_CANDIDATE_MIN_CHARS = 200
#: <meta refresh> zincirinde en fazla kaç sıçrama takip edilir (sonsuz döngüye karşı).
MAX_META_REFRESH_HOPS = 3
#: Aynı kategoride (iletişim/kariyer/hakkımızda) en fazla kaç sayfa gezilir.
MAX_PAGES_PER_CATEGORY = 2
#: Bir sayfanın en fazla bu kadar baytı okunur, fazlası kesilir. Bozuk/kötü niyetli bir site
#: yüzlerce MB döndürse bile bellek şişmez; gerçek şirket sayfaları bunun çok altında kalır.
MAX_RESPONSE_BYTES = 3 * 1024 * 1024
#: Ana sayfa yerel/özel bir ağ adresine çıktığında `CrawlResult.error` (bkz. `url_safety.py`).
BLOCKED_ADDRESS_ERROR = "site adresi yerel/özel bir ağa çıkıyor, taranmadı"

_DOMAIN_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*(\.[a-z0-9-]+)+")

# Bağlantı takibinde asla açılmayacak dosya uzantıları: bunlar HTML değildir, içlerinde link
# veya e-posta aranmaz. Gerçek dünyada gözlemlendi: bir KVKK aydınlatma/rıza metni PDF'i
# "iletişim" anahtar kelimesiyle eşleşip 6 sayfalık taramanın 2'sini boşa harcayabiliyor.
_NON_HTML_EXTENSIONS = frozenset(
    {
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "rar", "7z", "tar", "gz",
        "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp",
        "mp3", "mp4", "avi", "mov", "wmv",
        "css", "js", "json", "csv",
        "woff", "woff2", "ttf", "eot",
    }
)

# Content-Type'a göre ikinci savunma hattı: uzantıdan anlaşılamayan (örn. ?id=123 gibi bir
# URL'nin sunucu tarafında dosya servis ettiği) durumları yakalar.
_HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

# about_text çıkarımında ayıklanacak etiketler ve çerez/KVKK uyarı kutularını tanıyan anahtar
# kelimeler. E-posta ararken (extract_emails) HİÇBİR ŞEY ayıklanmaz — adresler genelde footer'da
# olur; bu ayıklama SADECE about_text için, _visible_text_for_about() içinde uygulanır.
_ABOUT_STRIP_TAGS = ("nav", "header", "footer")
_ABOUT_STRIP_KEYWORDS = ("cookie", "consent", "gdpr", "kvkk", "cerez")

# httpx yalnızca gerçek HTTP 30x yönlendirmelerini takip eder; bu istemci taraflı (HTML içi)
# yönlendirmeyi kaçırır. Gerçek dünyada gözlemlendi: bazı siteler ana sayfada bu yöntemle
# gerçek içeriğe yönlendiriyor (ör. bir üniversite sitesi: "<meta http-equiv='refresh'
# content='0; url=https://www.example.edu/tr/'>" + "buraya tıklayın" yedek linki).
_META_REFRESH_PATTERN = re.compile(
    r'<meta[^>]+http-equiv=["\']refresh["\'][^>]*content=["\']([^"\']*)["\']', re.IGNORECASE
)
_META_REFRESH_URL_PATTERN = re.compile(r"url\s*=\s*([^;]+)", re.IGNORECASE)

# (ziyaret önceliği, anahtar kelimeler). Küçük sayı = önce ziyaret edilir. İletişim ve kariyer
# e-posta sıralamasında (PAGE_RANK_PRIORITY) eşit ağırlıklı ama ziyaret sırasında iletişim önde
# — 6 sayfalık bütçe önce en olası e-posta barındıran sayfaya gitmeli.
_LINK_KEYWORDS: list[tuple[int, tuple[str, ...]]] = [
    (0, ("iletisim", "contact", "bizeulasin")),
    (1, ("kariyer", "career", "careers", "jobs", "insankaynaklari", "ik", "hr", "isbasvuru")),
    (2, ("hakkimizda", "about", "bizkimiz", "kurumsal", "hakkinda")),
]

# Kelime sınırına saygılı desenler (build_word_pattern, src/core/text.py): naif alt dize
# araması ("ik" in "tıklayınız") yanlış eşleşme üretiyordu — "tıklayınız" katlanınca
# "tiklayiniz" olur ve içinde harf harf "ik" geçer. Kelime sınırı bu tuzağı önler.
_LINK_PATTERNS: list[tuple[int, "re.Pattern[str]"]] = [
    (priority, build_word_pattern(keywords)) for priority, keywords in _LINK_KEYWORDS
]


def _page_rank_for_priority(priority: int) -> int:
    return PAGE_RANK_ABOUT if priority == 2 else PAGE_RANK_PRIORITY


def _is_same_company_domain(url: str, company_domain: str) -> bool:
    """Verilen URL şirketin kendi alan adında mı (ya da bir alt alan adında)?

    Hem `_extract_links` hem `<meta refresh>` takibi bunu kullanır — ikisi de şirketin sınırları
    dışına asla çıkmamalı (bir site kendi alan adı dışına yönlendirse bile takip edilmez).
    """
    domain = normalize_domain(url)
    return domain == company_domain or domain.endswith(f".{company_domain}")


#: Ziyaret önceliği -> Türkçe sayfa türü adı (`--url --verbose` çıktısı için).
PAGE_CATEGORY_NAMES: dict[int, str] = {-1: "ana sayfa", 0: "iletişim", 1: "kariyer", 2: "hakkımızda"}


def page_category_name(visit_priority: int) -> str:
    """Ziyaret önceliğini Türkçe sayfa türü adına çevirir (bilinmeyen -> "diğer")."""
    return PAGE_CATEGORY_NAMES.get(visit_priority, "diğer")


class Fetcher(Protocol):
    def __call__(self, url: str) -> "Response": ...


class BrowserFetcher(Protocol):
    def __call__(self, url: str) -> str: ...


@dataclass(frozen=True)
class Response:
    url: str  # yönlendirmeler sonrası son URL
    status: int
    text: str
    content_type: str = ""  # örn. "text/html; charset=utf-8"; sunucu vermezse boş


@dataclass
class CrawlResult:
    website: str
    emails: list[FoundEmail] = field(default_factory=list)  # en iyisi başta (rank_emails uygulanmış)
    about_text: Optional[str] = None
    visited_urls: list[str] = field(default_factory=list)
    error: Optional[str] = None  # Türkçe
    used_playwright: bool = False
    #: `error` doluyken bile True olabilir: ana sayfaya hiç ulaşılamadı (zaman aşımı, bağlantı
    #: hatası) ya da sunucu 5xx döndü -- bunlar GEÇİCİ, birkaç gün sonra site düzelmiş olabilir.
    #: Kalıcı sonuçlarda (site açıldı ama e-posta yok, robots.txt engelledi, alan adı geçersiz)
    #: False kalır: `company_finder/enricher.is_crawl_due` bunu 3 günlük yeniden deneme
    #: penceresine karar vermek için kullanır.
    temporary_failure: bool = False
    #: Ana sayfanın HTML'i (yalnız bellekte; veritabanına YAZILMAZ). `enricher` buradan
    #: `naming.derive_company_name()` ile temiz şirket adı çıkarır (PLAN.md 10.6 ek).
    home_html: Optional[str] = field(default=None, repr=False)


@dataclass
class _RobotsRule:
    parser: Optional[RobotFileParser]
    crawl_delay: Optional[float]


class _HostGuard:
    """Bir host'a istek atılabilir mi (genel IP mi)? Kararı host başına önbellekler.

    Bir tarama aynı host'a birkaç kez gider (robots.txt + 6 sayfa); her seferinde DNS'e sormaya
    gerek yok. DNS hatası önbelleklenmez: geçici olabilir.
    """

    def __init__(self, resolver: Optional[Resolver] = None) -> None:
        self._resolver = resolver
        self._verdicts: dict[str, Optional[BlockedAddressError]] = {}

    def check(self, host: str) -> None:
        """Host genel değilse `BlockedAddressError` fırlatır."""
        if host not in self._verdicts:
            try:
                ensure_public_host(host, self._resolver)
                self._verdicts[host] = None
            except BlockedAddressError as error:
                self._verdicts[host] = error
        verdict = self._verdicts[host]
        if verdict is not None:
            raise verdict

    def check_url(self, url: str) -> None:
        """http/https URL'nin host'unu denetler; başka şemalar (data:, blob:) ağa çıkmaz, geçer."""
        parsed = urlsplit(url)
        if parsed.scheme in ("http", "https"):
            self.check(parsed.hostname or "")


class _HttpxFetcher:
    """Gerçek HTTP istemcisi; `crawl_company` bir tane açar ve tarama sonunda kapatır.

    Her istekten önce host denetlenir (`_HostGuard`): httpx `request` kancasını ilk istek, robots.txt
    ve HER yönlendirme adımı için ayrı ayrı çağırır, yani genel bir siteden yerel bir adrese
    yapılan 30x yönlendirmesi de istek atılmadan durdurulur. `client` verilirse (yalnızca eski
    testler) kanca eklenmez; yeni testler `transport` + `resolver` verir.
    """

    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        resolver: Optional[Resolver] = None,
    ) -> None:
        self._guard = _HostGuard(resolver)
        self._client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            transport=transport,
            event_hooks={"request": [self._check_request]},
        )

    def _check_request(self, request: httpx.Request) -> None:
        self._guard.check(request.url.host)

    def __call__(self, url: str) -> Response:
        with self._client.stream("GET", url) as response:
            body = _read_limited(response, MAX_RESPONSE_BYTES)
            return Response(
                url=str(response.url),
                status=response.status_code,
                text=body.decode(response.encoding or "utf-8", errors="replace"),
                content_type=response.headers.get("content-type", ""),
            )

    def close(self) -> None:
        self._client.close()


def _read_limited(response: httpx.Response, limit: int) -> bytes:
    """Akan yanıtın en fazla `limit` baytını okur; fazlası okunmadan bağlantı kapatılır."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        remaining = limit - total
        if len(chunk) >= remaining:
            chunks.append(chunk[:remaining])
            logger.debug(f"{response.url}: yanıt {limit} baytta kesildi")
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _route_guard(route, guard: _HostGuard, blocked: list[BlockedAddressError]) -> None:
    """Playwright `page.route` işleyicisi: sayfanın yaptığı HER isteği (ana gezinme, JS'in arka
    planda yaptığı istekler, resimler...) gitmeden önce denetler.

    Yerel adrese giden istek iptal edilir ve `blocked`'a eklenir. Denetim DNS hatasıyla
    yapılamazsa da istek iptal edilir (denetlenemeyen isteğe izin verilmez).
    """
    try:
        guard.check_url(route.request.url)
    except BlockedAddressError as error:
        blocked.append(error)
        route.abort("blockedbyclient")
        return
    except OSError:
        route.abort("failed")
        return
    route.continue_()


def _ensure_navigation_public(response, final_url: str, guard: _HostGuard) -> None:
    """İkinci savunma hattı: gezinmenin yönlendirme zincirindeki her adres ve son adres genel mi?

    `page.route` zaten her isteği denetliyor; bu, yönlendirme adımlarının işleyiciye hiç
    uğramadığı bir Playwright/Chromium davranışına karşı ucuz bir güvencedir.
    """
    urls = [final_url]
    request = response.request if response is not None else None
    while request is not None:
        urls.append(request.url)
        request = request.redirected_from
    for url in urls:
        guard.check_url(url)


class _BrowserFetcher:
    """Playwright'ı tembel açar (ilk ihtiyaçta) ve tüm tarama boyunca aynı Chromium'u
    yeniden kullanır. `renderer.py` her çağrıda ayrı tarayıcı açıyor; 6 sayfa için bunu
    yapmak kabul edilemez yavaş olurdu.

    Yerel/özel ağ adreslerine giden istekler `_route_guard` ile iptal edilir; ana gezinme
    engellenirse `BlockedAddressError` fırlatılır ve sayfa içeriği kullanılmaz.
    """

    def __init__(self, resolver: Optional[Resolver] = None) -> None:
        self._playwright = None
        self._browser = None
        self._guard = _HostGuard(resolver)

    def __call__(self, url: str) -> str:
        # Fonksiyon içinde import: Chromium kurulu olmasa da modül import edilebilir kalsın
        # (Faz 8 paketlemede Chromium ilk açılışta iniyor).
        from playwright.sync_api import sync_playwright

        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch()
        page = self._browser.new_page()
        blocked: list[BlockedAddressError] = []
        page.route("**/*", lambda route: _route_guard(route, self._guard, blocked))
        try:
            try:
                response = page.goto(url, timeout=REQUEST_TIMEOUT_SECONDS * 1000, wait_until="load")
            except Exception as exc:
                if blocked and "ERR_BLOCKED_BY_CLIENT" in str(exc):
                    raise blocked[0] from exc
                raise
            _ensure_navigation_public(response, page.url, self._guard)
            return page.content()
        finally:
            page.close()

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()


class _RequestBudget:
    """Her istekten önce (ilki hariç) rastgele bekleme yapar. robots.txt istekleri de sayılır."""

    def __init__(self, fetcher: Fetcher, sleeper) -> None:
        self._fetcher = fetcher
        self._sleeper = sleeper
        self._made_request = False

    def get(self, url: str, min_delay: float = MIN_DELAY_SECONDS) -> Response:
        if self._made_request:
            self._sleeper(random.uniform(min_delay, max(min_delay, MAX_DELAY_SECONDS)))
        self._made_request = True
        return self._fetcher(url)


def _host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _log_blocked(error: BlockedAddressError) -> None:
    """Yerel/özel ağa çıkan sayfayı loglar: yalnızca host ve neden (tam URL yazılmaz)."""
    logger.warning(f"{error.host}: yerel/özel ağ adresine çıkıyor ({error.reason}), sayfa atlandı")


def _get_robots_rule(host: str, cache: dict[str, _RobotsRule], budget: _RequestBudget) -> _RobotsRule:
    """Host için robots.txt kuralını döndürür; host başına bir kez alınır ve önbelleklenir."""
    if host in cache:
        return cache[host]
    try:
        response = budget.get(f"https://{host}/robots.txt", min_delay=MIN_DELAY_SECONDS)
        if response.status >= 400:
            rule = _RobotsRule(parser=None, crawl_delay=None)
        else:
            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
            rule = _RobotsRule(parser=parser, crawl_delay=parser.crawl_delay(USER_AGENT))
    except Exception as exc:
        logger.debug(f"{host}: robots.txt alınamadı ({exc!r}), serbest sayıldı")
        rule = _RobotsRule(parser=None, crawl_delay=None)
    cache[host] = rule
    return rule


def _check_allowed(url: str, cache: dict[str, _RobotsRule], budget: _RequestBudget) -> bool:
    rule = _get_robots_rule(_host_of(url), cache, budget)
    if rule.parser is None:
        return True
    return rule.parser.can_fetch(USER_AGENT, url)


def _min_delay_for_host(host: str, cache: dict[str, _RobotsRule]) -> float:
    rule = cache.get(host)
    if rule and rule.crawl_delay:
        return max(rule.crawl_delay, MIN_DELAY_SECONDS)
    return MIN_DELAY_SECONDS


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def _visible_text_for_about(html: str) -> str:
    """about_text için görünür metin: `_visible_text`'ten farkı, nav/header/footer ile
    çerez/KVKK uyarı kutularının da ayıklanmasıdır.

    E-posta ararken (`extract_emails`) bu ayıklama YAPILMAZ — adresler genelde footer'da olur.
    Bu fonksiyon SADECE `home_text`/`about_candidates` doldurulurken kullanılır, aksi halde
    about_text'in ilk cümlesi sıkça "Kabul et / Reddet" çerez uyarısı oluyordu.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    to_remove = []
    for tag in soup.find_all(True):
        if tag.name in _ABOUT_STRIP_TAGS:
            to_remove.append(tag)
            continue
        identifier = f"{tag.get('id', '')} {' '.join(tag.get('class', None) or [])}"
        folded = fold_turkish(identifier).lower()
        if any(keyword in folded for keyword in _ABOUT_STRIP_KEYWORDS):
            to_remove.append(tag)
    for tag in to_remove:
        # Bir üst öğe (örn. <nav>) zaten ayıklandıysa bu alt öğe artık ağaçtan kopuktur
        # (parent None olur); tekrar decompose() çağırmak hataya yol açabilir.
        if tag.parent is not None:
            tag.decompose()
    return soup.get_text(" ", strip=True)


def _is_html_content_type(content_type: str) -> bool:
    """`content_type` HTML mi? Sunucu Content-Type vermemişse iyimser olunur (True)."""
    if not content_type:
        return True
    mime = content_type.split(";", 1)[0].strip().lower()
    return mime in _HTML_CONTENT_TYPES


def _extract_links(html: str, base_url: str, company_domain: str) -> list[tuple[str, int]]:
    """Aynı şirketin alan adındaki (ya da bir alt alan adındaki) İletişim/Kariyer/Hakkımızda
    linklerini döndürür: `[(url, ziyaret_önceliği), ...]`, önceliğe göre sıralı.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: dict[str, int] = {}
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.lower().startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlsplit(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if not _is_same_company_domain(absolute, company_domain):
            continue
        extension = parsed.path.rpartition(".")[2].lower()
        if extension in _NON_HTML_EXTENSIONS:
            continue
        # matches_any kendi içinde fold_turkish uygular; burada katlamıyoruz (çift katlama
        # zararsız olurdu ama tek sorumluluk için ham metni veriyoruz).
        haystack = f"{parsed.path} {tag.get_text(' ')}"
        for priority, pattern in _LINK_PATTERNS:
            if matches_any(pattern, haystack):
                clean_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
                if clean_url not in results or priority < results[clean_url]:
                    results[clean_url] = priority
                break
    return sorted(results.items(), key=lambda item: item[1])


def _meta_refresh_target(html: str, base_url: str) -> Optional[str]:
    """`<meta http-equiv="refresh" content="0; url=...">` yönlendirmesinin hedefini bulur."""
    meta_match = _META_REFRESH_PATTERN.search(html)
    if not meta_match:
        return None
    url_match = _META_REFRESH_URL_PATTERN.search(meta_match.group(1))
    if not url_match:
        return None
    target = url_match.group(1).strip().strip("\"'")
    if not target:
        return None
    return urljoin(base_url, target)


def _starting_url(website: str) -> str:
    """Taramanın başlayacağı URL: çağıranın verdiği biçim (www./yol dahil) korunur.

    `f"https://{domain}"` ile yeniden kurmak www. önekini ve yolu atardı — bazı sitelerde
    çıplak apex alan adı (www'siz) eski bir yönlendirme/frameset sayfası servis ederken
    `www.` alt alan adı gerçek siteyi gösteriyor (gerçek dünyada gözlemlendi: bir üniversite sitesi).
    """
    if re.match(r"^https?://", website, re.IGNORECASE):
        return website
    return f"https://{website}"


def _pick_about_text(about_candidates: list[str], home_text: Optional[str]) -> Optional[str]:
    """`about_text` için en iyi adayı seçer.

    Sıra: hakkımızda/kurumsal sayfaları (ziyaret sırasına göre), sonra ana sayfa. İlk
    `ABOUT_CANDIDATE_MIN_CHARS`'a ulaşan aday kazanır — ana sayfadaki "buraya tıklayın" gibi
    kısa bir yönlendirme metni yerine gerçek bir hakkımızda sayfası tercih edilsin. Hiçbiri bu
    uzunluğa ulaşmazsa en uzun aday alınır (hiç bulunamamaktan iyidir).
    """
    candidates = [text for text in (*about_candidates, home_text) if text]
    if not candidates:
        return None
    for candidate in candidates:
        if len(candidate) >= ABOUT_CANDIDATE_MIN_CHARS:
            return candidate
    return max(candidates, key=len)


def _run_crawl(
    domain: str,
    website: str,
    fetcher: Fetcher,
    sleeper,
    browser_fetcher: BrowserFetcher,
    on_page: Optional[Callable[[str, int, int], None]] = None,
) -> CrawlResult:
    budget = _RequestBudget(fetcher, sleeper)
    robots_cache: dict[str, _RobotsRule] = {}
    home_url = _starting_url(website)

    visited: list[str] = []
    all_emails: list[FoundEmail] = []
    home_text: Optional[str] = None
    home_html: Optional[str] = None
    # Ziyaret sırasına göre; birden çok hakkımızda/kurumsal sayfası olabilir (bkz. _pick_about_text).
    about_candidates: list[str] = []
    used_playwright = False
    error: Optional[str] = None
    temporary_error = False

    # Kategori (iletişim=0/kariyer=1/hakkımızda=2) başına gezilen sayfa sayısı ve o kategoride
    # e-posta bulunup bulunmadığı: ikinci sayfa (örn. /kariyer ve /career) SADECE ilk sayfada
    # e-posta bulunamadıysa gezilir; bulunduysa bütçe diğer kategorilere kalsın.
    category_visited_count: dict[int, int] = {}
    category_has_email: dict[int, bool] = {}

    queue: list[tuple[str, int, int]] = [(home_url, -1, PAGE_RANK_HOME)]
    seen_urls = {home_url}

    while queue and len(visited) < MAX_PAGES:
        url, visit_priority, page_rank = queue.pop(0)
        is_home = url == home_url

        if visit_priority >= 0:
            visited_in_category = category_visited_count.get(visit_priority, 0)
            already_has_email = category_has_email.get(visit_priority, False)
            if visited_in_category >= MAX_PAGES_PER_CATEGORY or (
                visited_in_category >= 1 and already_has_email
            ):
                logger.debug(
                    f"{url}: {page_category_name(visit_priority)} kategorisinde bütçe doldu "
                    "(e-posta zaten bulundu ya da yeterli sayfa gezildi), atlandı"
                )
                continue

        if not _check_allowed(url, robots_cache, budget):
            if is_home:
                error = "robots.txt taramaya izin vermiyor"
                break
            logger.debug(f"{url}: robots.txt yasaklıyor, atlandı")
            continue

        try:
            response = budget.get(url, min_delay=_min_delay_for_host(_host_of(url), robots_cache))
            # <meta refresh> zincirini takip et (bkz. modül üstü yorum): bir sayfanın tek işi
            # başka bir sayfaya yönlendirmekse, MAX_PAGES bütçesinden ayrı bir "sıçrama" sayılır.
            for _ in range(MAX_META_REFRESH_HOPS):
                target = _meta_refresh_target(response.text, response.url)
                if target is None or target == response.url:
                    break
                if not _is_same_company_domain(target, domain):
                    logger.debug(f"{target}: meta refresh başka alan adına gidiyor, takip edilmedi")
                    break
                if not _check_allowed(target, robots_cache, budget):
                    logger.debug(f"{target}: meta refresh hedefi robots.txt tarafından yasaklı")
                    break
                logger.debug(f"{url}: <meta refresh> -> {target}")
                response = budget.get(
                    target, min_delay=_min_delay_for_host(_host_of(target), robots_cache)
                )
                url = target
                seen_urls.add(url)
        except BlockedAddressError as exc:
            _log_blocked(exc)
            if is_home:
                # KALICI: adres yerel bir ağı gösteriyor, birkaç gün sonra düzelecek bir şey değil.
                error = BLOCKED_ADDRESS_ERROR
                break
            continue
        except Exception as exc:
            logger.debug(f"{url}: sayfa alınamadı ({exc!r})")
            if is_home:
                # Zaman aşımı, bağlantı hatası vb. -- GEÇİCİ, birkaç gün sonra site
                # ayakta olabilir (bkz. CrawlResult.temporary_failure).
                error = "ana sayfa açılamadı"
                temporary_error = True
                break
            continue

        if response.status >= 500:
            if is_home:
                # Sunucu tarafı hata (5xx) -- GEÇİCİ, siteye ait bir arıza olabilir.
                error = f"sunucu hatası ({response.status})"
                temporary_error = True
                break
            logger.debug(f"{url}: sunucu hatası ({response.status}), atlandı")
            continue

        if not _is_html_content_type(response.content_type):
            if is_home:
                error = "ana sayfa HTML değil"
                break
            logger.debug(f"{url}: HTML olmayan içerik türü ({response.content_type!r}), atlandı")
            continue

        html = response.text
        text = _visible_text(html)
        page_used_playwright = False
        if len(text) < MIN_PAGE_TEXT_CHARS:
            try:
                html = browser_fetcher(url)
                text = _visible_text(html)
                used_playwright = True
                page_used_playwright = True
            except BlockedAddressError as exc:
                _log_blocked(exc)
            except Exception as exc:
                logger.debug(f"{url}: Playwright ile de alınamadı ({exc!r})")

        visited.append(url)
        if on_page is not None:
            on_page(url, visit_priority, len(text))

        page_emails = extract_emails(
            html, company_domain=domain, source_url=url, page_rank=page_rank
        )

        # İletişim/kariyer sayfasında httpx ile e-posta bulunamazsa Playwright ile bir kez daha
        # dene: adres JavaScript ile yükleniyor olabilir (sayfa MIN_PAGE_TEXT_CHARS eşiğine
        # takılmamış olsa bile — görünür metin yeterince uzun ama e-posta script ile ekleniyor
        # olabilir).
        is_contact_or_career = visit_priority in (0, 1)
        if is_contact_or_career and not page_emails and not page_used_playwright:
            try:
                browser_html = browser_fetcher(url)
                browser_emails = extract_emails(
                    browser_html, company_domain=domain, source_url=url, page_rank=page_rank
                )
                if browser_emails:
                    html = browser_html
                    page_emails = browser_emails
                    used_playwright = True
            except BlockedAddressError as exc:
                _log_blocked(exc)
            except Exception as exc:
                logger.debug(f"{url}: iletişim/kariyer Playwright e-posta denemesi başarısız ({exc!r})")

        all_emails.extend(page_emails)

        if visit_priority >= 0:
            category_visited_count[visit_priority] = category_visited_count.get(visit_priority, 0) + 1
            if page_emails:
                category_has_email[visit_priority] = True

        if page_rank == PAGE_RANK_HOME:
            home_text = _visible_text_for_about(html)
            home_html = html
        elif page_rank == PAGE_RANK_ABOUT:
            about_candidates.append(_visible_text_for_about(html))

        if len(visited) < MAX_PAGES:
            for link_url, priority in _extract_links(html, url, domain):
                if link_url not in seen_urls:
                    seen_urls.add(link_url)
                    queue.append((link_url, priority, _page_rank_for_priority(priority)))
            queue.sort(key=lambda item: item[1])

    final_about = _pick_about_text(about_candidates, home_text)
    if final_about:
        final_about = final_about[:MAX_ABOUT_CHARS]

    return CrawlResult(
        website=website,
        emails=rank_emails(all_emails),
        about_text=final_about,
        visited_urls=visited,
        error=error,
        used_playwright=used_playwright,
        temporary_failure=temporary_error,
        home_html=home_html,
    )


def crawl_company(
    website: str,
    *,
    fetcher: Optional[Fetcher] = None,
    sleeper=None,
    browser_fetcher: Optional[BrowserFetcher] = None,
    on_page: Optional[Callable[[str, int, int], None]] = None,
) -> CrawlResult:
    """Şirket sitesini tarar; e-posta, tanıtım metni ve hata bilgisiyle `CrawlResult` döner.

    Hiçbir exception dışarı sızmaz (CLAUDE.md 3. kural): sayfa hataları `error` alanına yazılır.

    `on_page`, her başarıyla alınan sayfa için `(url, ziyaret_önceliği, metin_uzunluğu)` ile
    çağrılır — `--url --verbose` tanılaması için (bkz. `scripts/enrich_companies.py`).
    Ziyaret önceliğini isme çevirmek için `page_category_name()` kullanılır.
    """
    domain = normalize_domain(website)
    if not _DOMAIN_PATTERN.fullmatch(domain):
        return CrawlResult(website=website, error="site adresi geçersiz")

    sleeper = sleeper or time.sleep
    own_fetcher = _HttpxFetcher() if fetcher is None else None
    own_browser = _BrowserFetcher() if browser_fetcher is None else None
    try:
        return _run_crawl(
            domain,
            website,
            own_fetcher or fetcher,
            sleeper,
            own_browser or browser_fetcher,
            on_page,
        )
    except Exception as exc:  # beklenmeyen bir hata taramayı durdursun ama çökme olmasın
        logger.exception(f"{website}: tarama beklenmeyen şekilde başarısız oldu")
        # Sebebi bilinmiyor -- güvenli taraf GEÇİCİ saymak (aksi halde kalıcı bozuk
        # sanılıp bir daha hiç denenmeyebilir).
        return CrawlResult(website=website, error=f"tarama başarısız oldu: {exc}", temporary_failure=True)
    finally:
        if own_fetcher is not None:
            own_fetcher.close()
        if own_browser is not None:
            own_browser.close()
