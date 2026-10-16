"""`src/autopilot/runner.py` testleri (PLAN.md Faz 9 Adım 9.5).

Hiçbir test gerçek ağa (HTTP/SMTP/IMAP), gerçek SMTP'ye ya da gerçek saate bağlanmaz:
keşif kaynakları (`TeknokentSource`), tarayıcı (`crawler`), MX kontrolü (`mx`), gelen kutusu
kontrolü (`check_inbox_fn`), gönderim (`transport`) ve zaman (`clock`/`sleeper`) HER ZAMAN
sahte/enjekte edilir — `tests/conftest.py` (`FakeClock`, `FakeSleeper`, `FakeTransport`,
`build_mail_account`) ile aynı kalıp (`tests/test_worker.py`).
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from src.autopilot import runner
from src.core.db import get_session
from src.core.models import Application, STATUS_QUEUED, utcnow
from src.core.settings import (
    KEY_DISCOVERY_STATE,
    KEY_HOURLY_LIMIT,
    KEY_SEND_MODE,
    KEY_SENDING_PAUSED,
    KEY_TEST_MODE,
    MODE_MANUAL,
    get_setting,
    set_setting,
)
from src.core.timeutil import ISTANBUL
from src.company_finder.email_extractor import FoundEmail
from src.company_finder.site_crawler import CrawlResult
from src.company_finder.teknokent_source import TeknokentLead, TeknokentSource
from tests.conftest import (
    FakeClock,
    FakeSleeper,
    FakeTransport,
    build_application_row,
    build_company,
    build_mail_account,
)

# Sabit tarihler: test_worker.py ile aynı -- 2026-09-28 Pazartesi (mesai içi),
# 2026-09-27 Pazar (hafta sonu).


def ist(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ISTANBUL)


def _raising_crawler(website: str) -> CrawlResult:
    raise AssertionError(f"bu şirket tekrar TARANMAMALIYDI: {website}")


def _raising_mx(domain: str):
    raise AssertionError(f"bu alan adı için MX tekrar KONTROL EDİLMEMELİYDİ: {domain}")


def _raising_discover(*, fetcher=None, sleeper=None):
    raise AssertionError("bu kaynak tekrar KEŞFEDİLMEMELİYDİ (zaten tamamlanmıştı)")


def _no_op_check_inbox(session, profile):
    """`check_inbox_fn` için varsayılan sahte: hiç gönderim yokmuş gibi davranır, gerçek IMAP'e
    hiç dokunmaz. Testlerin çoğu gelen kutusu konusunu değil, başka bir şeyi test ediyor."""
    return SimpleNamespace(
        no_applications_sent=True, new_replies=0, candidates=0, bounced_domains=[], bounce_rate_paused=None
    )


@pytest.fixture(autouse=True)
def no_real_keyring(monkeypatch):
    """`run_autopilot`'un Google Places anahtarı kontrolü gerçek kasaya dokunmasın."""
    monkeypatch.setattr(runner.secrets, "get_api_key", lambda provider: None)


@pytest.fixture
def rendered(monkeypatch):
    """`render_pdf`'i sahte bir üreticiyle değiştirir; Playwright hiç çalışmaz.

    `tests/test_application_builder.py::rendered` ile aynı kalıp.
    """

    def fake_render_pdf(profile, tailored, out_path):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF-1.4 test")
        return out_path

    monkeypatch.setattr("src.cv_builder.application_builder.render_pdf", fake_render_pdf)


# --- Uçtan uca tek çalıştırma; "Bugünlük iş bitti" ile durur -----------------------------------


def test_full_cycle_discovers_crawls_prepares_and_sends(session, sample_profile, tmp_path, rendered):
    """Boş bir veritabanından başlayıp iki şirketi keşfeder, tarar, hazırlar ve gönderir."""

    leads = [
        TeknokentLead(name="Firma Bir", website="https://firma1.com", teknokent="Sahte"),
        TeknokentLead(name="Firma Iki", website="https://firma2.com", teknokent="Sahte"),
    ]

    def fake_discover(*, fetcher=None, sleeper=None):
        yield from leads

    source = TeknokentSource(key="sahte", name="Sahte Teknokent", discover=fake_discover)

    def fake_crawler(website: str) -> CrawlResult:
        domain = website.removeprefix("https://")
        return CrawlResult(
            website=website,
            emails=[
                FoundEmail(
                    email=f"kariyer@{domain}",
                    source_url=website,
                    is_role=True,
                    on_company_domain=True,
                )
            ],
            about_text="Şirketimiz yazılım alanında hizmet vermektedir, ekibimiz genç ve dinamiktir.",
            visited_urls=[website],
        )

    transport = FakeTransport()
    sleeper = FakeSleeper()
    events = []

    result = runner.run_autopilot(
        session,
        sample_profile,
        output_dir=tmp_path / "output",
        discovery_sources=[source],
        fetcher=None,
        discovery_sleeper=lambda seconds: None,
        crawler=fake_crawler,
        mx=lambda domain: True,
        check_inbox_fn=_no_op_check_inbox,
        sleeper=sleeper,
        clock=FakeClock(ist(2026, 9, 28, 10, 0)),
        transport=transport,
        account=build_mail_account(),
        on_event=events.append,
    )

    assert result.discovery_rounds == 1
    assert result.crawled == 2
    assert result.prepared == 2
    assert result.sent_count == 2
    assert sorted(result.sent_domains) == ["firma1.com", "firma2.com"]
    assert len(transport.calls) == 2
    assert result.paused is False

    # Madde 4: tükenince "Bugünlük iş bitti" ile durur, boş bir döngüye girip asılı kalmaz
    # (bu test zaten bittiği için otomatik olarak da doğrulanıyor).
    assert result.stop_reason.startswith(runner.IDLE_MESSAGE_PREFIX)

    # Keşif kaynağı kalıcı olarak "bitti" işaretlenmiş olmalı.
    state = get_setting(session, KEY_DISCOVERY_STATE)
    assert state["teknokent:sahte"]["done"] is True

    # "Verimli bekleme": en az bir gönderimler-arası bekleme gerçekleşti (2. şirketin
    # taranması bu beklemenin İÇİNDE yapıldı, ayrı bir tur beklemedi).
    assert len(sleeper.sleeps) >= 1

    applications = session.exec(select(Application)).all()
    assert len(applications) == 2
    assert all(app.test_sent_at is not None for app in applications)  # test modu varsayılan açık
    assert all(app.status == STATUS_QUEUED for app in applications)  # test modunda status değişmez

    # Madde 3: her aşama için bir AutopilotEvent yayılmış olmalı.
    stages = {event.stage for event in events}
    assert runner.STAGE_DISCOVER in stages
    assert runner.STAGE_CRAWL in stages
    assert runner.STAGE_PREPARE in stages
    assert runner.STAGE_SEND in stages
    assert runner.STAGE_IDLE in stages
    assert all(event.message for event in events)


# --- Kapanıp yeniden açma: hiçbir şey tekrarlanmaz --------------------------------------------


def test_reopen_does_not_repeat_discovery_crawl_or_send(file_engine, sample_profile, tmp_path, rendered):
    """Ayrı bir DB dosyası + iki AYRI oturum ile "kapat / yeniden aç" gerçekçi biçimde simüle edilir."""

    # --- "İlk açılış": durum zaten tamamlanmış olarak kurulur (keşif bitmiş, şirket başarıyla
    # taranmış, test modunda gönderim yapılmış) ---
    with get_session(engine=file_engine) as setup_session:
        set_setting(setup_session, KEY_DISCOVERY_STATE, {"teknokent:sahte": {"done": True}})
        company = build_company(
            domain="firma1.com",
            email="kariyer@firma1.com",
            email_is_role=True,
            mx_valid=True,
            about_text="Zaten taranmış bir şirket.",
            crawled_at=utcnow(),
            crawl_failed=False,
        )
        setup_session.add(company)
        setup_session.flush()
        application = Application(
            company_id=company.id,
            status=STATUS_QUEUED,
            mail_subject="Staj Başvurusu",
            mail_body="Sayın İnsan Kaynakları Ekibi,\n\nMerhaba.\n\nSaygılarımla.",
            cv_pdf_path=str(tmp_path / "cv.pdf"),
            test_sent_at=utcnow(),
        )
        setup_session.add(application)
        setup_session.commit()
        (tmp_path / "cv.pdf").write_bytes(b"%PDF-1.4 test")

    source = TeknokentSource(key="sahte", name="Sahte Teknokent", discover=_raising_discover)
    transport = FakeTransport()

    # --- "Yeniden açılış": run_autopilot YENİ bir oturumla ikinci kez çağrılır ---
    with get_session(engine=file_engine) as reopened_session:
        result = runner.run_autopilot(
            reopened_session,
            sample_profile,
            output_dir=tmp_path / "output",
            discovery_sources=[source],
            crawler=_raising_crawler,
            mx=_raising_mx,
            check_inbox_fn=_no_op_check_inbox,
            sleeper=FakeSleeper(),
            clock=FakeClock(ist(2026, 9, 28, 10, 0)),
            transport=transport,
            account=build_mail_account(),
        )

    assert transport.calls == []
    assert result.sent_count == 0
    assert result.crawled == 0
    assert result.prepared == 0
    assert result.discovery_rounds == 0
    assert result.refused_count == 1  # test_sent_at doluydu, guard reddetti (ağa çıkılmadı)


# --- Acil durdur / güvenlik freni: HER ŞEYİ anında durdurur, HİÇ başlamaz ----------------------


def test_sending_paused_stops_before_anything_starts(session, sample_profile, tmp_path, rendered):
    """`sending_paused` açıkken Otopilot ÖN KONTROLDE durur; keşif/tarama/hazırlama denenmez."""
    company = build_company(
        domain="firma1.com", email="kariyer@firma1.com", email_is_role=True, mx_valid=True
    )
    session.add(company)
    session.flush()
    set_setting(session, KEY_SENDING_PAUSED, True)
    session.commit()

    source = TeknokentSource(key="sahte", name="Sahte Teknokent", discover=_raising_discover)
    transport = FakeTransport()

    result = runner.run_autopilot(
        session,
        sample_profile,
        output_dir=tmp_path / "output",
        discovery_sources=[source],
        crawler=_raising_crawler,
        mx=_raising_mx,
        check_inbox_fn=_no_op_check_inbox,
        sleeper=FakeSleeper(),
        clock=FakeClock(ist(2026, 9, 28, 10, 0)),
        transport=transport,
        account=build_mail_account(),
    )

    assert result.paused is True
    assert "duraklatılmış" in result.stop_reason
    assert transport.calls == []
    assert result.discovery_rounds == 0
    assert result.crawled == 0
    assert result.prepared == 0
    # firma1.com'un e-postası var ve başvurusu yok -- duraklatılmamış olsaydı hazırlanırdı.
    assert session.exec(select(Application)).all() == []


# --- Ön kontrol (Madde 1): Gmail hesabı yoksa ya da hiç kaynak yoksa hiç başlamaz --------------


def test_stops_with_turkish_reason_when_no_gmail_account(session, sample_profile, monkeypatch):
    monkeypatch.setattr(runner, "load_account", lambda identity, connection=None: None)

    result = runner.run_autopilot(
        session,
        sample_profile,
        discovery_sources=[],
        crawler=_raising_crawler,
        mx=_raising_mx,
        check_inbox_fn=_no_op_check_inbox,
        sleeper=FakeSleeper(),
        clock=FakeClock(ist(2026, 9, 28, 10, 0)),
        transport=FakeTransport(),
        account=None,
    )

    assert result.paused is False
    assert "Mail uygulama şifresi" in result.stop_reason
    assert result.sent_count == 0


def test_stops_with_turkish_reason_when_no_sources_and_no_companies(session, sample_profile):
    """Ne keşif kaynağı ne veritabanında bir şirket varsa yapılacak hiçbir iş yoktur."""
    result = runner.run_autopilot(
        session,
        sample_profile,
        discovery_sources=[],  # boş -- teknokent kaynağı verilmedi
        crawler=_raising_crawler,
        mx=_raising_mx,
        check_inbox_fn=_no_op_check_inbox,
        sleeper=FakeSleeper(),
        clock=FakeClock(ist(2026, 9, 28, 10, 0)),
        transport=FakeTransport(),
        account=build_mail_account(),
    )

    assert result.paused is False
    assert "kaynak yok" in result.stop_reason
    assert result.sent_count == 0


# --- SendWindow.code: mesai dışı yalnızca GÖNDERİMİ durdurur, hazırlığı DEĞİL -------------------


def test_outside_working_hours_still_prepares_but_does_not_send(session, sample_profile, tmp_path, rendered):
    company = build_company(domain="firma1.com")  # e-postasız -- taranması gerekiyor
    session.add(company)
    session.flush()

    def fake_crawler(website: str) -> CrawlResult:
        return CrawlResult(
            website=website,
            emails=[
                FoundEmail(
                    email="kariyer@firma1.com", source_url=website, is_role=True, on_company_domain=True
                )
            ],
            about_text="Yeterince uzun bir tanıtım metni burada yer alıyor, epey kelime var.",
            visited_urls=[website],
        )

    result = runner.run_autopilot(
        session,
        sample_profile,
        output_dir=tmp_path / "output",
        discovery_sources=[],  # bu testte keşif konusu değil
        crawler=fake_crawler,
        mx=lambda domain: True,
        check_inbox_fn=_no_op_check_inbox,
        sleeper=FakeSleeper(),
        clock=FakeClock(ist(2026, 9, 27, 10, 0)),  # Pazar -- hafta sonu, mesai dışı
        transport=FakeTransport(),
        account=build_mail_account(),
    )

    assert result.paused is False
    assert result.crawled == 1
    assert result.prepared == 1
    assert result.sent_count == 0
    assert "hafta sonu" in result.stop_reason
    assert result.stop_reason.startswith(runner.IDLE_MESSAGE_PREFIX)


# --- Madde 5: saatlik limitte hazırlık yaparak bekler, süre dolunca göndermeye devam eder -------


def test_hourly_limit_waits_then_resumes_sending(session, sample_profile, pdf_path):
    """Saatlik limit 1'e ayarlanır, iki hazır (tarama/hazırlama gerektirmeyen) başvuru var.

    İlki gönderilir, saatlik limit dolar; hazırlık da tükenmiş olduğu için Otopilot pencere
    açılana kadar BEKLER (boşa "iş bitti" demeden) ve ikinciyi de gönderir.
    """
    set_setting(session, KEY_HOURLY_LIMIT, 1)
    build_application_row(session, domain="role1.com", cv_pdf_path=pdf_path, company_overrides={"crawled_at": utcnow()})
    build_application_row(session, domain="role2.com", cv_pdf_path=pdf_path, company_overrides={"crawled_at": utcnow()})
    session.commit()

    transport = FakeTransport()
    # `sleeper` gerçek zamanı GEÇİRMEDİĞİ için `clock`'a bağlanmalı: aksi halde saatlik limit
    # beklemesi "beklese" bile sahte saat hiç ilerlemez ve pencere asla açılmaz (sonsuz döngü).
    clock = FakeClock(ist(2026, 9, 28, 10, 0))
    sleeper = FakeSleeper(clock=clock)
    events = []

    result = runner.run_autopilot(
        session,
        sample_profile,
        discovery_sources=[],
        crawler=_raising_crawler,
        mx=_raising_mx,
        check_inbox_fn=_no_op_check_inbox,
        sleeper=sleeper,
        clock=clock,
        transport=transport,
        account=build_mail_account(),
        on_event=events.append,
    )

    assert result.sent_count == 2
    assert sorted(result.sent_domains) == ["role1.com", "role2.com"]
    assert len(transport.calls) == 2
    assert any(event.stage == runner.STAGE_WAITING for event in events)
    # En az iki bekleme oldu: normal gönderimler-arası bekleme + saatlik limit beklemesi.
    assert len(sleeper.sleeps) >= 2


# --- Yalnızca is_auto_eligible (rol adresli) başvurular gönderilir, mod ayarından bağımsız ------


def test_sends_only_role_address_applications_regardless_of_send_mode_setting(
    session, sample_profile, pdf_path
):
    build_application_row(
        session,
        domain="person.com",
        cv_pdf_path=pdf_path,
        company_overrides={"email_is_role": False, "crawled_at": utcnow()},
    )
    build_application_row(
        session, domain="role.com", cv_pdf_path=pdf_path, company_overrides={"crawled_at": utcnow()}
    )
    # Genel gönderim ayarı "manuel" olsa bile Otopilot HER ZAMAN otomatik mod kurallarını
    # (rol adresi + MX zorunlu) kullanır -- bu ayardan ETKİLENMEMELİ.
    set_setting(session, KEY_SEND_MODE, MODE_MANUAL)
    session.commit()

    transport = FakeTransport()
    result = runner.run_autopilot(
        session,
        sample_profile,
        discovery_sources=[],
        crawler=_raising_crawler,
        mx=_raising_mx,
        check_inbox_fn=_no_op_check_inbox,
        sleeper=FakeSleeper(),
        clock=FakeClock(ist(2026, 9, 28, 10, 0)),
        transport=transport,
        account=build_mail_account(),
    )

    assert result.sent_count == 1
    assert result.sent_domains == ["role.com"]


# --- Google Places (Adım 9.3) henüz yok: anahtar varsa bile yalnızca loglanır -------------------


def test_places_key_present_is_only_logged_not_used(session, sample_profile, monkeypatch, caplog):
    monkeypatch.setattr(runner.secrets, "get_api_key", lambda provider: "sahte-anahtar")
    # Ön kontrolün ("en az bir kaynak") geçmesi için -- keşif kaynağı vermiyoruz ama zaten bir
    # şirket kaydı var; taranmış/e-postasız olduğu için hazırlık hiçbir şey yapmayacak.
    company = build_company(domain="firma1.com", crawled_at=utcnow())
    session.add(company)
    session.flush()

    with caplog.at_level(logging.INFO):
        result = runner.run_autopilot(
            session,
            sample_profile,
            discovery_sources=[],
            crawler=_raising_crawler,
            mx=_raising_mx,
            check_inbox_fn=_no_op_check_inbox,
            sleeper=FakeSleeper(),
            clock=FakeClock(ist(2026, 9, 28, 10, 0)),
            transport=FakeTransport(),
            account=build_mail_account(),
        )

    assert "Google Places" in caplog.text
    assert result.sent_count == 0


# --- Keşif kalıcılığı (beyaz kutu): kaynak bittiğinde bir daha imleç bile kurulmaz --------------


def test_advance_discovery_marks_source_done_and_is_skipped_afterwards(session):
    leads = [TeknokentLead(name="A", website="https://a.com"), TeknokentLead(name="B", website="https://b.com")]

    def discover(*, fetcher=None, sleeper=None):
        yield from leads

    source = TeknokentSource(key="sahte", name="Sahte", discover=discover)

    cursors = runner._build_cursors(session, [source], fetcher=None, sleeper=lambda seconds: None)
    assert len(cursors) == 1

    progressed = runner._advance_discovery(session, cursors, batch_size=10)
    assert progressed is True
    assert runner._is_source_done(session, source) is True
    assert len(list(session.exec(select(Application.id)))) == 0  # bu test yalnızca keşfi ilgilendiriyor

    # "yeniden açılış": imleçler tekrar kurulur, artık bitmiş kaynak hiç listeye girmez.
    cursors_again = runner._build_cursors(session, [source], fetcher=None, sleeper=lambda seconds: None)
    assert cursors_again == []


# --- Madde 2: hazırlık birimlerinde 30 dk'da bir check_inbox çalışır -----------------------------


def test_check_inbox_one_respects_30_minute_gate(session, sample_profile):
    calls = []

    def fake_check_inbox(session_, profile_):
        calls.append(1)
        return SimpleNamespace(
            no_applications_sent=False,
            new_replies=1,
            candidates=1,
            bounced_domains=[],
            bounce_rate_paused=None,
        )

    gate = runner._InboxCheckGate()
    now = datetime(2026, 9, 28, 10, 0)

    assert runner._check_inbox_one(session, sample_profile, gate, now, fake_check_inbox) is True
    assert len(calls) == 1

    almost_due = now + timedelta(minutes=29)
    assert runner._check_inbox_one(session, sample_profile, gate, almost_due, fake_check_inbox) is False
    assert len(calls) == 1  # henüz 30 dk dolmadı, tekrar denenmedi

    due = now + timedelta(minutes=30)
    assert runner._check_inbox_one(session, sample_profile, gate, due, fake_check_inbox) is True
    assert len(calls) == 2


def test_run_autopilot_checks_inbox_as_prep_work_and_emits_event(session, sample_profile, tmp_path):
    """Yapılacak tek "iş" gelen kutusu kontrolüyse (şirket zaten tamamen hazır/gönderilmiş),
    Otopilot yine de gelen kutusunu kontrol etmeli ve bunu bir olay olarak yaymalı."""
    calls = []

    def fake_check_inbox(session_, profile_):
        calls.append(1)
        return SimpleNamespace(
            no_applications_sent=False,
            new_replies=2,
            candidates=3,
            bounced_domains=["firma1.com"],
            bounce_rate_paused=None,
        )

    company = build_company(
        domain="firma1.com",
        email="kariyer@firma1.com",
        email_is_role=True,
        mx_valid=True,
        crawled_at=utcnow(),
    )
    session.add(company)
    session.flush()
    cv_path = tmp_path / "cv.pdf"
    cv_path.write_bytes(b"%PDF-1.4 test")
    application = Application(
        company_id=company.id,
        status=STATUS_QUEUED,
        mail_subject="Konu",
        mail_body="Gövde",
        cv_pdf_path=str(cv_path),
        test_sent_at=utcnow(),  # zaten test modunda gönderilmiş -- tekrar gönderilmeyecek
    )
    session.add(application)
    session.commit()

    events = []
    result = runner.run_autopilot(
        session,
        sample_profile,
        discovery_sources=[],
        crawler=_raising_crawler,
        mx=_raising_mx,
        check_inbox_fn=fake_check_inbox,
        sleeper=FakeSleeper(),
        clock=FakeClock(ist(2026, 9, 28, 10, 0)),
        transport=FakeTransport(),
        account=build_mail_account(),
        on_event=events.append,
    )

    assert len(calls) == 1
    assert result.inbox_checks == 1
    assert any(event.stage == runner.STAGE_INBOX for event in events)
    assert result.sent_count == 0  # tek başvuru zaten test modunda gönderilmişti


# --- Madde 3: load_autopilot_stats() saf özet fonksiyonu ----------------------------------------


def test_load_autopilot_stats_summarizes_pipeline(session):
    set_setting(session, KEY_TEST_MODE, False)  # gerçek `sent_at` sayılsın (bkz. yorum aşağıda)
    no_email = build_company(domain="bos.com")
    role_sent = build_company(
        domain="role.com", email="kariyer@role.com", email_is_role=True, mx_valid=True
    )
    person_queued = build_company(
        domain="person.com", email="ahmet@person.com", email_is_role=False, mx_valid=True
    )
    session.add_all([no_email, role_sent, person_queued])
    session.flush()

    session.add(
        Application(
            company_id=role_sent.id,
            status="sent",
            mail_subject="Konu",
            mail_body="Gövde",
            sent_at=utcnow(),  # GERÇEK gönderim -- "bugün giden" sayacına girer
        )
    )
    session.add(
        Application(
            company_id=person_queued.id,
            status=STATUS_QUEUED,
            mail_subject="Konu",
            mail_body="Gövde",
        )
    )
    session.commit()

    stats = runner.load_autopilot_stats(session, clock=FakeClock(ist(2026, 9, 28, 10, 0)))

    assert stats.companies_found == 3
    assert stats.companies_with_email == 2
    assert stats.role_address_count == 1
    assert stats.person_address_count == 1
    assert stats.sent_today == 1
    assert stats.daily_limit == 20  # varsayılan
    assert stats.queued_count == 1  # yalnızca person_queued'un başvurusu queued
    assert stats.next_send_at is not None
    assert stats.next_send_reason is None  # Pazartesi 10:00, mesai içi, limit dolu değil


def test_load_autopilot_stats_reports_paused_state(session):
    set_setting(session, KEY_SENDING_PAUSED, True)
    session.commit()

    stats = runner.load_autopilot_stats(session, clock=FakeClock(ist(2026, 9, 28, 10, 0)))

    assert stats.next_send_at is None
    assert stats.next_send_reason is not None
