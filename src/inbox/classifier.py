"""Gelen bir cevabın kategorisini belirler (PLAN.md Faz 7 Adım 7.4).

İki aşamalı (Faz 7.1 notu):
  1. **Deterministik kural** — `classified_by=CLASSIFIED_BY_RULE`:
     * **bounce** — `reply_matcher.match_reply()` zaten `MATCH_BOUNCE` ile hangi mailin bounce
       olduğunu (gömülü orijinal Message-ID bulunduğu için) KESİN olarak söylüyor; burada bounce
       kararı yeniden üretilmez, yalnızca `matched_by` parametresi okunur.
     * **otomatik cevap** (tatil/izin/"out of office") — önce RFC 3834 `Auto-Submitted` ve eski
       `Precedence: auto_reply` / `X-Autoreply` / `X-Autorespond` başlıklarına bakılır
       (`imap_reader.EmailHeader`), yoksa konu satırındaki tipik kalıplara (`core/text.
       build_word_pattern`, bounce ön elemesiyle aynı desen). **`noreply@...` gibi bir gönderen
       adresi TEK BAŞINA otomatik cevap sayılmaz** — şirketler gerçek red/mülakat mailini de sık
       sık `noreply@` adresinden gönderiyor; böyle bir durumda mail (kural eşleşmezse) LLM/
       anahtar kelime aşamasına düşer.
     * **"bir daha yazmayın / listeden çıkarın / ilgilenmiyoruz"** (`REPLY_OPT_OUT`, Adım 10.8b)
       — konu + gövdede anahtar kelime ifadesi. BİLEREK yalnızca kural (LLM'e bırakılmaz):
       engelleme kalıcıdır, LLM'siz kurulumda da aynı çalışmalı ve bir LLM yanlış pozitifi
       şirketi sessizce engellememeli. Yanlış pozitif olursa kullanıcı Cevaplar sekmesinden
       engeli kaldırır. Bounce ve otomatik cevap kontrollerinden SONRA çalışır (otomatik bir
       "unsubscribe" alt bilgisi taşıyan tatil cevabı engel oluşturmasın).
  2. **LLM / anahtar kelime** — kural hiçbiri eşleşmezse `src/llm/factory.get_provider()`
     çağrılır. `classified_by`, `SafeProvider.last_provider_name`den okunur (yedeğe düşülürse
     "keyword" olur) — `self.name`e güvenilemez (bkz. Faz 3.2 notundaki açık soru).

Bu modül veritabanına hiç YAZMAZ (yalnızca `get_provider` için `session` okur); kaydetmek
çağıranın (Adım 7.5, `service.py`) işi.
"""
from dataclasses import dataclass

from sqlmodel import Session

from src.core.models import (
    CLASSIFIED_BY_KEYWORD,
    CLASSIFIED_BY_LLM,
    CLASSIFIED_BY_RULE,
    REPLY_AUTO,
    REPLY_BOUNCE,
    REPLY_OPT_OUT,
)
from src.core.text import build_word_pattern, fold_turkish, matches_any
from src.inbox.imap_reader import EmailBody, EmailHeader
from src.inbox.reply_matcher import MATCH_BOUNCE
from src.llm.factory import get_provider
from src.llm.keyword_provider import KeywordProvider

_AUTO_REPLY_SUBJECT_KEYWORDS = (
    "otomatik yanıt", "otomatik cevap", "otomatik bir yanıttır",
    "ofis dışındayım", "izindeyim", "yıllık izindeyim", "tatildeyim",
    "out of office", "automatic reply", "auto-reply", "auto reply", "autoreply",
    "away from office", "away from the office", "on leave", "on vacation",
)
_AUTO_REPLY_SUBJECT_PATTERN = build_word_pattern(_AUTO_REPLY_SUBJECT_KEYWORDS)

# "Bir daha yazmayın" türü ifadeler (Adım 10.8b). Karşılaştırma `fold_turkish` ile yapıldığı için
# Türkçe karakter kullanmadan yazılmış cevaplar da eşleşir; kelime sınırına saygılıdır
# (`build_word_pattern`), bu yüzden çekim ekli biçimler ayrı ayrı listelenir.
_OPT_OUT_KEYWORDS = (
    "bir daha yazmayın", "bir daha yazmayınız", "bir daha mail atmayın",
    "bir daha mail göndermeyin", "bir daha e-posta göndermeyin", "bir daha eposta göndermeyin",
    "bize yazmayın", "bize yazmayınız", "bize mail atmayın", "bize mail göndermeyin",
    "bize e-posta göndermeyin", "mail göndermeyin", "e-posta göndermeyin", "eposta göndermeyin",
    "iletişime geçmeyin", "iletişime geçmeyiniz", "rahatsız etmeyin", "rahatsız etmeyiniz",
    "listeden çıkarın", "listeden çıkarınız", "listenizden çıkarın", "listenizden çıkarınız",
    "listeden silin", "listenizden silin", "listeden çıkarır mısınız", "abonelikten çık",
    "ilgilenmiyoruz", "ilgilenmemekteyiz",
    "unsubscribe", "remove me", "remove us", "stop emailing", "stop contacting",
    "do not contact", "don't contact", "do not email", "don't email", "opt out", "opt-out",
    "take me off", "take us off",
)
_OPT_OUT_PATTERN = build_word_pattern(_OPT_OUT_KEYWORDS)

# RFC 3834'e göre `Auto-Submitted: no` VARSAYILAN değerdir ve otomatik cevap OLMADIĞINI
# gösterir; yalnızca bunun DIŞINDAKİ bir değer (auto-replied, auto-generated, auto-notified)
# otomatik cevap sayılır.
_AUTO_SUBMITTED_NOT_AUTO = "no"

# Eski (RFC öncesi) kural: `Precedence: auto_reply`. Diğer `Precedence` değerleri (bulk, list,
# junk) toplu postadır, otomatik CEVAP değildir — bu yüzden yalnızca tam bu değer sayılır.
_PRECEDENCE_AUTO_REPLY = "auto_reply"

# "Kapalı" sayılan değerler: bu başlıklar genelde VARLIĞI yeterliyken bazı sunucular yine de
# olumsuz bir değer yazabilir.
_NEGATIVE_FLAG_VALUES = frozenset({"no", "0", "false"})


def _is_truthy_header(value: str | None) -> bool:
    """Varlığı genelde yeterli olan bir başlık (`X-Autoreply`/`X-Autorespond`) "açık" mı?"""
    if not value:
        return False
    return fold_turkish(value).strip() not in _NEGATIVE_FLAG_VALUES


@dataclass(frozen=True)
class ClassifiedReply:
    """`classify()`nin sonucu: kategori ve nasıl bulunduğu."""

    category: str  # REPLY_* sabitlerinden biri
    classified_by: str  # CLASSIFIED_BY_* sabitlerinden biri


def _has_auto_reply_header(header: EmailHeader) -> bool:
    """RFC 3834 `Auto-Submitted` ya da eski `Precedence`/`X-Autoreply`/`X-Autorespond`
    başlıklarından biri otomatik cevabı işaret ediyor mu?"""
    auto_submitted = fold_turkish(header.auto_submitted or "").strip()
    if auto_submitted and auto_submitted != _AUTO_SUBMITTED_NOT_AUTO:
        return True

    precedence = fold_turkish(header.precedence or "").strip().replace("-", "_").replace(" ", "_")
    if precedence == _PRECEDENCE_AUTO_REPLY:
        return True

    return _is_truthy_header(header.x_autoreply) or _is_truthy_header(header.x_autorespond)


def _looks_like_auto_reply(header: EmailHeader) -> bool:
    """Başlıklar ya da konu satırı tipik bir otomatik cevaba (tatil/izin) mı benziyor?

    Gönderen adresi (`noreply@...` gibi) TEK BAŞINA sebep sayılmaz — bkz. modül docstring'i.
    """
    return _has_auto_reply_header(header) or matches_any(_AUTO_REPLY_SUBJECT_PATTERN, header.subject)


def _looks_like_opt_out(header: EmailHeader, body: EmailBody | None) -> bool:
    """Konu ya da gövdede "bir daha yazmayın / listeden çıkarın / ilgilenmiyoruz" türü bir ifade var mı?

    Metin eşleşmeden önce sadeleştirilir: boşluk/satır sonları tek boşluğa, kıvrık kesme işareti
    düz kesmeye çevrilir (ifadeler satır sonuyla bölünebilir, "don’t" yazımı farklı olabilir).
    """
    text = f"{header.subject or ''} {body.text if body else ''}"
    text = " ".join(text.replace("’", "'").split())
    return matches_any(_OPT_OUT_PATTERN, text)


def classify(
    session: Session,
    header: EmailHeader,
    body: EmailBody | None,
    *,
    matched_by: str,
) -> ClassifiedReply:
    """Bir cevabın kategorisini belirler. `matched_by`, `reply_matcher.match_reply()`nin
    döndürdüğü `MatchedReply.matched_by` değeridir (bounce kararı için gerekli)."""
    if matched_by == MATCH_BOUNCE:
        return ClassifiedReply(REPLY_BOUNCE, CLASSIFIED_BY_RULE)

    if _looks_like_auto_reply(header):
        return ClassifiedReply(REPLY_AUTO, CLASSIFIED_BY_RULE)

    if _looks_like_opt_out(header, body):
        return ClassifiedReply(REPLY_OPT_OUT, CLASSIFIED_BY_RULE)

    provider = get_provider(session)
    category = provider.classify_reply(header.subject, body.text if body else "")
    classified_by = (
        CLASSIFIED_BY_KEYWORD
        if provider.last_provider_name == KeywordProvider.name
        else CLASSIFIED_BY_LLM
    )
    return ClassifiedReply(category, classified_by)
