"""Ağa çıkan sağlayıcıların ortak atası: prompt kur, JSON al, Pydantic ile doğrula.

`ClaudeProvider`, `GeminiProvider` ve `OpenAICompatProvider` yalnızca `_complete_text()`
metodunu doldurur — yani "şu prompt'u gönder, dönen metni ver". Geri kalan her şey burada
TEK yerde durur:

  * prompt kurma ve metin kırpma,
  * **gelen mailin redaksiyonu** (`llm/redact.py`),
  * çok kısa girdide ağ çağrısını hiç yapmama,
  * cevabı toleranslı ayrıştırma ve şemaya karşı doğrulama.

Redaksiyonun burada olması bilinçli bir tasarım kararı: ağa çıkan her sağlayıcı bu sınıfı miras
aldığı için redaksiyon ATLANAMAZ, yeni bir sağlayıcı yazan kişinin hatırlaması gerekmez.
`SafeProvider`'a konulamazdı, çünkü o `KeywordProvider`'ı da sarıyor — ağa çıkmayan bir
sağlayıcıda imza bloğunu kesmek eşleştirdiği kelimeleri götürebilir.

Geniş `except` YAZILMAZ: SDK hataları (zaman aşımı, kota, 401) olduğu gibi yukarı çıkar,
`llm/factory.SafeProvider` hepsini yakalayıp anahtar kelime sağlayıcısına düşer.
"""
import json
import logging
import re
from abc import abstractmethod
from typing import Optional, TypeVar

from pydantic import BaseModel, ValidationError

from src.llm.base import REPLY_PENDING, LLMProvider, filter_tags, normalize_reply_category
from src.llm.prompts import (
    MAX_ABOUT_CHARS,
    MAX_REPLY_CHARS,
    build_focus_phrase_prompt,
    build_reply_category_prompt,
    build_tag_prompt,
)
from src.llm.redact import redact
from src.llm.schemas import FocusPhraseResponse, ReplyCategoryResponse, TagResponse

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)

# Bu uzunluğun altındaki tanıtım metninden anlamlı bir sonuç çıkmaz; ağa hiç çıkılmaz.
# Hem para hem de "az veriden çok şey uyduran" LLM riski tasarrufu.
MIN_ABOUT_CHARS = 30

# LLM'in "bulamadım" demek için kullandığı metinler; hepsi None sayılır.
_EMPTY_PHRASE_VALUES = frozenset({"", "-", "null", "none", "yok", "belirsiz", "bilinmiyor"})

# Modeller JSON'u sık sık ``` çitleri arasına koyar; çitler soyulur.
_CODE_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class LLMResponseError(Exception):
    """Sağlayıcı cevap verdi ama cevap JSON değil ya da şemaya uymuyor."""


def _extract_json(raw: str) -> str:
    """Model cevabından JSON gövdesini ayıklar (``` çitleri ve çevresindeki laf temizlenir)."""
    text = _CODE_FENCE.sub("", (raw or "").strip())
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise LLMResponseError("Cevapta JSON bulunamadı")
    return text[start : end + 1]


class JsonLLMProvider(LLMProvider):
    """JSON döndüren bir LLM'i `LLMProvider` arayüzüne bağlayan taban sınıf."""

    @abstractmethod
    def _complete_text(self, prompt: str) -> str:
        """Prompt'u sağlayıcıya gönderir ve modelin ürettiği düz metni döndürür."""

    def _ask(self, prompt: str, schema: type[ModelT]) -> ModelT:
        """Prompt'u gönderir, cevabı ayrıştırır ve şemaya karşı doğrular."""
        raw = self._complete_text(prompt)
        payload = _extract_json(raw)
        try:
            return schema.model_validate_json(payload)
        except (json.JSONDecodeError, ValidationError) as error:
            # Cevabın kendisi loglanmaz: içinde gelen mailden gelmiş kişisel veri olabilir.
            raise LLMResponseError(
                f"{schema.__name__} şemasına uymayan cevap ({type(error).__name__})"
            ) from error

    def classify_company(self, name: str, about_text: str) -> list[str]:
        about = (about_text or "").strip()
        if len(about) < MIN_ABOUT_CHARS:
            logger.debug(f"{name}: tanıtım metni çok kısa, {self.name} çağrılmadı")
            return []

        answer = self._ask(build_tag_prompt(name, about[:MAX_ABOUT_CHARS]), TagResponse)
        return filter_tags(answer.tags)

    def company_focus_phrase(self, name: str, about_text: str) -> Optional[str]:
        about = (about_text or "").strip()
        if len(about) < MIN_ABOUT_CHARS:
            logger.debug(f"{name}: tanıtım metni çok kısa, {self.name} çağrılmadı")
            return None

        answer = self._ask(
            build_focus_phrase_prompt(name, about[:MAX_ABOUT_CHARS]), FocusPhraseResponse
        )
        if answer.phrase is None:
            return None
        phrase = answer.phrase.strip()
        if phrase.lower() in _EMPTY_PHRASE_VALUES:
            return None
        return phrase

    def classify_reply(self, subject: str, body: str) -> str:
        """Gelen cevabı sınıflandırır. Konu ve gövde ÖNCE redaksiyondan geçer."""
        clean_body = redact(body or "").strip()
        if not clean_body:
            logger.debug(f"Boş mail gövdesi, {self.name} çağrılmadı")
            return REPLY_PENDING

        answer = self._ask(
            build_reply_category_prompt(redact(subject or ""), clean_body[:MAX_REPLY_CHARS]),
            ReplyCategoryResponse,
        )
        return normalize_reply_category(answer.category)
