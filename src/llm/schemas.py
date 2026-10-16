"""LLM'den dönen JSON'un şeması.

Sağlayıcı ne döndürürse döndürsün, uygulamaya yalnızca bu üç şekle uyan veri girer. Şemaya
uymayan cevap `pydantic.ValidationError` fırlatır; `llm/json_provider.py` bunu
`LLMResponseError`'a sarar, `llm/factory.SafeProvider` da anahtar kelime sağlayıcısına düşer.

Şema, halüsinasyona karşı ilk savunma hattıdır (CLAUDE.md kural 1): LLM'in serbest metin
döndürmesi yapısal olarak imkânsız. İkinci hat `base.filter_tags` ve
`phrase_validator.validate_focus_phrase`.
"""
from typing import Optional

from pydantic import BaseModel


class TagResponse(BaseModel):
    """Şirketin sektör etiketleri. Sözlük dışı olanlar sonra `filter_tags` ile süzülür."""

    tags: list[str]


class FocusPhraseResponse(BaseModel):
    """Şirketin ne yaptığını anlatan kısa ifade; şirketin metninde yoksa `null`.

    `phrase` alanına varsayılan BİLİNÇLİ olarak verilmedi: LLM anahtarı hiç döndürmezse
    `ValidationError` olsun, sessizce `None` sayılmasın. Böylece "cevap eksik" ile
    "cevap 'bulamadım'" birbirine karışmaz.
    """

    phrase: Optional[str]


class ReplyCategoryResponse(BaseModel):
    """Gelen cevabın kategorisi. Değer sonra `normalize_reply_category` ile doğrulanır."""

    category: str
