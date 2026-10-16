"""Ayarlara göre sağlayıcı seçer ve hata durumunda anahtar kelimeye düşmeyi garanti eder.

`get_provider()` her zaman bir `SafeProvider` döndürür. `SafeProvider` iki iş yapar:
  1. **Düşme:** sarmaladığı sağlayıcı herhangi bir hata verirse (kota, ağ, bozuk JSON...)
     hatayı loglar ve aynı çağrıyı `KeywordProvider` üzerinde tekrarlar.
  2. **Zorlama:** etiketler `filter_tags`, ifade `validate_focus_phrase`, cevap kategorisi
     `normalize_reply_category` süzgecinden geçer. Bir LLM ne döndürürse döndürsün
     uygulamaya yalnızca doğrulanmış veri girer (CLAUDE.md kural 1).
"""
import logging
from typing import Optional

from sqlmodel import Session

from src.core.secrets import get_api_key
from src.core.settings import KEY_LLM_PROVIDER, get_setting
from src.llm.base import LLMProvider, filter_tags, normalize_reply_category
from src.llm.keyword_provider import KeywordProvider
from src.llm.phrase_validator import validate_focus_phrase
from src.llm.providers import (
    MODEL_SETTING_KEYS,
    PROVIDER_CLAUDE,
    PROVIDER_GEMINI,
    PROVIDER_GROQ,
    PROVIDER_NONE,
)

logger = logging.getLogger(__name__)


class SafeProvider(LLMProvider):
    """Bir sağlayıcıyı sarar; hata olursa yedeğe düşer, çıktıyı her zaman doğrular."""

    def __init__(self, primary: LLMProvider, fallback: Optional[LLMProvider] = None) -> None:
        self.primary = primary
        self.fallback = fallback if fallback is not None else KeywordProvider()
        # DİKKAT: Bu ad, tek bir çağrının gerçekten hangi sağlayıcıyla yapıldığını söylemez —
        # yedeğe düşülen bir çağrıdan sonra da primary'nin adı kalır. Loglama dışında
        # güvenilmemeli; çağrı başına gerçek sağlayıcı için `last_provider_name`'e bakılmalı.
        self.name = primary.name
        #: `classify_reply()`in EN SON çağrısını gerçekte hangi sağlayıcının yanıtladığı
        #: (`self.name`'in aksine, yedeğe düşülürse `fallback.name` olur). Adım 7.4'ün
        #: `EmailReply.classified_by` ("llm" | "keyword") alanını doğru doldurabilmesi için
        #: eklendi (bkz. Adım 3.2 notundaki açık soru).
        self.last_provider_name: str = primary.name

    def _log_failure(self, method: str, error: Exception) -> None:
        # `except Exception` bilinçli olarak geniştir: kota aşımı, ağ hatası, bozuk yanıt...
        # hepsi aynı davranışa yol açmalı, uygulama LLM olmadan da çalışmaya devam etmeli.
        logger.warning(
            f"{self.primary.name} sağlayıcısı {method} çağrısında hata verdi, "
            f"{self.fallback.name} sağlayıcısına düşülüyor: {error}"
        )

    def classify_company(self, name: str, about_text: str) -> list[str]:
        try:
            return filter_tags(self.primary.classify_company(name, about_text))
        except Exception as error:
            self._log_failure("classify_company", error)
            return filter_tags(self.fallback.classify_company(name, about_text))

    def company_focus_phrase(self, name: str, about_text: str) -> Optional[str]:
        try:
            phrase = self.primary.company_focus_phrase(name, about_text)
        except Exception as error:
            self._log_failure("company_focus_phrase", error)
            phrase = self.fallback.company_focus_phrase(name, about_text)
        return validate_focus_phrase(phrase, about_text)

    def classify_reply(self, subject: str, body: str) -> str:
        try:
            result = normalize_reply_category(self.primary.classify_reply(subject, body))
            self.last_provider_name = self.primary.name
            return result
        except Exception as error:
            self._log_failure("classify_reply", error)
            self.last_provider_name = self.fallback.name
            return normalize_reply_category(self.fallback.classify_reply(subject, body))


def _build_llm_provider(name: str, model: str, api_key: str) -> LLMProvider:
    """Adı verilen sağlayıcıyı kurar.

    SDK'lar burada, fonksiyonun İÇİNDE import edilir. Sebebi: kurulu olmayan tek bir paket
    (örneğin `google-genai`) yüzünden `factory` modülü hiç yüklenemez hale gelmesin. Böyle bir
    durumda `ImportError` de diğer hatalar gibi yakalanır ve anahtar kelimeye düşülür
    (CLAUDE.md kural 3: uygulama LLM olmadan da çalışmalı).
    """
    if name == PROVIDER_CLAUDE:
        from src.llm.claude_provider import ClaudeProvider

        return ClaudeProvider(api_key=api_key, model=model)
    if name == PROVIDER_GEMINI:
        from src.llm.gemini_provider import GeminiProvider

        return GeminiProvider(api_key=api_key, model=model)

    from src.llm.openai_compat_provider import build_groq_provider

    return build_groq_provider(api_key=api_key, model=model)


def get_provider(session: Session) -> LLMProvider:
    """`llm_provider` ayarına göre sağlayıcıyı kurar; her zaman `SafeProvider` döndürür.

    Anahtar kelimeye düşülen üç durum vardır ve hiçbiri hata fırlatmaz — yanlış bir ayar ya da
    eksik bir anahtar yüzünden uygulama açılmaz hale gelmemeli:
      * ayar "none" ise (LLM zaten istenmiyor),
      * tanınmayan bir sağlayıcı adı yazılmışsa,
      * sağlayıcının API anahtarı şifre kasasında yoksa ya da sağlayıcı kurulamıyorsa.
    """
    name = get_setting(session, KEY_LLM_PROVIDER)

    if name == PROVIDER_NONE:
        # "none" bile sarmalanır: ifade doğrulaması tek bir yoldan geçsin.
        return SafeProvider(KeywordProvider())

    if name not in (PROVIDER_CLAUDE, PROVIDER_GEMINI, PROVIDER_GROQ):
        logger.warning(f"Bilinmeyen LLM sağlayıcısı {name!r}, anahtar kelime kullanılacak")
        return SafeProvider(KeywordProvider())

    api_key = get_api_key(name)
    if not api_key:
        logger.warning(
            f"{name} sağlayıcısının API anahtarı şifre kasasında yok, anahtar kelime "
            f"kullanılacak (anahtar girmek için: python scripts/set_api_key.py)"
        )
        return SafeProvider(KeywordProvider())

    model = get_setting(session, MODEL_SETTING_KEYS[name])
    try:
        primary = _build_llm_provider(name, model, api_key)
    except Exception as error:
        logger.warning(
            f"{name} sağlayıcısı kurulamadı, anahtar kelime kullanılacak "
            f"({type(error).__name__}: {error})"
        )
        return SafeProvider(KeywordProvider())

    logger.info(f"LLM sağlayıcısı: {name} (model: {model})")
    return SafeProvider(primary)
