"""Gemini (Google) sağlayıcısı — resmi `google-genai` paketi, native uç nokta.

**Neden OpenAI-uyumlu uç kullanılmıyor:** Gemini'nin `/v1beta/openai/` adresinde OpenAI uyumlu
bir ucu var, ama Google AI Studio'nun yeni "AQ." ile başlayan anahtarları bu uçta `Bearer`
başlığıyla 401 (yetkisiz) hatası veriyor; aynı anahtar native uçta çalışıyor. Bu yüzden Gemini
kendi SDK'sıyla bağlanıyor, Groq ise OpenAI-uyumlu yolda kalıyor
(`llm/openai_compat_provider.py`).

Anahtarın biçimine ("AIza..." eski, "AQ..." yeni) göre HİÇBİR kontrol yapılmaz; ikisi de
olduğu gibi SDK'ya verilir. Biçim kontrolü yapmak, Google yarın üçüncü bir biçim çıkardığında
kodun çalışmayı bırakması demek olurdu.

`response_mime_type="application/json"` sunucuya cevabın JSON olacağını söyler — prompt'taki
JSON isteğinin üstüne gelen ikinci bir güvence.
"""
import logging
from typing import Optional

from google import genai
from google.genai import types

from src.llm.json_provider import JsonLLMProvider, LLMResponseError
from src.llm.providers import (
    GEMINI_MAX_OUTPUT_TOKENS,
    PROVIDER_GEMINI,
    REQUEST_TIMEOUT_MS,
)

logger = logging.getLogger(__name__)


class GeminiProvider(JsonLLMProvider):
    """Google Gemini ile şirket etiketleme, odak ifadesi ve cevap sınıflandırma."""

    name = PROVIDER_GEMINI

    def __init__(self, api_key: str, model: str, client: Optional[object] = None) -> None:
        """
        Args:
            api_key: Google AI Studio API anahtarı ("AIza..." veya "AQ..." — fark etmez).
            model: Kullanılacak model adı (`llm_model_gemini` ayarı).
            client: Hazır bir SDK istemcisi; yalnızca testler doldurur.
        """
        self.model = model
        self._client = (
            client
            if client is not None
            # DİKKAT: `google-genai`'de zaman aşımı MİLİSANİYE cinsinden
            # (`anthropic` ve `openai` saniye bekliyor).
            else genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
            )
        )

    def _complete_text(self, prompt: str) -> str:
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
                max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
                # Otomatik fonksiyon çağırma (AFC) kapalı: hiç `tools` göndermiyoruz, ama SDK
                # açık kaldığı sürece her çağrıda AFC satırı logluyor. Kapatmak o gürültüyü
                # keser ve ileride biri `tools` eklerse sessizce devreye girmesini önler.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
        # `.text` None olabilir: güvenlik filtresi devreye girdiğinde ya da düşünme token'ları
        # bütçeyi bitirdiğinde cevap metni hiç üretilmez.
        text = response.text
        if not text or not text.strip():
            raise LLMResponseError(f"{self.model} boş cevap döndürdü")
        return text
