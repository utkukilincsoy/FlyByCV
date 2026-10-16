import logging
from typing import Optional

import pytest

from src.core.settings import KEY_LLM_PROVIDER, set_setting
from src.core.tags import TAG_VOCABULARY
from src.llm.base import MAX_TAGS, REPLY_PENDING, REPLY_REJECTED, LLMProvider
from src.llm.factory import SafeProvider, get_provider
from src.llm.keyword_provider import KeywordProvider

NETWORK_ABOUT = "Cisco router ve switch kurulumu, LAN/WAN ağ altyapısı projeleri yapıyoruz."
REJECT_BODY = "Maalesef başvurunuz olumsuz sonuçlanmıştır."


class _BoomProvider(LLMProvider):
    """Her çağrıda hata fırlatan sahte sağlayıcı (kota aşımı / ağ hatası taklidi)."""

    name = "patlayan"

    def classify_company(self, name: str, about_text: str) -> list[str]:
        raise RuntimeError("kota doldu")

    def company_focus_phrase(self, name: str, about_text: str) -> Optional[str]:
        raise RuntimeError("bağlantı hatası")

    def classify_reply(self, subject: str, body: str) -> str:
        raise RuntimeError("bozuk yanıt")


class _LiarProvider(LLMProvider):
    """Sözlük dışı etiket, fazla etiket ve uydurma ifade döndüren sahte sağlayıcı."""

    name = "uyduran"

    def classify_company(self, name: str, about_text: str) -> list[str]:
        return ["uydurma-etiket", "backend", "frontend", "mobil", "veritabani"]

    def company_focus_phrase(self, name: str, about_text: str) -> Optional[str]:
        return "30 yıllık deneyimimle harika işler"

    def classify_reply(self, subject: str, body: str) -> str:
        return "harika bir haber"


class _HonestProvider(LLMProvider):
    """Kurallara uyan sahte sağlayıcı."""

    name = "duzgun"

    def classify_company(self, name: str, about_text: str) -> list[str]:
        return ["ag-yonetimi"]

    def company_focus_phrase(self, name: str, about_text: str) -> Optional[str]:
        return "ağ altyapısı projeleri"

    def classify_reply(self, subject: str, body: str) -> str:
        return REPLY_REJECTED


def test_get_provider_returns_safe_provider_by_default(session):
    """Varsayılan ayarla ("none") anahtar kelime sağlayıcısı sarmalanmış olarak dönmeli."""
    provider = get_provider(session)
    assert isinstance(provider, SafeProvider)
    assert isinstance(provider.primary, KeywordProvider)


def test_get_provider_works_end_to_end(session):
    """Varsayılan sağlayıcı gerçek bir metni etiketleyebilmeli."""
    tags = get_provider(session).classify_company("Örnek Ağ A.Ş.", NETWORK_ABOUT)
    assert "ag-yonetimi" in tags
    assert set(tags) <= TAG_VOCABULARY


def test_unknown_provider_name_falls_back_without_raising(session, caplog):
    """Bilinmeyen sağlayıcı adı uygulamayı kırmamalı, anahtar kelimeye düşmeli."""
    set_setting(session, KEY_LLM_PROVIDER, "henuz-yazilmadi")
    with caplog.at_level(logging.WARNING):
        provider = get_provider(session)

    assert isinstance(provider.primary, KeywordProvider)
    assert "Bilinmeyen LLM sağlayıcısı" in caplog.text


def test_safe_provider_falls_back_on_error(caplog):
    """Sağlayıcı hata fırlatırsa sonuç yedek sağlayıcıdan gelmeli."""
    provider = SafeProvider(_BoomProvider())
    with caplog.at_level(logging.WARNING):
        tags = provider.classify_company("Örnek Ağ A.Ş.", NETWORK_ABOUT)
        category = provider.classify_reply("Başvurunuz", REJECT_BODY)
        phrase = provider.company_focus_phrase("Örnek Ağ A.Ş.", NETWORK_ABOUT)

    assert "ag-yonetimi" in tags
    assert category == REPLY_REJECTED
    assert phrase is None  # yedek sağlayıcı ifade üretmez
    assert caplog.text.count("patlayan sağlayıcısı") == 3


def test_safe_provider_filters_invalid_tags():
    """Sözlük dışı ve üçten fazla etiket süzülmeli."""
    tags = SafeProvider(_LiarProvider()).classify_company("Örnek", NETWORK_ABOUT)
    assert "uydurma-etiket" not in tags
    assert len(tags) == MAX_TAGS
    assert set(tags) <= TAG_VOCABULARY


def test_safe_provider_rejects_invalid_phrase():
    """Uydurma ifade (rakam + birinci şahıs) doğrulamadan geçmemeli."""
    assert SafeProvider(_LiarProvider()).company_focus_phrase("Örnek", NETWORK_ABOUT) is None


def test_safe_provider_normalizes_unknown_category():
    """Tanınmayan kategori 'pending' olmalı."""
    assert SafeProvider(_LiarProvider()).classify_reply("Konu", "Gövde") == REPLY_PENDING


def test_safe_provider_passes_valid_output_through():
    """Kurallara uyan çıktı olduğu gibi geçmeli."""
    provider = SafeProvider(_HonestProvider())
    assert provider.classify_company("Örnek", NETWORK_ABOUT) == ["ag-yonetimi"]
    assert provider.company_focus_phrase("Örnek", NETWORK_ABOUT) == "ağ altyapısı projeleri"
    assert provider.classify_reply("Konu", REJECT_BODY) == REPLY_REJECTED


def test_safe_provider_keeps_primary_name():
    """Loglamada hangi sağlayıcının kullanıldığı görünmeli."""
    assert SafeProvider(_HonestProvider()).name == "duzgun"


def test_safe_provider_last_provider_name_reflects_success():
    """Çağrı başarılıysa `last_provider_name` birincil sağlayıcıyı göstermeli."""
    provider = SafeProvider(_HonestProvider())
    provider.classify_reply("Konu", REJECT_BODY)
    assert provider.last_provider_name == "duzgun"


def test_safe_provider_last_provider_name_reflects_fallback(caplog):
    """Çağrı yedeğe düşerse `last_provider_name` "keyword" olmalı, `.name` DEĞİL
    (`.name` loglama dışında güvenilmez, bkz. sınıf docstring'i)."""
    provider = SafeProvider(_BoomProvider())
    with caplog.at_level(logging.WARNING):
        provider.classify_reply("Konu", REJECT_BODY)

    assert provider.last_provider_name == "keyword"
    assert provider.name == "patlayan"
