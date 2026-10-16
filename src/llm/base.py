"""LLMProvider arayüzü ve her sağlayıcının uymak zorunda olduğu ortak kısıtlar.

Bir sağlayıcı yalnızca üç şey döndürebilir (CLAUDE.md kural 1):
  1. Şirketin sektör etiketleri — `TAG_VOCABULARY` dışına çıkamaz, en fazla üç tane.
  2. Şirketin ne yaptığını anlatan kısa bir ifade — adaydan hiç söz etmez, şirketin
     kendi metnine karşı `llm/phrase_validator.py` ile doğrulanır.
  3. Gelen bir cevabın kategorisi — üç sabit değerden biri.

CV metni, tarih, unvan, yetenek veya başarı ASLA sağlayıcıdan gelmez.
"""
import logging
from abc import ABC, abstractmethod
from typing import Optional

from src.core.models import REPLY_AUTO, REPLY_INTERVIEW, REPLY_PENDING, REPLY_REJECTED
from src.core.tags import TAG_VOCABULARY

logger = logging.getLogger(__name__)

MAX_TAGS = 3

# Bir sağlayıcının classify_reply()'den döndürebileceği değerler. Kaynak `src/core/models.py`
# (EmailReply.category'nin tam kümesi orada) — burada yalnızca LLM/anahtar kelimenin
# ÜRETEBİLECEĞİ alt küme var: "bounce" LLM'e hiç gönderilmez, kural (src/inbox/classifier.py)
# ile belirlenir, bu yüzden burada YOKTUR.
REPLY_CATEGORIES = frozenset({REPLY_INTERVIEW, REPLY_REJECTED, REPLY_PENDING, REPLY_AUTO})


def filter_tags(tags: list[str]) -> list[str]:
    """Etiket listesini sözlüğe indirger: bilinmeyenleri atar, tekrarları temizler, üçe keser.

    Sıra korunur. Aynı sonuca ikinci kez uygulanması bir şeyi değiştirmez (idempotent),
    bu yüzden hem sağlayıcının kendi içinde hem `SafeProvider`'da çağrılabilir.
    """
    result: list[str] = []
    for tag in tags:
        cleaned = tag.strip()
        if not cleaned:
            continue
        if cleaned not in TAG_VOCABULARY:
            logger.debug(f"Sözlükte olmayan etiket atıldı: {cleaned!r}")
            continue
        if cleaned not in result:
            result.append(cleaned)
    return result[:MAX_TAGS]


def normalize_reply_category(value: str) -> str:
    """Cevap kategorisini üç geçerli değerden birine indirger; tanımadığında 'pending'."""
    cleaned = (value or "").strip().lower()
    if cleaned in REPLY_CATEGORIES:
        return cleaned
    logger.warning(f"Bilinmeyen cevap kategorisi {value!r}, '{REPLY_PENDING}' kabul edildi")
    return REPLY_PENDING


class LLMProvider(ABC):
    """Şirket etiketleme, şirket odağı ifadesi ve cevap sınıflandırma arayüzü."""

    #: Loglama ve `EmailReply.classified_by` için kısa ad ("keyword", "claude", ...).
    name: str = "base"

    @abstractmethod
    def classify_company(self, name: str, about_text: str) -> list[str]:
        """Şirketin sektör etiketlerini döndürür.

        Sonuç her zaman `TAG_VOCABULARY` içindedir ve en fazla `MAX_TAGS` etiket içerir;
        gerçekleştiren sınıf bunu `filter_tags()` ile garanti etmelidir.
        """

    @abstractmethod
    def company_focus_phrase(self, name: str, about_text: str) -> Optional[str]:
        """Şirketin ne yaptığını anlatan 2–6 kelimelik ifade; bulunamazsa None.

        Aday hakkında hiçbir şey söylemez. Çağıran taraf sonucu
        `phrase_validator.validate_focus_phrase()` ile doğrulamalıdır.
        """

    @abstractmethod
    def classify_reply(self, subject: str, body: str) -> str:
        """Gelen cevabı sınıflandırır: `REPLY_INTERVIEW`, `REPLY_REJECTED` veya `REPLY_PENDING`."""
