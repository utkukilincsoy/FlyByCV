"""Claude (Anthropic) sağlayıcısı.

Tek işi prompt'u Anthropic'in Messages API'sine göndermek ve dönen metni vermek. JSON
ayrıştırma, doğrulama ve redaksiyon `llm/json_provider.JsonLLMProvider` içinde.

İki bilinçli karar:

* **`temperature` GÖNDERİLMİYOR.** Yeni Claude modellerinde (Opus 5, Sonnet 5) bu parametre
  kaldırıldı ve gönderilirse istek 400 hatası alıyor. Model adı bir ayar olduğu için,
  `temperature` yazmak kullanıcının modeli değiştirdiği gün patlayan bir tuzak olurdu.
* **Yapılandırılmış çıktı (`messages.parse`) kullanılmıyor.** SDK'da var, ama sunucu tarafında
  desteği modele bağlı; ayardan desteklemeyen bir model seçilirse her çağrı hata verir ve
  `SafeProvider` sessizce anahtar kelimeye düşer. Bunun yerine prompt'ta JSON istenip cevap
  Pydantic ile doğrulanıyor — her modelde çalışır.
"""
import logging
from typing import Optional

import anthropic

from src.llm.json_provider import JsonLLMProvider, LLMResponseError
from src.llm.providers import (
    MAX_OUTPUT_TOKENS,
    MAX_RETRIES,
    PROVIDER_CLAUDE,
    REQUEST_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


class ClaudeProvider(JsonLLMProvider):
    """Anthropic Claude ile şirket etiketleme, odak ifadesi ve cevap sınıflandırma."""

    name = PROVIDER_CLAUDE

    def __init__(self, api_key: str, model: str, client: Optional[object] = None) -> None:
        """
        Args:
            api_key: Anthropic API anahtarı (`core/secrets.get_api_key`'den gelir).
            model: Kullanılacak model adı (`llm_model_claude` ayarı).
            client: Hazır bir SDK istemcisi. Yalnızca testler doldurur; böylece testler
                üçüncü parti modülün iç adlarına dokunmadan sahte istemci verebilir ve
                kazara ağa çıkma ihtimali kalmaz.
        """
        self.model = model
        self._client = (
            client
            if client is not None
            else anthropic.Anthropic(
                api_key=api_key,
                timeout=REQUEST_TIMEOUT_SECONDS,
                max_retries=MAX_RETRIES,
            )
        )

    def _complete_text(self, prompt: str) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=MAX_OUTPUT_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise LLMResponseError(f"{self.model} boş cevap döndürdü")
        return text
