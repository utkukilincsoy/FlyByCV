import os

# PySide6 import edilmeden ÖNCE kurulmalı: görünmez (offscreen) ekran sürücüsü, testlerde
# gerçek pencere açılmasın. setdefault: geliştirici isterse dışarıdan ezebilir.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTEST_QT_API", "pyside6")

import imaplib
import smtplib
from datetime import timedelta
from typing import Callable, Optional

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from src.core.db import configure_sqlite_engine, get_session, init_db
from src.core.models import (
    CLASSIFIED_BY_KEYWORD,
    REPLY_PENDING,
    STATUS_QUEUED,
    Application,
    Company,
    EmailReply,
)
from src.cv_builder.profile_loader import (
    Achievement,
    Education,
    Experience,
    Language,
    MasterProfile,
    PersonalInfo,
    Project,
    Skill,
)
from src.sender.smtp_sender import MailAccount, MailIdentity


def _experience(id_: str, tags: list[str]) -> Experience:
    return Experience(
        id=id_,
        tags=tags,
        company="Şirket",
        title="Unvan",
        start_date="2020",
        bullets=["Açıklama"],
    )


def _education(id_: str, tags: list[str]) -> Education:
    return Education(
        id=id_,
        tags=tags,
        school="Okul",
        field="Bölüm",
        degree="Lisans",
        start_date="2016-09",
    )


def _project(id_: str, tags: list[str]) -> Project:
    return Project(id=id_, tags=tags, name=id_, bullets=["Açıklama"], technologies=[])


def build_sample_profile() -> MasterProfile:
    """Small in-memory profile; no real personal data.

    A plain function as well as a fixture, so tests that need it in a
    wider scope (slow PDF rendering) can build it themselves.
    """
    return MasterProfile(
        personal_info=PersonalInfo(name="Test Kişi", email="test@example.com"),
        summaries={"default": "Varsayılan özet", "yapay-zeka": "YZ özeti"},
        experience=[_experience("exp_1", ["backend"]), _experience("exp_2", ["yapay-zeka"])],
        education=[_education("edu_1", ["backend"]), _education("edu_2", ["elektronik"])],
        projects=[
            _project("proj_1", ["backend"]),
            _project("proj_2", ["yapay-zeka"]),
            _project("proj_3", ["yapay-zeka", "veri-bilimi"]),
            _project("proj_4", ["veri-bilimi"]),
        ],
        skills=[
            Skill(id="skill_1", tags=["backend"], name="Python"),
            Skill(id="skill_2", tags=["yapay-zeka"], name="PyTorch"),
            Skill(id="skill_3", tags=["veri-bilimi"], name="Pandas"),
        ],
        achievements=[
            Achievement(id="ach_1", tags=["arge"], title="Ödül", description="Açıklama"),
            Achievement(id="ach_2", tags=["backend"], title="Sertifika", description="Açıklama"),
        ],
        languages=[
            Language(name="Türkçe", level="Anadil"),
            Language(name="İngilizce", level="B2"),
        ],
    )


@pytest.fixture(autouse=True)
def no_real_api_keys(monkeypatch):
    """Testlerin kazara gerçek bir LLM sağlayıcısına bağlanmasını engeller.

    SDK'lar API anahtarını ortam değişkeninden (ve `anthropic` ayrıca makinedeki CLI
    oturumundan) kendiliğinden bulabiliyor. "Anahtar vermedim, çağrı yapamaz" varsayımı bu
    yüzden yanlıştır; bu fixture ortamı temizleyerek varsayımı doğru hale getirir.
    """
    for variable in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture(autouse=True)
def no_legacy_home_env(monkeypatch):
    """Eski ev değişkeni (`CV_AUTOPILOT_HOME`) geliştiricinin kabuğunda kalmışsa testlere sızmasın.

    `FLYBYCV_HOME` yokken eski değişken yedek olarak okunur (`paths.home_override`); testler
    `FLYBYCV_HOME`'u ayarlar ya da siler, eski değişkeni yalnızca onu sınayan testler ayarlar.
    """
    monkeypatch.delenv("CV_AUTOPILOT_HOME", raising=False)


@pytest.fixture
def sample_profile() -> MasterProfile:
    return build_sample_profile()


@pytest.fixture(autouse=True)
def no_real_smtp(monkeypatch):
    """Testlerin kazara gerçek bir SMTP sunucusuna bağlanmasını engeller.

    `no_real_api_keys` ile aynı mantık: "transport'u enjekte ediyorum, ağa çıkamam" varsayımı
    bir gün yanlış olacak (unutulan bir varsayılan parametre, yeni bir çağrı yolu...); bu
    fixture ortamı öyle bir hataya karşı güvenli hale getirir.
    """
    def _refuse_connection(self, host, port, timeout):
        raise AssertionError("Test gerçek bir SMTP bağlantısı kurmaya çalıştı")

    monkeypatch.setattr(smtplib.SMTP, "_get_socket", _refuse_connection)


@pytest.fixture(autouse=True)
def no_real_imap(monkeypatch):
    """Testlerin kazara gerçek bir IMAP sunucusuna bağlanmasını engeller (bkz. `no_real_smtp`).

    Testler `imaplib.IMAP4_SSL`i (ör. `test_imap_reader.py`) tamamen kendi sahte sınıflarıyla
    DEĞİŞTİRDİĞİNDE bu yama hiç devreye girmez -- yalnızca hiç sahtelenmeden gerçek sınıfa
    ulaşan bir çağrıyı yakalar.
    """
    def _refuse_connection(self, *args, **kwargs):
        raise AssertionError("Test gerçek bir IMAP bağlantısı kurmaya çalıştı")

    monkeypatch.setattr(imaplib.IMAP4_SSL, "__init__", _refuse_connection)


@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):
    """Site tarayıcısının SSRF denetiminin (`url_safety`) testte gerçek DNS'e gitmesini engeller.

    `no_real_smtp` ile aynı mantık: testler sahte bir `resolver` verir; unutulan bir varsayılan
    gerçek DNS sorgusuna (ağa) dönüşmesin.
    """
    def _refuse_resolution(host: str) -> list[str]:
        raise AssertionError(f"Test gerçek bir DNS çözümlemesi yapmaya çalıştı: {host}")

    monkeypatch.setattr("src.company_finder.url_safety._system_resolve", _refuse_resolution)


@pytest.fixture(autouse=True)
def no_real_relaunch(monkeypatch):
    """Geri yükleme sonrası yeniden başlatmanın (`ui/restart.py`) testte gerçek bir süreç
    açmasını engeller (bkz. `no_real_smtp`). Testler sahte bir başlatıcı verir."""
    from PySide6.QtCore import QProcess

    def _refuse_start(*args, **kwargs):
        raise AssertionError("Test gerçek bir süreç başlatmaya çalıştı (QProcess.startDetached)")

    monkeypatch.setattr(QProcess, "startDetached", staticmethod(_refuse_start))


@pytest.fixture(autouse=True)
def legal_consent_given(monkeypatch):
    """Gönderim testleri KVKK/6563 onayını (Adım 10.8b) zaten verilmiş varsayar.

    Veritabanına onay satırı YAZILMAZ (ayar satırı sayan/"var olan kullanıcı" tespit eden testler
    bozulmasın diye); yalnızca gönderim geçidinin baktığı `has_consent` sahtelenir. Onay
    davranışını sınayan testler (`tests/test_legal.py`) gerçek fonksiyonu geri yükler.
    """
    monkeypatch.setattr("src.sender.smtp_sender.has_consent", lambda session: True)


def build_company(**overrides) -> Company:
    """Küçük bir Company nesnesi; domain zorunlu alanlarla birlikte verilir."""
    defaults = dict(name="Test A.Ş.", domain="test.com", source="manual")
    defaults.update(overrides)
    return Company(**defaults)


@pytest.fixture
def engine():
    """Yabancı anahtar (FK) denetimi açık, bellek içi SQLite motoru.

    StaticPool şart: "sqlite://" her yeni bağlantıda boş bir veritabanı açar.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    configure_sqlite_engine(engine)
    init_db(engine=engine)
    return engine


@pytest.fixture
def session(engine):
    with get_session(engine=engine) as session:
        yield session


@pytest.fixture
def pdf_path(tmp_path):
    """Gerçek bir Playwright çalıştırmadan, `cv_pdf_path` için sahte bir PDF dosyası."""
    path = tmp_path / "cv.pdf"
    path.write_bytes(b"%PDF-1.4 test icerik")
    return path


@pytest.fixture
def file_engine(tmp_path):
    """Dosya tabanlı bir SQLite motoru (`tmp_path` altında).

    Bellek içi `engine` fixture'ı (StaticPool) TEK bir DBAPI bağlantısını paylaştırır — "iki
    oturum" aslında aynı işlem olur, gerçek izolasyonu/kilidi test edemez. Bu fixture, başka
    bir pencereden yapılan bir değişikliğin (örn. `sending_paused`) GERÇEKTEN ayrı bir
    bağlantıdan görülüp görülmediğini test etmek için kullanılır (bkz. worker'ın "acil durdur"
    testi).
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    configure_sqlite_engine(engine)
    init_db(engine=engine)
    return engine


def build_application_row(
    session, *, domain: str, cv_pdf_path, status: str = STATUS_QUEUED, company_overrides=None, **overrides
) -> tuple[Application, Company]:
    """Bir `Company` + gönderime hazır bir `Application` kaydı oluşturur (kuyruk/worker testleri).

    Varsayılan olarak rol adresi + MX doğrulanmış bir şirket üretir (otomatik moda uygun);
    `company_overrides` ile değiştirilebilir (örn. `email_is_role=False`).
    """
    company_kwargs = dict(domain=domain, email=f"kariyer@{domain}", email_is_role=True, mx_valid=True)
    company_kwargs.update(company_overrides or {})
    company = build_company(**company_kwargs)
    session.add(company)
    session.flush()

    application_kwargs = dict(
        company_id=company.id,
        status=status,
        mail_subject=f"Staj Başvurusu – {domain}",
        mail_body="Sayın İnsan Kaynakları Ekibi,\n\nMerhaba, öğrenciyim.\n\nSaygılarımla.",
        cv_pdf_path=str(cv_pdf_path),
    )
    application_kwargs.update(overrides)
    application = Application(**application_kwargs)
    session.add(application)
    session.flush()
    return application, company


def build_email_reply(
    session, *, application_id: int, message_id: str, **overrides
) -> EmailReply:
    """Bir `EmailReply` kaydı oluşturur (Cevaplar sekmesi testleri, `src/ui/replies_*`)."""
    kwargs = dict(
        application_id=application_id,
        from_addr="ik@firma.com",
        subject="Re: Başvurunuz",
        category=REPLY_PENDING,
        classified_by=CLASSIFIED_BY_KEYWORD,
        message_id=message_id,
        auto_category=REPLY_PENDING,
    )
    kwargs.update(overrides)
    reply = EmailReply(**kwargs)
    session.add(reply)
    session.flush()
    return reply


def build_mail_account(**overrides) -> MailAccount:
    """Testler için hazır bir `MailAccount` (gerçek şifre kasasına dokunmaz)."""
    password = overrides.pop("password", "gizli-sifre-1234")
    identity_kwargs = dict(email="ben@gmail.com", name="Test Kişi")
    identity_kwargs.update(overrides)
    return MailAccount(identity=MailIdentity(**identity_kwargs), password=password)


class FakeClock:
    """Enjekte edilebilir "şu an"; `advance()` ile ilerletilir (gerçek zaman geçmez)."""

    def __init__(self, start):
        self.current = start

    def __call__(self):
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class FakeSleeper:
    """Gerçekte uyumaz; süreleri kaydeder. `clock` verilirse onu da ilerletir (tutarlı zaman)."""

    def __init__(self, clock: Optional[FakeClock] = None, on_sleep: Optional[Callable[[int], None]] = None):
        self.sleeps: list[float] = []
        self.clock = clock
        self.on_sleep = on_sleep

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.clock is not None:
            self.clock.advance(seconds)
        if self.on_sleep is not None:
            self.on_sleep(len(self.sleeps))


class FakeTransport:
    """Ağa hiç çıkmayan sahte gönderim fonksiyonu; `errors` sırayla tüketilir (tükenince başarılı)."""

    def __init__(self, errors=None):
        self.calls: list[tuple] = []
        self._errors = iter(errors or [])

    def __call__(self, account, message, recipients) -> None:
        self.calls.append((account, message, list(recipients)))
        error = next(self._errors, None)
        if error is not None:
            raise error
