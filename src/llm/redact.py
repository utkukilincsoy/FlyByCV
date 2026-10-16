"""Gelen mail metnini LLM'e göndermeden önce kişisel bilgilerden arındırır.

Cevap sınıflandırması için LLM'in ihtiyacı olan tek şey mailin tonudur: davet mi, red mi,
belirsiz mi. Gönderenin adı, telefonu, e-postası ve imza bloğu bu karara katkı sağlamaz;
dışarıya çıkmasına da gerek yoktur. `llm/json_provider.py` bu yüzden her `classify_reply`
çağrısından önce `redact()` uygular.

Bilgi SİLİNMEZ, yer tutucuyla DEĞİŞTİRİLİR (`[eposta]`, `[telefon]`, `[baglanti]`) —
böylece cümlenin yapısı bozulmaz ve LLM orada bir şey olduğunu bilir.

İmza kesme kuralı iki yönden dikkatli:
  1. Bir imza işareti ("İyi çalışmalar", "Saygılarımla"...) ancak SATIRIN BAŞINDA ve o satırda
     KENDİ BAŞINA duruyorsa imza sayılır. Aksi halde "Merhaba, iyi çalışmalar. Maalesef
     başvurunuz olumsuz..." cümlesinde mailin tek gerçek sinyali ("Maalesef") kesilirdi.
  2. Metinde birden fazla işaret varsa SONUNCUSU kesme noktası olur (mail zincirlerinde
     alıntılanmış eski imzalar üstte kalabilir).
"""
import logging
import re

from src.core.text import fold_turkish

logger = logging.getLogger(__name__)

EMAIL_PLACEHOLDER = "[eposta]"
PHONE_PLACEHOLDER = "[telefon]"
URL_PLACEHOLDER = "[baglanti]"

# İmza kesildikten sonra en az bu kadar metin kalmalı. Kısa mailde imza satırı tek sinyal
# olabilir ("Saygılarımla" öncesi boşsa), onu da götürmeyelim.
MIN_KEPT_CHARS = 40

# Karşılaştırma `fold_turkish` ile yapıldığı için liste de katlanır; kaynakta doğru yazım kalsın
# diye burada çevrilir ("Saygılarımla" = "saygilarimla", Türkçe karakter kullanmayanlar da eşleşir).
_SIGNATURE_MARKERS = frozenset(
    fold_turkish(marker)
    for marker in (
        "Saygılarımla",
        "Saygılarımızla",
        "İyi çalışmalar",
        "İyi çalışmalar dilerim",
        "İyi günler",
        "Teşekkürler",
        "Best regards",
        "Kind regards",
        "Regards",
        "Sincerely",
        "Sent from my iPhone",
        "Sent from my Android",
        "Bu e-posta Android cihazımdan gönderildi",
    )
)

# İmza işaretinin sonunda olabilecek noktalama; "Saygılarımla," da "Saygılarımla:" da aynıdır.
_MARKER_TRAILING = " \t,.:;!-"

# Klasik imza ayıracı: yalnızca kısa çizgilerden oluşan bir satır ("--", "---", "-- ").
_DASH_LINE = re.compile(r"^-{2,}$")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Şema ya da "www." şart: böylece e-posta alan adları ve "3.5 kat" gibi ifadeler bağlantı sayılmaz.
_URL = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]]+", re.IGNORECASE)

# Telefon adayı. İki biçim kabul edilir:
#   1. "+90" ya da "0" ile BAŞLAYAN, aralarında ayırıcı olabilen bir dizi ("+90 532 111 22 33",
#      "0(532) 111-22-33"). Baştaki bu şart olmadan "45.000 - 60.000 TL" de telefon sanılıyordu.
#   2. Ayırıcısız 10-11 haneli tek blok ("5321112233").
# Satır sonu ayırıcı DEĞİLDİR (`\s` yerine boşluk yazıldı), yoksa alt alta yazılmış sayılar tek
# numara sanılırdı. Son kararı, rakam sayısına bakan `_replace_phone` verir.
# Bilinen sınır: başında 0 olmadan yazılan şehir içi numaralar ("212 555 12 34") yakalanmaz;
# bunu genişletmek tarih ve tutarları yeniden riske atıyor.
_PHONE_CANDIDATE = re.compile(r"(?:\+90|0)[\d ().\-]{7,}\d|(?<!\d)\d{10,11}(?!\d)")
_PHONE_MIN_DIGITS = 10
_PHONE_MAX_DIGITS = 13


def _is_signature_marker(line: str) -> bool:
    """Satır, kendi başına duran bir imza işareti mi?"""
    stripped = line.strip()
    if not stripped:
        return False
    if _DASH_LINE.match(stripped):
        return True
    cleaned = stripped.rstrip(_MARKER_TRAILING)
    return fold_turkish(cleaned) in _SIGNATURE_MARKERS


def _cut_signature(text: str) -> str:
    """İmza bloğunu ve sonrasını atar; güvenli bir kesme noktası yoksa metni olduğu gibi döner."""
    offset = 0
    cut_at: int | None = None
    for line in text.splitlines(keepends=True):
        if _is_signature_marker(line):
            cut_at = offset  # son eşleşme kazanır, döngü kırılmaz
        offset += len(line)

    if cut_at is None:
        return text

    kept = text[:cut_at].rstrip()
    if len(kept) < MIN_KEPT_CHARS:
        logger.debug("İmza kesilmedi: kalan metin çok kısa olurdu")
        return text
    return kept


def _replace_phone(match: re.Match[str]) -> str:
    """Aday diziyi yalnızca telefon numarası uzunluğundaysa yer tutucuyla değiştirir."""
    candidate = match.group(0)
    digits = sum(1 for char in candidate if char.isdigit())
    if _PHONE_MIN_DIGITS <= digits <= _PHONE_MAX_DIGITS:
        return PHONE_PLACEHOLDER
    return candidate


def redact(text: str) -> str:
    """Metinden imza bloğunu atar; e-posta, bağlantı ve telefonu yer tutucuya çevirir.

    Aynı metne ikinci kez uygulanması bir şeyi değiştirmez (idempotent) ve hiçbir girdi için
    hata fırlatmaz. Sıra önemlidir: e-posta bağlantıdan önce temizlenir, yoksa e-postanın
    alan adı bağlantı sanılabilir.
    """
    if not text:
        return ""

    result = _cut_signature(text)
    result = _EMAIL.sub(EMAIL_PLACEHOLDER, result)
    result = _URL.sub(URL_PLACEHOLDER, result)
    result = _PHONE_CANDIDATE.sub(_replace_phone, result)
    return result
