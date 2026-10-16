import pytest

from src.company_finder.site_crawler import (
    BLOCKED_ADDRESS_ERROR,
    MAX_DELAY_SECONDS,
    MAX_PAGES,
    MIN_DELAY_SECONDS,
    Response,
    crawl_company,
)
from src.company_finder.url_safety import BlockedAddressError
from src.llm.prompts import MAX_ABOUT_CHARS

FILLER = "Dolgu metni buradadir. " * 30  # sayfayı MIN_PAGE_TEXT_CHARS üstüne çıkarır


def _page(body: str) -> str:
    return f"<html><body>{body}<p>{FILLER}</p></body></html>"


class _FakeFetcher:
    """Verilen URL -> Response eşlemesini taklit eder; istenen her URL'yi kaydeder."""

    def __init__(self, pages: dict[str, Response], errors: dict[str, Exception] | None = None):
        self.pages = pages
        self.errors = errors or {}
        self.requested: list[str] = []

    def __call__(self, url: str) -> Response:
        self.requested.append(url)
        if url in self.errors:
            raise self.errors[url]
        if url in self.pages:
            return self.pages[url]
        return Response(url=url, status=404, text="")


class _SleepRecorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class _BrowserFetcherSpy:
    """Playwright'ın gerçekten hiç çağrılmadığını doğrulamak için sayaç tutar."""

    def __init__(self, html_by_url: dict[str, str] | None = None):
        self.html_by_url = html_by_url or {}
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if url in self.html_by_url:
            return self.html_by_url[url]
        return _page("<p>playwright ile alindi</p>")


HOME = "https://acme.example.com"
ROBOTS = f"{HOME}/robots.txt"


def _crawl(pages: dict[str, Response], **kwargs):
    fetcher = _FakeFetcher(pages)
    sleeper = _SleepRecorder()
    browser = kwargs.pop("browser", None) or _BrowserFetcherSpy()
    result = crawl_company(HOME, fetcher=fetcher, sleeper=sleeper, browser_fetcher=browser, **kwargs)
    return result, fetcher, sleeper, browser


def test_follows_home_to_contact_page_and_finds_email():
    home_html = _page('<a href="/iletisim">İletişim</a>')
    contact_html = _page("<p>Bize kariyer@acme.example.com adresinden ulasin.</p>")
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/iletisim": Response(f"{HOME}/iletisim", 200, contact_html),
    }
    result, fetcher, _, _ = _crawl(pages)

    assert result.error is None
    assert f"{HOME}/iletisim" in result.visited_urls
    assert [e.email for e in result.emails] == ["kariyer@acme.example.com"]


def test_link_to_other_domain_never_requested():
    home_html = _page(
        '<a href="https://baskafirma.com/iletisim">İletişim (başka firma)</a>'
        '<a href="https://facebook.com/acme">Facebook</a>'
    )
    pages = {HOME: Response(HOME, 200, home_html)}
    result, fetcher, _, _ = _crawl(pages)

    assert not any("baskafirma" in url for url in fetcher.requested)
    assert not any("facebook" in url for url in fetcher.requested)


def test_subdomain_link_is_followed():
    sub_url = "https://kariyer.acme.example.com/basvuru"
    home_html = _page(f'<a href="{sub_url}">Kariyer Başvuru</a>')
    sub_html = _page("<p>kariyer@kariyer.acme.example.com adresine yazin.</p>")
    pages = {
        HOME: Response(HOME, 200, home_html),
        sub_url: Response(sub_url, 200, sub_html),
    }
    result, fetcher, _, _ = _crawl(pages)

    assert sub_url in result.visited_urls


def test_robots_disallow_all_blocks_entire_crawl():
    pages = {
        ROBOTS: Response(ROBOTS, 200, "User-agent: *\nDisallow: /\n"),
    }
    result, fetcher, _, _ = _crawl(pages)

    assert result.error == "robots.txt taramaya izin vermiyor"
    assert result.visited_urls == []
    assert fetcher.requested == [ROBOTS]
    # Kalıcı bir sonuç -- site kendi isteğiyle taramayı reddetti, tekrar denemek anlamsız.
    assert result.temporary_failure is False


def test_robots_disallows_one_path_others_still_visited():
    home_html = _page(
        '<a href="/kariyer">Kariyer</a><a href="/iletisim">İletişim</a>'
    )
    contact_html = _page("<p>info@acme.example.com</p>")
    pages = {
        ROBOTS: Response(ROBOTS, 200, "User-agent: *\nDisallow: /kariyer\n"),
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/iletisim": Response(f"{HOME}/iletisim", 200, contact_html),
    }
    result, fetcher, _, _ = _crawl(pages)

    assert f"{HOME}/kariyer" not in fetcher.requested
    assert f"{HOME}/iletisim" in result.visited_urls


def test_robots_missing_means_everything_allowed():
    home_html = _page("<p>ana sayfa</p>")
    pages = {HOME: Response(HOME, 200, home_html)}  # robots.txt hiç eklenmedi -> 404
    result, fetcher, _, _ = _crawl(pages)

    assert result.error is None
    assert HOME in result.visited_urls


def test_subdomain_has_its_own_robots_rule():
    sub_url = "https://kariyer.acme.example.com/basvuru"
    sub_robots = "https://kariyer.acme.example.com/robots.txt"
    home_html = _page(
        f'<a href="{sub_url}">Kariyer</a><a href="/iletisim">İletişim</a>'
    )
    contact_html = _page("<p>info@acme.example.com</p>")
    pages = {
        HOME: Response(HOME, 200, home_html),
        # ana alan adının robots.txt'si serbest (404 -> free, eklenmedi)
        sub_robots: Response(sub_robots, 200, "User-agent: *\nDisallow: /\n"),
        f"{HOME}/iletisim": Response(f"{HOME}/iletisim", 200, contact_html),
    }
    result, fetcher, _, _ = _crawl(pages)

    assert sub_url not in fetcher.requested
    assert f"{HOME}/iletisim" in result.visited_urls


def test_robots_fetched_once_per_host():
    home_html = _page('<a href="/iletisim">İletişim</a><a href="/hakkimizda">Hakkımızda</a>')
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/iletisim": Response(f"{HOME}/iletisim", 200, _page("<p>info@acme.example.com</p>")),
        f"{HOME}/hakkimizda": Response(f"{HOME}/hakkimizda", 200, _page("<p>hakkimizda metni</p>")),
    }
    result, fetcher, _, _ = _crawl(pages)

    assert fetcher.requested.count(ROBOTS) == 1


def test_max_pages_limit_respected():
    links = "".join(f'<a href="/iletisim-{i}">İletişim {i}</a>' for i in range(10))
    home_html = _page(links)
    pages = {HOME: Response(HOME, 200, home_html)}
    for i in range(10):
        url = f"{HOME}/iletisim-{i}"
        pages[url] = Response(url, 200, _page(f"<p>sayfa {i}</p>"))

    result, fetcher, _, _ = _crawl(pages)

    assert len(result.visited_urls) <= MAX_PAGES
    content_requests = [url for url in fetcher.requested if url != ROBOTS]
    assert len(content_requests) <= MAX_PAGES


def test_sleep_happens_between_every_request():
    home_html = _page('<a href="/iletisim">İletişim</a>')
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/iletisim": Response(f"{HOME}/iletisim", 200, _page("<p>info@acme.example.com</p>")),
    }
    result, fetcher, sleeper, _ = _crawl(pages)

    assert len(sleeper.sleeps) == len(fetcher.requested) - 1
    assert all(MIN_DELAY_SECONDS <= s <= MAX_DELAY_SECONDS for s in sleeper.sleeps)


def test_short_page_falls_back_to_playwright():
    home_html = "<html><body>kisa</body></html>"  # MIN_PAGE_TEXT_CHARS altında
    pages = {HOME: Response(HOME, 200, home_html)}
    browser = _BrowserFetcherSpy({HOME: _page("<p>playwright ile gelen zengin metin</p>")})

    result, fetcher, _, browser = _crawl(pages, browser=browser)

    assert result.used_playwright is True
    assert browser.calls == [HOME]


def test_long_page_never_uses_playwright():
    home_html = _page("<p>yeterince uzun metin</p>")
    pages = {HOME: Response(HOME, 200, home_html)}
    browser = _BrowserFetcherSpy()

    result, fetcher, _, browser = _crawl(pages, browser=browser)

    assert result.used_playwright is False
    assert browser.calls == []


def test_home_page_fetch_failure_returns_turkish_error_without_crashing():
    fetcher = _FakeFetcher({}, errors={HOME: TimeoutError("zaman aşımı")})
    sleeper = _SleepRecorder()
    result = crawl_company(HOME, fetcher=fetcher, sleeper=sleeper, browser_fetcher=_BrowserFetcherSpy())

    assert result.error == "ana sayfa açılamadı"
    assert result.visited_urls == []
    assert result.emails == []
    # Zaman aşımı/bağlantı hatası GEÇİCİ -- birkaç gün sonra tekrar denenebilmeli.
    assert result.temporary_failure is True


def test_home_page_server_error_is_temporary_failure():
    pages = {HOME: Response(HOME, 503, "")}
    result, fetcher, _, _ = _crawl(pages)

    assert result.error == "sunucu hatası (503)"
    assert result.visited_urls == []
    assert result.temporary_failure is True


def test_home_page_opens_fine_but_no_email_is_not_temporary_failure():
    home_html = _page("<p>iletişim bilgisi yok</p>")
    pages = {HOME: Response(HOME, 200, home_html)}
    result, fetcher, _, _ = _crawl(pages)

    assert result.error is None
    assert result.emails == []
    # Kalıcı bir sonuç -- site açıldı, sadece e-posta bulunamadı; tekrar taramaya gerek yok.
    assert result.temporary_failure is False


# --- SSRF: yerel/özel ağa çıkan sayfalar (url_safety.BlockedAddressError) ---------------------


def test_home_page_on_private_network_is_permanent_error(caplog):
    blocked = BlockedAddressError("acme.example.com", "özel ağ adresi")
    fetcher = _FakeFetcher({}, errors={HOME: blocked, ROBOTS: blocked})
    browser = _BrowserFetcherSpy()

    with caplog.at_level("WARNING", logger="src.company_finder.site_crawler"):
        result = crawl_company(HOME, fetcher=fetcher, sleeper=_SleepRecorder(), browser_fetcher=browser)

    assert result.error == BLOCKED_ADDRESS_ERROR
    assert result.visited_urls == []
    # Kalıcı: adres yerel bir ağı gösteriyor, 3 gün sonra yeniden denemenin anlamı yok.
    assert result.temporary_failure is False
    assert browser.calls == []
    assert "acme.example.com: yerel/özel ağ adresine çıkıyor" in caplog.text


def test_blocked_sub_page_is_skipped_and_crawl_continues(caplog):
    home_html = _page('<a href="/iletisim">İletişim</a> <a href="/kariyer">Kariyer</a>')
    career_html = _page("<p>ik@acme.example.com</p>")
    fetcher = _FakeFetcher(
        {
            HOME: Response(HOME, 200, home_html),
            f"{HOME}/kariyer": Response(f"{HOME}/kariyer", 200, career_html),
        },
        # Örn. /iletisim yerel bir adrese 302 ile yönlendiriyor (fetcher'ın kancası durdurur).
        errors={f"{HOME}/iletisim": BlockedAddressError("192.168.1.1", "özel ağ adresi")},
    )

    with caplog.at_level("WARNING", logger="src.company_finder.site_crawler"):
        result = crawl_company(
            HOME, fetcher=fetcher, sleeper=_SleepRecorder(), browser_fetcher=_BrowserFetcherSpy()
        )

    assert result.error is None
    assert f"{HOME}/iletisim" not in result.visited_urls
    assert f"{HOME}/kariyer" in result.visited_urls
    assert [e.email for e in result.emails] == ["ik@acme.example.com"]
    assert "192.168.1.1: yerel/özel ağ adresine çıkıyor" in caplog.text


def test_blocked_playwright_attempt_is_logged_and_skipped(caplog):
    home_html = _page('<a href="/iletisim">İletişim</a>')
    contact_html = _page("<p>e-posta yok</p>")
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/iletisim": Response(f"{HOME}/iletisim", 200, contact_html),
    }

    def blocking_browser(url: str) -> str:
        raise BlockedAddressError("10.0.0.7", "özel ağ adresi")

    with caplog.at_level("WARNING", logger="src.company_finder.site_crawler"):
        result, _, _, _ = _crawl(pages, browser=blocking_browser)

    assert result.error is None
    assert result.used_playwright is False
    assert f"{HOME}/iletisim" in result.visited_urls  # httpx içeriği yine kullanıldı
    assert "10.0.0.7: yerel/özel ağ adresine çıkıyor" in caplog.text


def test_meta_refresh_stub_home_page_is_followed_to_real_content():
    """httpx gerçek HTTP yönlendirmelerini takip eder ama istemci taraflı `<meta refresh>`'i
    (HTML içi) takip etmez. Bazı siteler ana sayfada tam olarak bunu kullanıyor — gerçek
    dünyada gözlemlendi (bir üniversite sitesi): ana sayfa 84 karakterlik bir "buraya tıklayın"
    yönlendirme sayfası, gerçek içerik `<meta refresh>` hedefinde. Tarayıcı bunu kendi takip
    etmeli, yoksa about_text hiçbir zaman gerçek içeriğe ulaşmaz.
    """
    stub_html = (
        '<html><head>'
        '<meta http-equiv="refresh" content="0; url=https://acme.example.com/tr/">'
        "</head><body><p>Acme web sayfasını açmak için "
        '<a href="/tr/">buraya tıklayın</a>.</p></body></html>'
    )
    real_home_html = _page('<a href="/kurumsal">Kurumsal</a>')
    about_html = _page("<p>Acme kurumsal tanıtım metni burada.</p>")
    pages = {
        HOME: Response(HOME, 200, stub_html),
        f"{HOME}/tr/": Response(f"{HOME}/tr/", 200, real_home_html),
        f"{HOME}/kurumsal": Response(f"{HOME}/kurumsal", 200, about_html),
    }
    result, fetcher, _, _ = _crawl(pages)

    assert f"{HOME}/tr/" in fetcher.requested
    assert result.about_text is not None
    assert "Acme kurumsal tanıtım metni" in result.about_text
    assert "buraya tıklayın" not in result.about_text


def test_meta_refresh_hop_respects_target_robots_txt():
    """<meta refresh> hedefi de robots.txt'ye tabi: yasaklıysa takip edilmemeli."""
    target_url = "https://gizli.acme.example.com/sayfa"
    target_robots = "https://gizli.acme.example.com/robots.txt"
    stub_html = (
        "<html><head>"
        f'<meta http-equiv="refresh" content="0; url={target_url}">'
        "</head><body>yönlendirme</body></html>"
    )
    pages = {
        HOME: Response(HOME, 200, stub_html),
        target_robots: Response(target_robots, 200, "User-agent: *\nDisallow: /\n"),
    }
    result, fetcher, _, _ = _crawl(pages)

    assert target_url not in fetcher.requested


def test_meta_refresh_to_other_domain_never_followed():
    """<meta refresh> şirketin kendi alan adı dışına yönlendirse bile takip edilmemeli."""
    stub_html = (
        "<html><head>"
        '<meta http-equiv="refresh" content="0; url=https://baska-firma.example.com/">'
        "</head><body>yönlendirme</body></html>"
    )
    pages = {HOME: Response(HOME, 200, stub_html)}
    result, fetcher, _, _ = _crawl(pages)

    assert not any("baska-firma" in url for url in fetcher.requested)


def test_link_category_not_confused_by_substring_inside_unrelated_word():
    """"tıklayınız" (folded: "tiklayiniz") "ik" alt dizesini içerir ama KARİYER kelimesi değil.

    Gerçek dünya senaryosu: aynı href'e giden iki <a> etiketi var — biri "Kurumsal" metniyle
    (doğru kategori), diğeri "daha fazla görmek için tıklayınız" gibi bir "devamını oku"
    linkiyle. Naif alt dize araması ikinci etiketi yanlışlıkla "kariyer" (İK) sayıp, düşük
    öncelik numarasıyla doğru "hakkımızda" kategorisinin üzerine yazardı.
    """
    home_html = _page(
        '<a href="/kurumsal">Kurumsal</a>'
        '<a href="/kurumsal">daha fazla görmek için tıklayınız</a>'
    )
    about_html = _page("<p>Kurumsal tanıtım metni burada.</p>")
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/kurumsal": Response(f"{HOME}/kurumsal", 200, about_html),
    }
    result, fetcher, _, _ = _crawl(pages)

    # "kariyer" kategorisine (yanlış) düşseydi, /kurumsal ayrı bir /kariyer sayfası aranmaz,
    # ama about_text home_text'e (kısa "Dolgu metni" değilse) düşerdi; asıl kanıt about_text'in
    # gerçekten kurumsal sayfadan gelmesi:
    assert result.about_text is not None
    assert result.about_text.startswith("Kurumsal tanıtım metni")


def test_about_text_prefers_about_page_over_short_home_redirect_text():
    """Ana sayfada kısa bir yönlendirme metni varsa, uzun kurumsal sayfa tercih edilmeli."""
    home_html = '<html><body>buraya tıklayın<a href="/kurumsal">Kurumsal</a></body></html>'
    long_about = "Kurumsal tanıtım metni. " * 20  # 200 karakterden uzun
    about_html = f"<html><body>{long_about}</body></html>"
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/kurumsal": Response(f"{HOME}/kurumsal", 200, about_html),
    }
    # Playwright'a düşmesin diye kısa sayfalar için de metin döndüren bir sahte tarayıcı veriyoruz;
    # gerçek davranışta MIN_PAGE_TEXT_CHARS altı sayfalar zaten Playwright'a düşer, burada asıl
    # test edilen about_text SEÇİMİ, JS yedeği değil.
    browser = _BrowserFetcherSpy({HOME: home_html, f"{HOME}/kurumsal": about_html})

    result, fetcher, _, browser = _crawl(pages, browser=browser)

    assert result.about_text is not None
    assert result.about_text.startswith("Kurumsal tanıtım metni.")
    assert "buraya tıklayın" not in result.about_text


def test_about_text_falls_back_to_longest_when_none_reach_threshold():
    """Hiçbir aday 200 karaktere ulaşmıyorsa en uzunu alınmalı."""
    home_html = '<html><body>kisa ana sayfa<a href="/kurumsal">Kurumsal</a></body></html>'
    about_html = "<html><body>kisa kurumsal metin ama biraz daha uzun</body></html>"
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/kurumsal": Response(f"{HOME}/kurumsal", 200, about_html),
    }
    browser = _BrowserFetcherSpy({HOME: home_html, f"{HOME}/kurumsal": about_html})

    result, fetcher, _, browser = _crawl(pages, browser=browser)

    assert result.about_text is not None
    # ikisi de 200'ün altında; kurumsal metin daha uzun olduğu için o seçilmeli
    assert "kurumsal metin" in result.about_text


def test_about_text_comes_from_about_page_and_is_capped():
    long_text = "A" * (MAX_ABOUT_CHARS + 500)
    home_html = _page('<a href="/hakkimizda">Hakkımızda</a>')
    about_html = f"<html><body>{long_text}</body></html>"
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/hakkimizda": Response(f"{HOME}/hakkimizda", 200, about_html),
    }
    result, fetcher, _, _ = _crawl(pages)

    assert result.about_text is not None
    assert len(result.about_text) == MAX_ABOUT_CHARS


def test_pdf_link_never_followed_even_with_contact_keyword():
    """PDF gibi HTML olmayan linkler (uzantıya bakılarak) hiç istenmemeli — sayfa hakkını yemesin.

    Gerçek dünyada gözlemlendi (bir firma sitesi): bir KVKK aydınlatma/rıza metni PDF'i
    "İletişim" bağlantı metniyle eşleşip 6 sayfalık bütçenin 2'sini boşa harcıyordu.
    """
    home_html = _page(
        '<a href="/iletisim">İletişim</a>'
        '<a href="/docs/iletisim-aydinlatma-metni.pdf">İletişim Aydınlatma Metni</a>'
    )
    contact_html = _page("<p>info@acme.example.com</p>")
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/iletisim": Response(f"{HOME}/iletisim", 200, contact_html),
    }
    result, fetcher, _, _ = _crawl(pages)

    assert not any(url.lower().endswith(".pdf") for url in fetcher.requested)
    assert [e.email for e in result.emails] == ["info@acme.example.com"]


def test_non_html_content_type_page_skipped_without_consuming_budget():
    """Uzantıdan anlaşılamayan ama Content-Type'ı HTML olmayan bir sayfa (ikinci savunma
    hattı) atlanmalı ve sayfa bütçesini tüketmemeli; aynı kategorideki diğer sayfa yine gezilir."""
    home_html = _page(
        '<a href="/docs/aydinlatma">İletişim Aydınlatma Metni</a>'
        '<a href="/iletisim">İletişim</a>'
    )
    contact_html = _page("<p>info@acme.example.com</p>")
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/docs/aydinlatma": Response(
            f"{HOME}/docs/aydinlatma", 200, "%PDF-ikili-icerik%", content_type="application/pdf"
        ),
        f"{HOME}/iletisim": Response(f"{HOME}/iletisim", 200, contact_html),
    }
    result, fetcher, _, _ = _crawl(pages)

    assert f"{HOME}/docs/aydinlatma" not in result.visited_urls
    assert f"{HOME}/iletisim" in result.visited_urls
    assert [e.email for e in result.emails] == ["info@acme.example.com"]


def test_contact_page_email_retried_with_playwright_when_httpx_finds_none():
    """İletişim/kariyer sayfasında httpx ile e-posta bulunamazsa, sayfa Playwright ile bir kez
    daha denenir (adres JavaScript ile yükleniyor olabilir) — sayfa MIN_PAGE_TEXT_CHARS eşiğine
    takılmamış (yeterince uzun) olsa bile.
    """
    home_html = _page('<a href="/iletisim">İletişim</a>')
    contact_html_no_email = _page("<p>Bize telefonla ulaşın: 0312 219 64 74</p>")
    contact_html_via_js = _page("<p>info@acme.example.com adresinden ulaşabilirsiniz.</p>")
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/iletisim": Response(f"{HOME}/iletisim", 200, contact_html_no_email),
    }
    browser = _BrowserFetcherSpy({f"{HOME}/iletisim": contact_html_via_js})

    result, fetcher, _, browser = _crawl(pages, browser=browser)

    assert f"{HOME}/iletisim" in browser.calls
    assert result.used_playwright is True
    assert [e.email for e in result.emails] == ["info@acme.example.com"]


def test_playwright_email_retry_not_used_when_httpx_already_found_email():
    """İletişim sayfasında httpx zaten e-posta bulduysa Playwright ile tekrar denenmemeli
    (gereksiz yavaşlık)."""
    home_html = _page('<a href="/iletisim">İletişim</a>')
    contact_html = _page("<p>info@acme.example.com</p>")
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/iletisim": Response(f"{HOME}/iletisim", 200, contact_html),
    }
    browser = _BrowserFetcherSpy()

    result, fetcher, _, browser = _crawl(pages, browser=browser)

    assert browser.calls == []
    assert result.used_playwright is False


def test_second_page_in_category_skipped_when_first_already_has_email():
    """Aynı kategoride (kariyer) ikinci bir sayfa, yalnızca ilk sayfada e-posta bulunamadıysa
    gezilir; ilk sayfada bulunduysa ikinci sayfa hiç istenmez ve bütçe diğer kategorilere
    (hakkımızda) kalır.
    """
    home_html = _page(
        '<a href="/kariyer">Kariyer</a><a href="/career">Career</a>'
        '<a href="/hakkimizda">Hakkımızda</a>'
    )
    kariyer_html = _page("<p>ik@acme.example.com adresine basvurun.</p>")
    career_html = _page("<p>ikinci kariyer sayfasi, e-posta yok</p>")
    about_html = _page("<p>Hakkımızda uzun tanıtım metni burada.</p>")
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/kariyer": Response(f"{HOME}/kariyer", 200, kariyer_html),
        f"{HOME}/career": Response(f"{HOME}/career", 200, career_html),
        f"{HOME}/hakkimizda": Response(f"{HOME}/hakkimizda", 200, about_html),
    }
    result, fetcher, _, _ = _crawl(pages)

    assert f"{HOME}/career" not in fetcher.requested
    assert f"{HOME}/career" not in result.visited_urls
    assert f"{HOME}/hakkimizda" in result.visited_urls


def test_second_page_in_category_visited_when_first_has_no_email():
    """İlk kariyer sayfasında e-posta bulunamazsa ikinci kariyer sayfası gezilmeli."""
    home_html = _page('<a href="/kariyer">Kariyer</a><a href="/career">Career</a>')
    kariyer_html = _page("<p>e-posta yok, sadece form</p>")
    career_html = _page("<p>ik@acme.example.com adresine basvurun.</p>")
    pages = {
        HOME: Response(HOME, 200, home_html),
        f"{HOME}/kariyer": Response(f"{HOME}/kariyer", 200, kariyer_html),
        f"{HOME}/career": Response(f"{HOME}/career", 200, career_html),
    }
    result, fetcher, _, _ = _crawl(pages)

    assert f"{HOME}/career" in result.visited_urls
    assert [e.email for e in result.emails] == ["ik@acme.example.com"]


def test_about_text_strips_cookie_banner_and_chrome_but_email_extraction_is_untouched():
    """about_text çıkarımında çerez/KVKK uyarı kutuları ile nav/header/footer ayıklanmalı;
    e-posta ararken bu ayıklama YAPILMAZ (adresler genelde footer'da olur).

    Gerçek dünyada gözlemlendi (bir firma sitesi): about_text'in ilk cümlesi "Sitemizde size en
    iyi hizmeti sunabilmek için çerez kullanılmaktadır... Kabul et Reddet" oluyordu.
    """
    home_html = (
        "<html><body>"
        '<div id="cookie-banner">Sitemizde çerez kullanılmaktadır. Kabul et Reddet</div>'
        "<nav>Anasayfa Hakkımızda Kariyer İletişim</nav>"
        f'<p>{"Gerçek tanıtım metni burada. " * 20}</p>'
        "<footer>info@acme.example.com Tüm hakları saklıdır</footer>"
        "</body></html>"
    )
    pages = {HOME: Response(HOME, 200, home_html)}

    result, fetcher, _, browser = _crawl(pages)

    assert result.about_text is not None
    assert "çerez" not in result.about_text.lower()
    assert "Kabul et" not in result.about_text
    assert "Hakkımızda Kariyer" not in result.about_text
    assert result.about_text.startswith("Gerçek tanıtım metni")
    # e-posta ararken hiçbir şey ayıklanmaz (footer dahil):
    assert [e.email for e in result.emails] == ["info@acme.example.com"]
    assert browser.calls == []


def test_invalid_website_returns_error_without_any_request():
    fetcher = _FakeFetcher({})
    sleeper = _SleepRecorder()
    browser = _BrowserFetcherSpy()

    result = crawl_company("yok", fetcher=fetcher, sleeper=sleeper, browser_fetcher=browser)

    assert result.error == "site adresi geçersiz"
    assert fetcher.requested == []
    assert browser.calls == []
