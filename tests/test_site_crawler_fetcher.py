"""site_crawler fetcher'ları: yanıt boyutu sınırı ve yerel/özel ağ adresi engeli (SSRF).

Ağa çıkmaz: httpx için `httpx.MockTransport` + sahte resolver, Playwright için sahte route nesneleri.
"""
import socket

import httpx
import pytest

from src.company_finder import site_crawler
from src.company_finder.site_crawler import (
    _ensure_navigation_public,
    _HostGuard,
    _HttpxFetcher,
    _route_guard,
)
from src.company_finder.url_safety import BlockedAddressError


def _fetcher(body: bytes, headers: dict[str, str] | None = None) -> _HttpxFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers=headers or {"content-type": "text/html"})

    return _HttpxFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_small_response_is_read_completely():
    fetcher = _fetcher("<html>Merhaba dünya</html>".encode("utf-8"),
                       {"content-type": "text/html; charset=utf-8"})

    response = fetcher("https://ornek.com/")

    assert response.status == 200
    assert response.text == "<html>Merhaba dünya</html>"
    assert response.content_type.startswith("text/html")
    assert response.url == "https://ornek.com/"


def test_large_response_is_truncated_at_limit(monkeypatch):
    monkeypatch.setattr(site_crawler, "MAX_RESPONSE_BYTES", 1000)
    fetcher = _fetcher(b"a" * 50_000)

    response = fetcher("https://ornek.com/")

    assert len(response.text) == 1000


def test_charset_from_header_is_used():
    fetcher = _fetcher("Çağrı".encode("iso-8859-9"), {"content-type": "text/html; charset=iso-8859-9"})

    assert fetcher("https://ornek.com/").text == "Çağrı"


# --- SSRF: yerel/özel ağ adreslerine istek atılmaz (url_safety) -----------------------------

PUBLIC_IP = "93.184.216.34"


def _resolver(mapping: dict[str, list[str]]):
    def resolve(host: str) -> list[str]:
        return mapping[host]

    return resolve


def _guarded_fetcher(handler, mapping: dict[str, list[str]]) -> tuple[_HttpxFetcher, list[str]]:
    """Kancalı (üretimdeki gibi) fetcher; transport'a ULAŞAN isteklerin URL'lerini kaydeder."""
    reached: list[str] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        reached.append(str(request.url))
        return handler(request)

    fetcher = _HttpxFetcher(
        transport=httpx.MockTransport(recording_handler), resolver=_resolver(mapping)
    )
    return fetcher, reached


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"<html>ok</html>", headers={"content-type": "text/html"})


def test_public_host_is_fetched():
    fetcher, reached = _guarded_fetcher(_ok, {"firma.com": [PUBLIC_IP]})

    assert fetcher("https://firma.com/").status == 200
    assert reached == ["https://firma.com/"]


def test_loopback_literal_never_reaches_transport():
    fetcher, reached = _guarded_fetcher(_ok, {})

    with pytest.raises(BlockedAddressError):
        fetcher("http://127.0.0.1/")
    assert reached == []


def test_host_resolving_to_private_ip_never_reaches_transport():
    fetcher, reached = _guarded_fetcher(_ok, {"modem.example": ["192.168.1.1"]})

    with pytest.raises(BlockedAddressError):
        fetcher("https://modem.example/")
    assert reached == []


def test_redirect_to_private_address_is_blocked_before_second_request():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "firma.com":
            return httpx.Response(302, headers={"location": "http://192.168.1.1/admin"})
        return _ok(request)

    fetcher, reached = _guarded_fetcher(handler, {"firma.com": [PUBLIC_IP]})

    with pytest.raises(BlockedAddressError) as info:
        fetcher("https://firma.com/")
    assert info.value.host == "192.168.1.1"
    assert reached == ["https://firma.com/"]  # yönlendirme hedefine hiç gidilmedi


def test_redirect_to_host_resolving_privately_is_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "firma.com":
            return httpx.Response(301, headers={"location": "https://ic.firma.com/"})
        return _ok(request)

    fetcher, reached = _guarded_fetcher(
        handler, {"firma.com": [PUBLIC_IP], "ic.firma.com": ["10.0.0.5"]}
    )

    with pytest.raises(BlockedAddressError):
        fetcher("https://firma.com/")
    assert reached == ["https://firma.com/"]


def test_redirect_between_public_hosts_is_followed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "firma.com":
            return httpx.Response(301, headers={"location": "https://www.firma.com/"})
        return _ok(request)

    fetcher, reached = _guarded_fetcher(
        handler, {"firma.com": [PUBLIC_IP], "www.firma.com": [PUBLIC_IP]}
    )

    assert fetcher("https://firma.com/").url == "https://www.firma.com/"
    assert reached == ["https://firma.com/", "https://www.firma.com/"]


def test_host_verdict_is_cached_per_host():
    calls: list[str] = []

    def resolve(host: str) -> list[str]:
        calls.append(host)
        return [PUBLIC_IP]

    fetcher = _HttpxFetcher(transport=httpx.MockTransport(_ok), resolver=resolve)
    fetcher("https://firma.com/")
    fetcher("https://firma.com/iletisim")

    assert calls == ["firma.com"]


# --- Playwright yolu: page.route işleyicisi ve yönlendirme zinciri (gerçek tarayıcı yok) ------


class _FakeRequest:
    def __init__(self, url: str, redirected_from: "_FakeRequest | None" = None) -> None:
        self.url = url
        self.redirected_from = redirected_from


class _FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = _FakeRequest(url)
        self.outcome: str | None = None

    def abort(self, error_code: str) -> None:
        self.outcome = f"abort:{error_code}"

    def continue_(self) -> None:
        self.outcome = "continue"


class _FakeResponse:
    def __init__(self, request: _FakeRequest) -> None:
        self.request = request


def _guard(mapping: dict[str, list[str]]) -> _HostGuard:
    return _HostGuard(_resolver(mapping))


def test_route_guard_lets_public_request_through():
    route, blocked = _FakeRoute("https://firma.com/iletisim"), []

    _route_guard(route, _guard({"firma.com": [PUBLIC_IP]}), blocked)

    assert route.outcome == "continue" and blocked == []


def test_route_guard_aborts_private_request():
    route, blocked = _FakeRoute("http://192.168.1.1/api"), []

    _route_guard(route, _guard({}), blocked)

    assert route.outcome == "abort:blockedbyclient"
    assert [error.host for error in blocked] == ["192.168.1.1"]


def test_route_guard_passes_non_network_schemes():
    route, blocked = _FakeRoute("data:image/png;base64,AAAA"), []

    _route_guard(route, _guard({}), blocked)

    assert route.outcome == "continue" and blocked == []


def test_route_guard_aborts_when_dns_fails():
    def failing(host: str) -> list[str]:
        raise socket.gaierror("çözülemedi")

    route, blocked = _FakeRoute("https://yok.example/"), []

    _route_guard(route, _HostGuard(failing), blocked)

    assert route.outcome == "abort:failed" and blocked == []


def test_navigation_check_rejects_private_hop_in_redirect_chain():
    first = _FakeRequest("https://firma.com/")
    hop = _FakeRequest("http://10.0.0.1/", redirected_from=first)
    last = _FakeRequest("https://www.firma.com/", redirected_from=hop)
    guard = _guard({"firma.com": [PUBLIC_IP], "www.firma.com": [PUBLIC_IP]})

    with pytest.raises(BlockedAddressError):
        _ensure_navigation_public(_FakeResponse(last), "https://www.firma.com/", guard)


def test_navigation_check_rejects_private_final_url():
    guard = _guard({"firma.com": [PUBLIC_IP]})

    with pytest.raises(BlockedAddressError):
        _ensure_navigation_public(
            _FakeResponse(_FakeRequest("https://firma.com/")), "http://127.0.0.1/", guard
        )


def test_navigation_check_accepts_public_chain():
    first = _FakeRequest("https://firma.com/")
    last = _FakeRequest("https://www.firma.com/", redirected_from=first)
    guard = _guard({"firma.com": [PUBLIC_IP], "www.firma.com": [PUBLIC_IP]})

    _ensure_navigation_public(_FakeResponse(last), "https://www.firma.com/", guard)
