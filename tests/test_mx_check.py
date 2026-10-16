import logging

import dns.exception
import dns.resolver
import pytest

from src.company_finder.mx_check import clear_mx_cache, has_mx_record


class _FakeResolver:
    """MX kaydı olan/olmayan alan adlarını taklit eder; çağrı sayısını tutar."""

    def __init__(self, mx_domains: set[str]) -> None:
        self.mx_domains = mx_domains
        self.calls = 0

    def resolve(self, domain: str, record_type: str):
        self.calls += 1
        if domain in self.mx_domains:
            return [object()]  # len() > 0 olan herhangi bir dizi yeterli
        raise dns.resolver.NXDOMAIN()


class _BrokenResolver:
    """DNS'e hiç erişilemediği durumu taklit eder."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    def resolve(self, domain: str, record_type: str):
        self.calls += 1
        raise self.exc


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_mx_cache()
    yield
    clear_mx_cache()


def test_mx_record_exists():
    resolver = _FakeResolver({"acme.example.com"})
    assert has_mx_record("acme.example.com", resolver=resolver) is True


@pytest.mark.parametrize("exc", [dns.resolver.NXDOMAIN(), dns.resolver.NoAnswer()])
def test_no_mx_record_returns_false(exc):
    resolver = _BrokenResolver(exc)
    assert has_mx_record("acme.example.com", resolver=resolver) is False


def test_timeout_returns_none_and_logs_warning(caplog):
    resolver = _BrokenResolver(dns.exception.Timeout())
    with caplog.at_level(logging.WARNING):
        result = has_mx_record("acme.example.com", resolver=resolver)
    assert result is None
    assert "zaman aşımı" in caplog.text


def test_unexpected_error_does_not_raise():
    resolver = _BrokenResolver(RuntimeError("bağlantı yok"))
    assert has_mx_record("acme.example.com", resolver=resolver) is None


def test_result_is_cached_per_domain():
    resolver = _FakeResolver({"acme.example.com"})
    has_mx_record("acme.example.com", resolver=resolver)
    has_mx_record("acme.example.com", resolver=resolver)
    assert resolver.calls == 1


def test_none_result_is_not_cached():
    resolver = _BrokenResolver(dns.exception.Timeout())
    has_mx_record("acme.example.com", resolver=resolver)
    has_mx_record("acme.example.com", resolver=resolver)
    assert resolver.calls == 2
