"""OpenAI-uyumlu uç noktalar için sağlayıcı — şu an Groq.

Bazı sağlayıcılar OpenAI'nin sohbet API'siyle aynı istek/cevap biçimini sunuyor. Böyle bir uç
için ayrı bir SDK gerekmez: `openai` paketine `base_url` verilir, gerisi aynı çalışır.

`response_format={"type": "json_object"}` cevabın JSON olmasını sunucu tarafında zorlar. Bu mod
mesajın içinde "JSON" kelimesinin geçmesini istiyor; `llm/prompts.py` bunu garanti ediyor.

Gemini bu yoldan BAĞLANMIYOR (yeni "AQ." anahtarları bu uçta 401 veriyor); gerekçe
`llm/gemini_provider.py` başında yazılı.
"""
import logging
from typing import Optional

from openai import OpenAI

from src.llm.json_provider import JsonLLMProvider, LLMResponseError
from src.llm.providers import (
    GROQ_BASE_URL,
    MAX_OUTPUT_TOKENS,
    MAX_RETRIES,
    PROVIDER_GROQ,
    REQUEST_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


class OpenAICompatProvider(JsonLLMProvider):
    """OpenAI uyumlu bir uç noktayla çalışan sağlayıcı."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        name: str,
        client: Optional[object] = None,
    ) -> None:
        """
        Args:
            api_key: Uç noktanın API anahtarı.
            model: Kullanılacak model adı.
            base_url: Uç nokta adresi (`llm/providers.py` içindeki sabitlerden biri).
            name: Loglamada görünecek sağlayıcı adı ("groq").
            client: Hazır bir SDK istemcisi; yalnızca testler doldurur.
        """
        self.name = name
        self.model = model
        self._client = (
            client
            if client is not None
            else OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=REQUEST_TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
        )

    def _complete_text(self, prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        text = response.choices[0].message.content
        if not text or not text.strip():
            raise LLMResponseError(f"{self.model} boş cevap döndürdü")
        return text


def build_groq_provider(
    api_key: str, model: str, client: Optional[object] = None
) -> OpenAICompatProvider:
    """Groq için hazır ayarlarla sağlayıcı kurar."""
    return OpenAICompatProvider(
        api_key=api_key,
        model=model,
        base_url=GROQ_BASE_URL,
        name=PROVIDER_GROQ,
        client=client,
    )
