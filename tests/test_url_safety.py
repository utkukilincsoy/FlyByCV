"""url_safety: yalnızca genel (public) IP adreslerine izin verilir (SSRF koruması). Ağa çıkmaz."""
import socket

import pytest

from src.company_finder.url_safety import BlockedAddressError, ensure_public_host, is_public_ip


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",          # loopback
        "127.255.255.254",
        "10.0.0.1",           # 10/8
        "172.16.5.4",         # 172.16/12 alt sınır
        "172.31.255.255",     # 172.16/12 üst sınır
        "192.168.1.1",        # 192.168/16
        "169.254.169.254",    # link-local (bulut meta veri adresi)
        "0.0.0.0",
        "::1",                # IPv6 loopback
        "::",
        "fc00::1",            # IPv6 özel (ULA)
        "fd12:3456::1",
        "fe80::1",            # IPv6 link-local
        "fe80::1%eth0",       # bölge ekli
        "::ffff:127.0.0.1",   # IPv4-eşlemeli IPv6 içinde loopback
        "::ffff:10.0.0.1",
        "::ffff:192.168.1.1",
        "224.0.0.1",          # çoklu yayın
        "100.64.0.1",         # operatör NAT'ı (genel değil)
        "192.0.2.10",         # dokümantasyon bloğu
    ],
)
def test_non_public_addresses_are_rejected(address):
    assert is_public_ip(address) is False


@pytest.mark.parametrize(
    "address",
    ["93.184.216.34", "8.8.8.8", "172.15.255.255", "172.32.0.1", "2606:4700::1111", "::ffff:8.8.8.8"],
)
def test_public_addresses_are_allowed(address):
    assert is_public_ip(address) is True


def test_garbage_is_not_public():
    assert is_public_ip("firma.com") is False
    assert is_public_ip("") is False


def _resolver(mapping: dict[str, list[str]]):
    calls: list[str] = []

    def resolve(host: str) -> list[str]:
        calls.append(host)
        return mapping[host]

    resolve.calls = calls
    return resolve


def test_host_resolving_to_public_address_passes():
    ensure_public_host("firma.com", _resolver({"firma.com": ["93.184.216.34"]}))


def test_host_resolving_to_private_address_is_blocked():
    with pytest.raises(BlockedAddressError) as info:
        ensure_public_host("kotu.example", _resolver({"kotu.example": ["192.168.1.1"]}))

    assert info.value.host == "kotu.example"
    assert "özel" in info.value.reason


def test_one_private_address_among_public_ones_blocks():
    resolver = _resolver({"karisik.example": ["93.184.216.34", "127.0.0.1"]})

    with pytest.raises(BlockedAddressError):
        ensure_public_host("karisik.example", resolver)


def test_ip_literal_never_calls_resolver():
    resolver = _resolver({})

    with pytest.raises(BlockedAddressError):
        ensure_public_host("127.0.0.1", resolver)
    ensure_public_host("93.184.216.34", resolver)

    assert resolver.calls == []


def test_bracketed_ipv6_literal_is_checked():
    with pytest.raises(BlockedAddressError):
        ensure_public_host("[::1]", _resolver({}))


def test_empty_resolution_is_blocked():
    with pytest.raises(BlockedAddressError):
        ensure_public_host("bos.example", _resolver({"bos.example": []}))


def test_empty_host_is_blocked():
    with pytest.raises(BlockedAddressError):
        ensure_public_host("", _resolver({}))


def test_dns_failure_propagates_as_os_error():
    def failing(host: str) -> list[str]:
        raise socket.gaierror("çözülemedi")

    with pytest.raises(socket.gaierror):
        ensure_public_host("yok.example", failing)


def test_default_resolver_is_blocked_in_tests():
    """`conftest.no_real_dns` güvencesi: resolver verilmezse gerçek DNS'e gidilmez."""
    with pytest.raises(AssertionError):
        ensure_public_host("firma.com")
