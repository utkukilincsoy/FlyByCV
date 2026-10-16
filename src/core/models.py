import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import NaiveDatetime
from sqlalchemy import Column, JSON
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlmodel import Field, Relationship, SQLModel


def utcnow() -> datetime:
    """Return a naive UTC timestamp (SQLite stores no timezone info)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Timestamp fields are typed as NaiveDatetime, not datetime: SQLModel maps a
# plain `datetime` to a column that requires timezone-aware values, which
# would reject the naive UTC timestamps utcnow() returns.


def normalize_domain(value: str) -> str:
    """Normalize a URL/domain to a comparable canonical form.

    Lowercases and strips the http(s):// scheme, a leading "www." label, any
    path, query string, fragment, port and trailing dots. Every place that
    creates a Company must call this before storing `domain` (see CLAUDE.md):
    the same company reached as "firma.com" and "firma.com?utm_source=x" would
    otherwise be stored twice and be mailed twice.

    This formats, it does not validate: "yok" and "" pass through unchanged.
    Callers decide whether the result is a usable domain (see
    `src/company_finder/csv_import.py`).

    Example: "https://www.Firma.com/iletisim?utm=1" -> "firma.com"
    """
    # Turkish "İ" under a plain .lower() becomes "i" + a combining dot (U+0307),
    # and the domain then never matches again. turkish_lower() must NOT be used
    # here: it maps "I" to "ı", which would corrupt an all-caps ASCII domain
    # like "WWW.FIRMA.COM". Domains want ASCII lowercasing.
    domain = value.strip().replace("İ", "i").lower()
    # The scheme goes first: otherwise the port split below would cut at "https:".
    domain = re.sub(r"^https?://", "", domain)
    domain = re.sub(r"^www\.", "", domain)
    domain = re.split(r"[/?#]", domain, maxsplit=1)[0]
    domain = domain.split(":")[0]
    return domain.strip(".")


class Company(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    website: Optional[str] = None
    domain: str = Field(unique=True, index=True)
    email: Optional[str] = None
    # "csv"/"manual" (hangi boru hattı verdi) VEYA tarayıcının adresi bulduğu sayfanın tam
    # URL'si (src/company_finder/enricher.py). email boşsa None.
    email_source: Optional[str] = None
    city: Optional[str] = None
    sector_tags: list[str] = Field(
        default_factory=list, sa_column=Column(MutableList.as_mutable(JSON))
    )
    about_text: Optional[str] = None
    source: str  # "csv" | "google" | "manual" | "teknokent"
    mx_valid: Optional[bool] = None  # None = henüz kontrol edilmedi
    #: Sitede bulunan tüm adresler, en iyisi başta. Her öğe:
    #: {"email": ..., "source_url": ..., "is_role": bool}. `email` alanı bunlardan seçilir;
    #: arayüz (Faz 5) yanlış seçimi buradan değiştirebilir.
    found_emails: list[dict] = Field(
        default_factory=list, sa_column=Column(MutableList.as_mutable(JSON))
    )
    #: `email` bir rol adresi mi (kariyer@, info@) yoksa kişi adı mı (ahmet.yilmaz@)?
    #: None = e-posta yok. False olan adrese otomatik gönderim YASAK (CLAUDE.md 10. kural).
    email_is_role: Optional[bool] = None
    #: Bu şirket sitesi hiç taranmış mı (Faz 9 — Otopilot). `enrich_company` başarılı/başarısız
    #: fark etmeden (bir daha KENDİLİĞİNDEN denenmesin diye) taramanın sonunda bunu yazar; None =
    #: hiç taranmadı. Otopilot yalnızca bu None olan şirketleri tarama kuyruğuna alır, aksi halde
    #: her açılışta aynı şirket tekrar taranırdı.
    crawled_at: Optional[NaiveDatetime] = None
    #: Son tarama GEÇİCİ bir hatayla mı bitti (zaman aşımı, bağlantı hatası, 5xx)? True ise
    #: `company_finder/enricher.is_crawl_due()` `crawled_at`'ten birkaç gün sonra şirketi tekrar
    #: tarama kuyruğuna alınabilir sayar. Kalıcı bir sonuçtan (site açıldı ama e-posta yok,
    #: robots.txt engelledi, alan adı geçersiz) sonra False kalır -- bir daha KENDİLİĞİNDEN
    #: denenmez. None/False = son tarama kalıcıydı (ya da hiç taranmadı).
    crawl_failed: Optional[bool] = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)

    application: Optional["Application"] = Relationship(
        back_populates="company",
        sa_relationship_kwargs={"uselist": False},
    )


# Application.status'un alabileceği değerler. Enum DEĞİL, düz dizgi sabitleri: kolon `str`
# ve bir Enum eşlemesi şema değişikliği demek olurdu (var olan data/app.db'nin silinmesi
# gerekirdi), kazancı ise buradaki kadar az sayıda değer için sıfıra yakın.
STATUS_QUEUED = "queued"
STATUS_APPROVED = "approved"  # kullanıcı onay kuyruğunda onayladı (src/sender/queue.py)
#: Gerçek gönderim sırasında, `transport()` çağrısından hemen önce yazılıp COMMIT edilir
#: (src/sender/smtp_sender.py) — Ctrl+C/çökme mail gitmiş olsa da kaydın "queued" kalıp
#: ikinci kez gönderilmesini önleyen bir write-ahead işareti. Asla otomatik/manuel modda
#: SEÇİLMEZ (src/sender/queue.py); kurtarma `queue.mark_sent`/`queue.mark_queued` ile.
STATUS_SENDING = "sending"
STATUS_REJECTED = "rejected"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"


class Application(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", unique=True, index=True)
    status: str = Field(default=STATUS_QUEUED, index=True)  # bkz. STATUS_* sabitleri
    tailored: bool = Field(default=False)
    matched_tags: list[str] = Field(
        default_factory=list, sa_column=Column(MutableList.as_mutable(JSON))
    )
    cv_pdf_path: Optional[str] = None
    mail_subject: Optional[str] = None
    mail_body: Optional[str] = None
    message_id: Optional[str] = Field(default=None, index=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    sent_at: Optional[NaiveDatetime] = Field(default=None, index=True)
    error: Optional[str] = None
    #: Bu başvuruya giden mail geri döndü mü (Faz 7 — src/inbox). `status`/`sent_at`'e
    #: DOKUNULMAZ (kural 4 bozulmaz, kuyruk/gönderim kodu bu alanı hiç okumaz); yalnızca
    #: Cevaplar sekmesinde ve olası bir istatistikte kullanılan ayrı bir bilgi notudur.
    #: None = bounce hiç görülmedi.
    bounced_at: Optional[NaiveDatetime] = None
    #: Test modunda BAŞARILI bir gönderim yapıldı mı (Faz 9 — Otopilot). `status`/`sent_at`/`error`
    #: gibi gerçek gönderim alanlarına HİÇ dokunmaz (Faz 6.1 kararı korunur); yalnızca
    #: `check_send_allowed()`'ın test modunda aynı başvuruyu tekrar tekrar test maili olarak
    #: göndermesini engellemek için kalıcı bir işarettir — `tried_ids` (worker.py) yalnızca TEK
    #: çalıştırma içinde korur, bu alan çalıştırmalar arasında (Otopilot kapanıp açılsa da) korur.
    #: None = test modunda hiç gönderilmedi.
    test_sent_at: Optional[NaiveDatetime] = None

    company: Company = Relationship(back_populates="application")
    email_replies: list["EmailReply"] = Relationship(back_populates="application")


# EmailReply.category'nin alabileceği değerler (Faz 7). `src/llm/base.py` bunları buradan
# alır (tek kaynak): LLM'in döndürebileceği üç eski değer + otomatik cevap; bounce LLM'e HİÇ
# gitmez (src/inbox/classifier.py kuralla yakalar), bu yüzden LLM'in izinli kümesinde YOKTUR.
REPLY_INTERVIEW = "interview"
REPLY_REJECTED = "rejected"
REPLY_PENDING = "pending"
REPLY_AUTO = "auto_reply"  # otomatik alındı bildirimi / tatil-izin yanıtı (kural veya LLM)
REPLY_BOUNCE = "bounce"  # teslim edilemedi (mailer-daemon), yalnızca kuralla belirlenir
#: "Bir daha yazmayın / listeden çıkarın / ilgilenmiyoruz" (Faz 10 Adım 10.8b). Bounce gibi
#: yalnızca KURALLA (anahtar kelime) belirlenir, LLM'in izinli kümesinde YOKTUR -- böylece LLM
#: bir cevabı yanlışlıkla "engelle" diye işaretleyemez ve LLM'siz kurulumda da aynı çalışır. Bu
#: kategoriye düşen cevabın şirketi kalıcı engellenir (`src/core/blocklist.py`).
REPLY_OPT_OUT = "opt_out"
REPLY_CATEGORIES_ALL = frozenset(
    {REPLY_INTERVIEW, REPLY_REJECTED, REPLY_PENDING, REPLY_AUTO, REPLY_BOUNCE, REPLY_OPT_OUT}
)

# EmailReply.classified_by'ın alabileceği değerler: hangi mekanizma kategoriyi belirledi.
CLASSIFIED_BY_LLM = "llm"
CLASSIFIED_BY_KEYWORD = "keyword"
CLASSIFIED_BY_RULE = "rule"  # bounce/otomatik cevap gibi başlık/içerik kuralıyla (LLM'siz)
CLASSIFIED_BY_USER = "user"  # kullanıcı arayüzden elle düzeltti
CLASSIFIED_BY_VALUES = frozenset(
    {CLASSIFIED_BY_LLM, CLASSIFIED_BY_KEYWORD, CLASSIFIED_BY_RULE, CLASSIFIED_BY_USER}
)


class EmailReply(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    application_id: int = Field(foreign_key="application.id", index=True)
    from_addr: str
    subject: Optional[str] = None
    snippet: Optional[str] = None
    received_at: NaiveDatetime = Field(default_factory=utcnow)
    category: str  # bkz. REPLY_* sabitleri
    classified_by: str  # bkz. CLASSIFIED_BY_* sabitleri
    message_id: str = Field(unique=True, index=True)
    #: Bu cevabın hangi maile cevap olduğu (varsa) — eşleştirme izini görünür kılar, teşhis
    #: amaçlı. Eşleştirme başlıktan (`In-Reply-To`) okunur, `src/inbox/reply_matcher.py` yazar.
    in_reply_to: Optional[str] = None
    #: Mail gövdesinin ilk ~1000 karakteri (düz metin) — Cevaplar sekmesindeki önizleme.
    #: Yalnızca yerel veritabanında durur, dışarı hiç gönderilmez.
    body_preview: Optional[str] = None
    #: Bu kayıt hangi yöntemle bulundu: "thread" (Message-ID zinciri) | "domain" (gönderen
    #: alan adı) | "bounce" (DSN içindeki orijinal Message-ID). Teşhis amaçlı.
    matched_by: Optional[str] = None
    #: İlk otomatik sınıflandırma (kural/LLM/anahtar kelime). Kullanıcı `category`'yi elle
    #: değiştirirse (`classified_by` "user" olur) bu alan DEĞİŞMEZ — "başlangıçta ne
    #: denilmişti" bilgisi kaybolmasın.
    auto_category: Optional[str] = None
    #: Kayıt son ne zaman değişti (elle düzeltme dahil). Oluşturmada `received_at` ile aynı
    #: anda yazılır; sonraki her değişiklikte çağıran taraf elle günceller (`onupdate` yok —
    #: bu şekilde davranan başka bir kolon projede yok, bkz. `sent_at`/`error` deseni).
    updated_at: Optional[NaiveDatetime] = None

    application: Application = Relationship(back_populates="email_replies")


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: dict = Field(
        default_factory=dict, sa_column=Column(MutableDict.as_mutable(JSON))
    )


#: `BlockedDomain.reason` değerleri.
BLOCK_REASON_OPT_OUT = "opt_out"  # şirket "bir daha yazmayın" dedi (cevap sınıflandırması)
BLOCK_REASON_MANUAL = "manual"  # kullanıcı Cevaplar sekmesinden elle engelledi


class BlockedDomain(SQLModel, table=True):
    """Kalıcı engelli alan adı (PLAN.md Adım 10.8b): keşif, CSV, kuyruk ve gönderim bu alan
    adına HİÇBİR yoldan işlem yapmaz. `domain` `normalize_domain()`'dan geçmiş olarak yazılır.

    Şirket kaydı silinse de engel kalır (ayrı tablo) -- aynı alan adı bir kaynaktan tekrar
    gelirse yine atlanır. Engeli yalnızca kullanıcı kaldırır (`blocklist.unblock_domain`)."""

    domain: str = Field(primary_key=True)
    reason: str = Field(default=BLOCK_REASON_MANUAL)
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class PlaceRef(SQLModel, table=True):
    """Google Places'te daha önce görülmüş bir yer (PLAN.md Adım 10.6f).

    Google şartları yalnızca `place_id`'nin süresiz saklanmasına izin verir (ad/adres saklanamaz);
    bu tablo yalnızca "bu place_id'yi daha önce işledim, ikinci kez istek/kayıt harcama" bilgisidir.
    `company_id` EN İYİ ÇABA bir bağdır: yerin web sitesi yoksa ya da şirket başka bir yoldan
    eklenmemişse None kalır.
    """

    place_id: str = Field(primary_key=True)
    company_id: Optional[int] = Field(default=None, foreign_key="company.id")
    checked_at: NaiveDatetime = Field(default_factory=utcnow)
