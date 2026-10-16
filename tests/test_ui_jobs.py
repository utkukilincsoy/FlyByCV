"""src/ui/jobs.py testleri (Qt YOK; işler senkron çağrılır, `engine` fixture'ı kullanılır)."""
import time
from datetime import datetime
from pathlib import Path

import pytest
from sqlmodel import select

from src.company_finder.email_extractor import FoundEmail
from src.company_finder.site_crawler import CrawlResult
from src.core import version as app_version
from src.core.db import get_session
from src.core.models import STATUS_APPROVED, STATUS_QUEUED, Application, Company
from src.core.settings import KEY_SENDING_PAUSED, MODE_MANUAL, set_setting
from src.core.update_check import UpdateInfo
from src.core.timeutil import ISTANBUL
from src.cv_builder.profile_loader import load_profile
from src.llm.providers import PROVIDER_NONE
from src.inbox.imap_reader import ImapConnectionError
from src.inbox.service import InboxCheckResult
from src.ui.jobs import (
    AutopilotRunJobResult,
    CancelToken,
    Cancelled,
    Progress,
    QueueRunJobResult,
    autopilot_run_job,
    build_job,
    cancellable_crawler,
    check_inbox_job,
    countdown_sleeper,
    enrich_job,
    import_job,
    llm_test_job,
    places_key_test_job,
    queue_run_job,
    send_one_job,
    smtp_login_test_job,
    update_check_job,
)
from tests.conftest import FakeClock, FakeTransport, build_application_row, build_company, build_mail_account

EXAMPLE_PROFILE_PATH = Path("data/master_profile.example.json")


def ist(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ISTANBUL)


#: `queue_run_job`/`process_queue` testleri gerçek saati KULLANMAMALI -- mesai/hafta içi kısıtı
#: gerçek saate bakar, test gün ortasında geçse de gece geçse de aynı sonucu vermeli. Sabit,
#: hafta içi ve mesai saati içinde bir an: 2026-09-28 Pazartesi 10:00 (`tests/test_worker.py`
#: ile aynı tarih).
WORKING_HOURS_INSTANT = ist(2026, 9, 28, 10, 0)


def _crawl_result(email: str, domain: str, *, is_role: bool = True) -> CrawlResult:
    return CrawlResult(
        website=f"https://{domain}",
        emails=[
            FoundEmail(
                email=email,
                source_url=f"https://{domain}/iletisim",
                is_role=is_role,
                on_company_domain=True,
            )
        ],
    )


# ---------------------------------------------------------------------------
# CancelToken / cancellable_crawler
# ---------------------------------------------------------------------------


def test_cancel_token_raises_only_after_cancel():
    token = CancelToken()
    token.raise_if_cancelled()  # henüz iptal edilmedi, patlamaz

    token.cancel()

    assert token.is_cancelled is True
    with pytest.raises(Cancelled):
        token.raise_if_cancelled()


def test_cancellable_crawler_raises_when_on_page_sees_cancel():
    token = CancelToken()

    def crawler(website, *, on_page=None):
        token.cancel()
        on_page(website, 0, 5)  # burada Cancelled fırlamalı
        return CrawlResult(website=website)  # asla buraya gelinmez

    wrapped = cancellable_crawler(crawler, token)

    with pytest.raises(Cancelled):
        wrapped("https://acme.com")


def test_cancel_token_wait_returns_false_when_not_cancelled():
    token = CancelToken()
    assert token.wait(0.02) is False


def test_cancel_token_wait_returns_true_immediately_after_cancel():
    token = CancelToken()
    token.cancel()

    start = time.monotonic()
    result = token.wait(5)
    elapsed = time.monotonic() - start

    assert result is True
    assert elapsed < 1  # 5 sn beklemeyi BEKLEMEDİ -- iptal hemen döndürdü


# ---------------------------------------------------------------------------
# enrich_job
# ---------------------------------------------------------------------------


def test_enrich_job_processes_and_commits(engine):
    with get_session(engine) as session:
        a = build_company(domain="a.com", name="A Şirketi")
        b = build_company(domain="b.com", name="B Şirketi")
        session.add(a)
        session.add(b)
        session.commit()
        ids = [a.id, b.id]

    def crawler(website, *, on_page=None):
        domain = website.removeprefix("https://")
        return _crawl_result(f"kariyer@{domain}", domain)

    progress_events: list[Progress] = []
    result = enrich_job(
        engine,
        ids,
        on_progress=progress_events.append,
        token=CancelToken(),
        crawler=crawler,
        mx=lambda domain: True,
    )

    assert result.processed == 2
    assert result.email_added == 2
    assert result.failed == []
    assert result.cancelled is False
    assert [event.current for event in progress_events] == [1, 2]
    assert progress_events[0].total == 2
    assert "A Şirketi" in progress_events[0].message

    with get_session(engine) as session:
        refreshed = session.get(Company, ids[0])
        assert refreshed.email == "kariyer@a.com"
        assert refreshed.mx_valid is True


def test_enrich_job_cancel_rolls_back_in_flight_company(engine):
    with get_session(engine) as session:
        a = build_company(domain="a.com", name="A")
        b = build_company(domain="b.com", name="B")
        session.add(a)
        session.add(b)
        session.commit()
        ids = [a.id, b.id]

    token = CancelToken()

    def crawler(website, *, on_page=None):
        # Bir sayfa "ziyaret edilir" (henüz iptal yok), SONRA iptal bayrağı çekilir --
        # `enrich_company` yine de normal döner ve `company.email`'i doldurur; bu değişikliğin
        # `enrich_job` tarafından geri alınması (rollback) gerekiyor.
        on_page(website, 0, 10)
        token.cancel()
        domain = website.removeprefix("https://")
        return _crawl_result(f"kariyer@{domain}", domain)

    result = enrich_job(
        engine, ids, on_progress=lambda p: None, token=token, crawler=crawler, mx=lambda d: True
    )

    assert result.cancelled is True
    assert result.processed == 0

    with get_session(engine) as session:
        refreshed = session.get(Company, ids[0])
        assert refreshed.email is None  # rollback -- yarım tarama kalıcı olmadı


def test_enrich_job_records_failed_when_crawl_result_has_error(engine):
    with get_session(engine) as session:
        a = build_company(domain="a.com", name="A")
        b = build_company(domain="b.com", name="B")
        session.add(a)
        session.add(b)
        session.commit()
        ids = [a.id, b.id]

    def crawler(website, *, on_page=None):
        if "a.com" in website:
            return CrawlResult(website=website, error="tarama başarısız oldu: zaman aşımı")
        domain = website.removeprefix("https://")
        return _crawl_result(f"kariyer@{domain}", domain)

    result = enrich_job(
        engine, ids, on_progress=lambda p: None, token=CancelToken(), crawler=crawler, mx=lambda d: True
    )

    assert result.processed == 2
    assert result.email_added == 1
    assert len(result.failed) == 1
    assert result.failed[0][0] == "a.com"


# ---------------------------------------------------------------------------
# build_job
# ---------------------------------------------------------------------------


def test_build_job_prepares_applications(engine, sample_profile):
    with get_session(engine) as session:
        a = build_company(domain="a.com", name="A", email="kariyer@a.com")
        b = build_company(domain="b.com", name="B", email="kariyer@b.com")
        session.add(a)
        session.add(b)
        session.commit()
        ids = [a.id, b.id]

    created: list[str] = []

    def fake_builder(session, company, profile, *, output_dir):
        created.append(company.domain)
        application = Application(company_id=company.id, mail_subject="Konu", mail_body="Metin")
        session.add(application)
        session.flush()
        return application

    result = build_job(
        engine, ids, sample_profile, on_progress=lambda p: None, token=CancelToken(), builder=fake_builder
    )

    assert result.prepared == 2
    assert result.failed == []
    assert created == ["a.com", "b.com"]  # şirket başına commit, sırayla

    with get_session(engine) as session:
        assert len(session.exec(select(Application)).all()) == 2


def test_build_job_continues_after_one_company_fails(engine, sample_profile):
    with get_session(engine) as session:
        a = build_company(domain="a.com", name="A", email="kariyer@a.com")
        b = build_company(domain="b.com", name="B", email="kariyer@b.com")
        session.add(a)
        session.add(b)
        session.commit()
        ids = [a.id, b.id]

    def failing_builder(session, company, profile, *, output_dir):
        if company.domain == "a.com":
            raise RuntimeError("PDF üretilemedi")
        application = Application(company_id=company.id)
        session.add(application)
        session.flush()
        return application

    result = build_job(
        engine, ids, sample_profile, on_progress=lambda p: None, token=CancelToken(), builder=failing_builder
    )

    assert result.prepared == 1
    assert len(result.failed) == 1
    assert result.failed[0][0] == "a.com"

    with get_session(engine) as session:
        assert len(session.exec(select(Application)).all()) == 1


def test_build_job_stops_when_cancelled_between_companies(engine, sample_profile):
    with get_session(engine) as session:
        a = build_company(domain="a.com", name="A", email="kariyer@a.com")
        b = build_company(domain="b.com", name="B", email="kariyer@b.com")
        session.add(a)
        session.add(b)
        session.commit()
        ids = [a.id, b.id]

    token = CancelToken()

    def builder(session, company, profile, *, output_dir):
        token.cancel()  # ilk şirket bitince iptal iste
        application = Application(company_id=company.id)
        session.add(application)
        session.flush()
        return application

    result = build_job(
        engine, ids, sample_profile, on_progress=lambda p: None, token=token, builder=builder
    )

    assert result.prepared == 1  # ilk şirket bitmişti, commit edildi
    assert result.cancelled is True


# ---------------------------------------------------------------------------
# import_job
# ---------------------------------------------------------------------------


def test_import_job_dry_run_does_not_write(engine, tmp_path):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("name,website\nAcme,acme.com\n", encoding="utf-8")

    result = import_job(engine, csv_path, dry_run=True, on_progress=lambda p: None, token=CancelToken())

    assert result.error is None
    assert result.report.added_count == 1
    assert result.committed is False

    with get_session(engine) as session:
        assert session.exec(select(Company)).all() == []


def test_import_job_real_run_commits(engine, tmp_path):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_text("name,website\nAcme,acme.com\n", encoding="utf-8")

    result = import_job(engine, csv_path, dry_run=False, on_progress=lambda p: None, token=CancelToken())

    assert result.error is None
    assert result.committed is True

    with get_session(engine) as session:
        companies = session.exec(select(Company)).all()
        assert len(companies) == 1
        assert companies[0].domain == "acme.com"


def test_import_job_file_level_error_is_turkish_and_does_not_raise(engine, tmp_path):
    bad_path = tmp_path / "companies.txt"
    bad_path.write_text("junk", encoding="utf-8")

    result = import_job(engine, bad_path, dry_run=True, on_progress=lambda p: None, token=CancelToken())

    assert result.report is None
    assert result.error is not None
    assert "Desteklenmeyen" in result.error or result.error  # Türkçe mesaj mevcut


# ---------------------------------------------------------------------------
# send_one_job (gerçek SMTP yok -- `FakeTransport`, `tests/conftest.no_real_smtp`)
# ---------------------------------------------------------------------------


def test_send_one_job_test_mode_redirects_and_leaves_record_queued(engine, sample_profile, pdf_path):
    with get_session(engine) as session:
        application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
        session.commit()
        application_id = application.id

    account = build_mail_account()
    transport = FakeTransport()

    result = send_one_job(
        engine,
        application_id,
        sample_profile,
        auto_mode=True,
        on_progress=lambda p: None,
        token=CancelToken(),
        transport=transport,
        account=account,
    )

    assert result.sent is True
    assert result.test_mode is True
    assert result.domain == "acme.com"
    assert len(transport.calls) == 1
    envelope_to = transport.calls[0][2]
    assert envelope_to == [account.identity.email]
    assert "kariyer@acme.com" not in envelope_to  # şirketin adresi test modunda hiç kullanılmadı

    with get_session(engine) as session:
        refreshed = session.get(Application, application_id)
        assert refreshed.status == STATUS_QUEUED  # test modunda kayda dokunulmadı


def test_send_one_job_blocked_when_sending_paused_is_reread_before_send(engine, sample_profile, pdf_path):
    """Onay diyalogları açıkken acil durdur başka bir pencereden açılmış olabilir."""
    with get_session(engine) as session:
        application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
        session.commit()
        application_id = application.id

        set_setting(session, KEY_SENDING_PAUSED, True)
        session.commit()

    transport = FakeTransport()
    result = send_one_job(
        engine,
        application_id,
        sample_profile,
        auto_mode=True,
        on_progress=lambda p: None,
        token=CancelToken(),
        transport=transport,
        account=build_mail_account(),
    )

    assert result.blocked_reason is not None
    assert transport.calls == []


def test_send_one_job_no_account_when_keyring_password_missing(engine, sample_profile, pdf_path, monkeypatch):
    with get_session(engine) as session:
        application, _ = build_application_row(session, domain="acme.com", cv_pdf_path=pdf_path)
        session.commit()
        application_id = application.id

    monkeypatch.setattr("src.ui.jobs.load_account", lambda identity, connection=None: None)
    transport = FakeTransport()

    result = send_one_job(
        engine,
        application_id,
        sample_profile,
        auto_mode=True,
        on_progress=lambda p: None,
        token=CancelToken(),
        transport=transport,
    )

    assert result.no_account is True
    assert transport.calls == []


def test_send_one_job_unknown_application_returns_error(engine, sample_profile):
    result = send_one_job(
        engine,
        999,
        sample_profile,
        auto_mode=True,
        on_progress=lambda p: None,
        token=CancelToken(),
        transport=FakeTransport(),
    )

    assert result.error is not None


# ---------------------------------------------------------------------------
# countdown_sleeper (5.2b: iptal edilebilir, geri sayım yayan bekleme)
# ---------------------------------------------------------------------------


def test_countdown_sleeper_ticks_decrease_and_end_at_zero():
    token = CancelToken()
    ticks: list[float] = []
    sleep = countdown_sleeper(token, ticks.append, tick_seconds=0.02)

    sleep(0.06)

    assert ticks[-1] == 0.0
    non_final = ticks[:-1]
    assert len(non_final) >= 1
    assert all(non_final[i] >= non_final[i + 1] for i in range(len(non_final) - 1))


def test_countdown_sleeper_cancel_returns_immediately_without_waiting_full_duration():
    token = CancelToken()
    token.cancel()
    ticks: list[float] = []
    sleep = countdown_sleeper(token, ticks.append, tick_seconds=0.01)

    start = time.monotonic()
    sleep(100)  # uzun bir süre verilse bile iptal edilmiş token'da anında bitmeli
    elapsed = time.monotonic() - start

    assert elapsed < 1
    assert ticks[-1] == 0.0


# ---------------------------------------------------------------------------
# queue_run_job (Onay Kuyruğu sekmesi -- "Gönderimi başlat")
# ---------------------------------------------------------------------------


def test_queue_run_job_sends_one_and_returns_run_result(engine, sample_profile, pdf_path):
    with get_session(engine) as session:
        build_application_row(session, domain="role.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
        session.commit()

    events: list = []
    countdowns: list[float] = []
    result = queue_run_job(
        engine,
        sample_profile,
        on_event=events.append,
        on_countdown=countdowns.append,
        token=CancelToken(),
        mode=MODE_MANUAL,
        max_count=1,
        transport=FakeTransport(),
        account=build_mail_account(),
        clock=FakeClock(WORKING_HOURS_INSTANT),
    )

    assert isinstance(result, QueueRunJobResult)
    assert result.blocked_reason is None
    assert result.no_account is False
    assert result.run is not None
    assert result.run.sent_count == 1
    assert events  # en az bir olay yayıldı ("başladı", "gönderildi", "durdu")


def test_queue_run_job_blocked_when_sending_paused(engine, sample_profile, pdf_path):
    with get_session(engine) as session:
        build_application_row(session, domain="role.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
        set_setting(session, KEY_SENDING_PAUSED, True)
        session.commit()

    transport = FakeTransport()
    result = queue_run_job(
        engine,
        sample_profile,
        on_event=lambda event: None,
        on_countdown=lambda seconds: None,
        token=CancelToken(),
        transport=transport,
        account=build_mail_account(),
        clock=FakeClock(WORKING_HOURS_INSTANT),
    )

    assert result.blocked_reason is not None
    assert result.run is None
    assert transport.calls == []


def test_queue_run_job_no_account_when_keyring_password_missing(engine, sample_profile, monkeypatch):
    monkeypatch.setattr("src.ui.jobs.load_account", lambda identity, connection=None: None)

    result = queue_run_job(
        engine,
        sample_profile,
        on_event=lambda event: None,
        on_countdown=lambda seconds: None,
        token=CancelToken(),
        transport=FakeTransport(),
        clock=FakeClock(WORKING_HOURS_INSTANT),
    )

    assert result.no_account is True
    assert result.run is None


def test_queue_run_job_stops_immediately_when_token_already_cancelled(engine, sample_profile, pdf_path):
    with get_session(engine) as session:
        build_application_row(session, domain="role.com", cv_pdf_path=pdf_path, status=STATUS_APPROVED)
        session.commit()

    token = CancelToken()
    token.cancel()
    transport = FakeTransport()

    result = queue_run_job(
        engine,
        sample_profile,
        on_event=lambda event: None,
        on_countdown=lambda seconds: None,
        token=token,
        transport=transport,
        account=build_mail_account(),
        clock=FakeClock(WORKING_HOURS_INSTANT),
    )

    assert transport.calls == []
    assert result.run is not None
    assert result.run.sent_count == 0
    assert result.run.stop_reason == "kullanıcı gönderimi durdurdu"


# ---------------------------------------------------------------------------
# autopilot_run_job (Otopilot sekmesi -- "Başlat")
#
# `run_autopilot()`'un kendi mantığı (keşif/tarama/hazırlama/gönderim) zaten
# `tests/test_autopilot_runner.py`'de kapsamlıca test edildi -- burada yalnızca `jobs.py`
# kabuğunun doğru plumbing yaptığı (on_event/on_countdown/token'ın gerçekten geçtiği, sonucun
# `AutopilotRunJobResult`e sarıldığı) doğrulanıyor. Gerçek ağa/discovery'ye hiç GİRİLMİYOR:
# her iki test de `run_autopilot`'un ÖN KONTROLDE ya da ilk turun başında (should_stop) durduğu
# yollarla sınırlı -- `queue_run_job`'un aynı adlı testleriyle aynı ilke.
# ---------------------------------------------------------------------------


def test_autopilot_run_job_blocked_when_sending_paused_reports_paused_result(engine, sample_profile):
    with get_session(engine) as session:
        set_setting(session, KEY_SENDING_PAUSED, True)
        session.commit()

    events: list = []
    transport = FakeTransport()

    result = autopilot_run_job(
        engine,
        sample_profile,
        on_event=events.append,
        on_countdown=lambda seconds: None,
        token=CancelToken(),
        transport=transport,
        account=build_mail_account(),
        clock=FakeClock(WORKING_HOURS_INSTANT),
    )

    assert isinstance(result, AutopilotRunJobResult)
    assert result.run is not None
    assert result.run.paused is True
    assert "duraklatılmış" in result.run.stop_reason
    assert transport.calls == []
    assert events  # ön kontrol sonucu da bir olay olarak yayıldı


def test_autopilot_run_job_no_account_when_keyring_password_missing(engine, sample_profile, monkeypatch):
    # `account=None` verilince hesabı `run_autopilot()` KENDİSİ okur (`src/autopilot/runner.py`
    # -- `jobs.py::queue_run_job`'un aksine, bkz. `autopilot_run_job` docstring'i); bu yüzden
    # yama `src.ui.jobs.load_account`'a DEĞİL, `src.autopilot.runner.load_account`'a yapılmalı.
    monkeypatch.setattr("src.autopilot.runner.load_account", lambda identity, connection=None: None)

    result = autopilot_run_job(
        engine,
        sample_profile,
        on_event=lambda event: None,
        on_countdown=lambda seconds: None,
        token=CancelToken(),
        transport=FakeTransport(),
        account=None,
        clock=FakeClock(WORKING_HOURS_INSTANT),
    )

    assert result.run is not None
    assert result.run.paused is False
    assert "Mail uygulama şifresi" in result.run.stop_reason


def test_autopilot_run_job_stops_immediately_when_token_already_cancelled(engine, sample_profile):
    token = CancelToken()
    token.cancel()
    transport = FakeTransport()
    countdowns: list[float] = []

    result = autopilot_run_job(
        engine,
        sample_profile,
        on_event=lambda event: None,
        on_countdown=countdowns.append,
        token=token,
        transport=transport,
        account=build_mail_account(),
        clock=FakeClock(WORKING_HOURS_INSTANT),
    )

    assert transport.calls == []
    assert result.run is not None
    assert result.run.sent_count == 0
    assert result.run.stop_reason == "kullanıcı Otopilot'u durdurdu"


# ---------------------------------------------------------------------------
# llm_test_job (Ayarlar sekmesi -- "Bağlantıyı test et", LLM; gerçek ağ/keyring yok)
# ---------------------------------------------------------------------------


class _FakeLLMProvider:
    """Ağa hiç çıkmayan sahte sağlayıcı; çağrıları saklar."""

    def __init__(self, tags=None, error=None):
        self._tags = tags if tags is not None else ["yapay-zeka"]
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def classify_company(self, name, about_text):
        self.calls.append((name, about_text))
        if self._error is not None:
            raise self._error
        return self._tags


def test_llm_test_job_none_provider_uses_keyword_without_network():
    """"none" sağlayıcısı seçiliyse anahtar da aranmaz, `build` de hiç çağrılmaz."""
    result = llm_test_job(
        PROVIDER_NONE,
        "",
        on_progress=lambda p: None,
        token=CancelToken(),
        build=lambda name, model, api_key: (_ for _ in ()).throw(AssertionError("çağrılmamalı")),
    )

    assert result.provider == PROVIDER_NONE
    assert result.no_key is False
    assert result.error is None


def test_llm_test_job_missing_key_sets_no_key_without_calling_build(monkeypatch):
    monkeypatch.setattr("src.ui.jobs.get_api_key", lambda provider: None)

    result = llm_test_job(
        "claude",
        "claude-haiku-4-5",
        on_progress=lambda p: None,
        token=CancelToken(),
        build=lambda name, model, api_key: (_ for _ in ()).throw(AssertionError("çağrılmamalı")),
    )

    assert result.no_key is True
    assert result.error is None


def test_llm_test_job_success_returns_filtered_tags(monkeypatch):
    """`filter_tags` gerçekten uygulanmalı: sözlük dışı etiket süzülmeli."""
    monkeypatch.setattr("src.ui.jobs.get_api_key", lambda provider: "sk-test-123")
    fake = _FakeLLMProvider(tags=["yapay-zeka", "sozlukte-olmayan-etiket"])

    result = llm_test_job(
        "claude",
        "claude-haiku-4-5",
        on_progress=lambda p: None,
        token=CancelToken(),
        build=lambda name, model, api_key: fake,
    )

    assert result.error is None
    assert result.no_key is False
    assert "yapay-zeka" in result.tags
    assert "sozlukte-olmayan-etiket" not in result.tags
    assert fake.calls  # gerçekten çağrıldı


def test_llm_test_job_provider_error_is_described_and_key_never_leaks(monkeypatch):
    secret = "sk-ant-cok-gizli-anahtar"
    monkeypatch.setattr("src.ui.jobs.get_api_key", lambda provider: secret)

    class _AuthError(Exception):
        status_code = 401

    fake = _FakeLLMProvider(error=_AuthError("yetkisiz"))

    result = llm_test_job(
        "claude",
        "claude-haiku-4-5",
        on_progress=lambda p: None,
        token=CancelToken(),
        build=lambda name, model, api_key: fake,
    )

    assert result.error is not None
    assert "401" in result.error
    assert secret not in result.error


def test_llm_test_job_build_error_is_described(monkeypatch):
    monkeypatch.setattr("src.ui.jobs.get_api_key", lambda provider: "sk-test-123")

    def _failing_build(name, model, api_key):
        raise ImportError("paket yok")

    result = llm_test_job(
        "gemini",
        "gemini-2.5-flash",
        on_progress=lambda p: None,
        token=CancelToken(),
        build=_failing_build,
    )

    assert result.error is not None
    assert "ImportError" in result.error


# ---------------------------------------------------------------------------
# smtp_login_test_job (Ayarlar sekmesi -- "Bağlantıyı test et", Gmail; gerçek SMTP/keyring yok)
# ---------------------------------------------------------------------------


def test_smtp_login_test_job_uses_given_sender_email_without_touching_profile(tmp_path, monkeypatch):
    """`sender_email` doluysa profil dosyasına hiç bakılmamalı (var olmayan bir yol verilir)."""
    monkeypatch.setattr(
        "src.ui.jobs.load_account", lambda identity, connection=None: build_mail_account(email=identity.email)
    )
    missing_profile = tmp_path / "olmayan.json"

    result = smtp_login_test_job(
        missing_profile,
        sender_email="ben@gmail.com",
        sender_name="Ben",
        on_progress=lambda p: None,
        token=CancelToken(),
        connect=lambda account: None,
        connect_imap=lambda account: None,
    )

    assert result.ok is True
    assert result.imap_ok is True
    assert result.email == "ben@gmail.com"
    assert result.error is None
    assert result.imap_error is None


def test_smtp_login_test_job_falls_back_to_profile_when_sender_email_empty(monkeypatch):
    monkeypatch.setattr(
        "src.ui.jobs.load_account", lambda identity, connection=None: build_mail_account(email=identity.email)
    )
    expected_email = load_profile(EXAMPLE_PROFILE_PATH).personal_info.email

    result = smtp_login_test_job(
        EXAMPLE_PROFILE_PATH,
        sender_email=None,
        sender_name=None,
        on_progress=lambda p: None,
        token=CancelToken(),
        connect=lambda account: None,
        connect_imap=lambda account: None,
    )

    assert result.ok is True
    assert result.imap_ok is True
    assert result.email == expected_email


def test_smtp_login_test_job_missing_profile_and_no_sender_email_returns_error(tmp_path):
    missing_profile = tmp_path / "olmayan.json"

    result = smtp_login_test_job(
        missing_profile,
        sender_email=None,
        sender_name=None,
        on_progress=lambda p: None,
        token=CancelToken(),
        connect=lambda account: None,
    )

    assert result.ok is False
    assert result.error is not None


def test_smtp_login_test_job_no_account_when_keyring_password_missing(monkeypatch):
    monkeypatch.setattr("src.ui.jobs.load_account", lambda identity, connection=None: None)

    result = smtp_login_test_job(
        EXAMPLE_PROFILE_PATH,
        sender_email="ben@gmail.com",
        sender_name=None,
        on_progress=lambda p: None,
        token=CancelToken(),
        connect=lambda account: None,
    )

    assert result.no_account is True
    assert result.ok is False


def test_smtp_login_test_job_connect_error_is_reported(monkeypatch):
    monkeypatch.setattr(
        "src.ui.jobs.load_account", lambda identity, connection=None: build_mail_account(email=identity.email)
    )

    result = smtp_login_test_job(
        EXAMPLE_PROFILE_PATH,
        sender_email="ben@gmail.com",
        sender_name=None,
        on_progress=lambda p: None,
        token=CancelToken(),
        connect=lambda account: "bağlantı hatası: zaman aşımı",
        connect_imap=lambda account: None,
    )

    assert result.ok is False
    assert result.error == "bağlantı hatası: zaman aşımı"
    # SMTP başarısız olsa da IMAP AYRICA/bağımsız denenmeli.
    assert result.imap_ok is True


def test_smtp_login_test_job_imap_error_is_reported_independently_of_smtp(monkeypatch):
    """IMAP başarısız olsa da SMTP AYRICA/bağımsız denenmeli; ikisinin sonucu ayrı tutulur."""
    monkeypatch.setattr(
        "src.ui.jobs.load_account", lambda identity, connection=None: build_mail_account(email=identity.email)
    )

    result = smtp_login_test_job(
        EXAMPLE_PROFILE_PATH,
        sender_email="ben@gmail.com",
        sender_name=None,
        on_progress=lambda p: None,
        token=CancelToken(),
        connect=lambda account: None,
        connect_imap=lambda account: "IMAP bağlantı hatası: zaman aşımı",
    )

    assert result.ok is True
    assert result.imap_ok is False
    assert result.imap_error == "IMAP bağlantı hatası: zaman aşımı"


def test_smtp_login_test_job_does_not_call_transport(monkeypatch):
    """Bu iş mail GÖNDERMEMELİ -- `connect` yalnızca login/hata metni döner, gönderim yapmaz."""
    calls = []
    monkeypatch.setattr(
        "src.ui.jobs.load_account", lambda identity, connection=None: build_mail_account(email=identity.email)
    )

    def _connect(account):
        calls.append(account)
        return None

    smtp_login_test_job(
        EXAMPLE_PROFILE_PATH,
        sender_email="ben@gmail.com",
        sender_name=None,
        on_progress=lambda p: None,
        token=CancelToken(),
        connect=_connect,
        connect_imap=lambda account: None,
    )

    assert len(calls) == 1


def test_smtp_login_test_job_no_account_skips_both_smtp_and_imap(monkeypatch):
    """Kasada şifre yoksa (aynı hesap ikisi için de kullanıldığından) ikisi de HİÇ denenmemeli."""
    smtp_calls = []
    imap_calls = []
    monkeypatch.setattr("src.ui.jobs.load_account", lambda identity, connection=None: None)

    result = smtp_login_test_job(
        EXAMPLE_PROFILE_PATH,
        sender_email="ben@gmail.com",
        sender_name=None,
        on_progress=lambda p: None,
        token=CancelToken(),
        connect=lambda account: smtp_calls.append(account),
        connect_imap=lambda account: imap_calls.append(account),
    )

    assert result.no_account is True
    assert smtp_calls == []
    assert imap_calls == []


# ---------------------------------------------------------------------------
# check_inbox_job (Cevaplar sekmesi -- "Şimdi kontrol et"; `check_inbox()` sahtelenir, gerçek
# IMAP/keyring hiç yok -- birleştirme mantığının kendisi zaten `test_inbox_service.py`de)
# ---------------------------------------------------------------------------


def test_check_inbox_job_returns_result_on_success(engine, sample_profile):
    fake_result = InboxCheckResult(headers_fetched=3, candidates=1, new_replies=1)

    result = check_inbox_job(
        engine,
        sample_profile,
        on_progress=lambda p: None,
        token=CancelToken(),
        checker=lambda session, profile: fake_result,
    )

    assert result.connection_error is None
    assert result.error is None
    assert result.no_applications_sent is False
    assert result.result is fake_result


def test_check_inbox_job_surfaces_no_applications_sent_as_non_error(engine, sample_profile):
    """"Henüz gönderilmiş başvuru yok" bir hata değil, bilgilendirme -- sekme bunu ayırt etmeli."""
    fake_result = InboxCheckResult(
        error="Henüz gönderilmiş başvuru yok, taranacak bir şey yok", no_applications_sent=True
    )

    result = check_inbox_job(
        engine,
        sample_profile,
        on_progress=lambda p: None,
        token=CancelToken(),
        checker=lambda session, profile: fake_result,
    )

    assert result.no_applications_sent is True
    assert result.error == "Henüz gönderilmiş başvuru yok, taranacak bir şey yok"
    assert result.connection_error is None


def test_check_inbox_job_surfaces_setup_error(engine, sample_profile):
    fake_result = InboxCheckResult(error="Gmail uygulama şifresi kayıtlı değil")

    result = check_inbox_job(
        engine,
        sample_profile,
        on_progress=lambda p: None,
        token=CancelToken(),
        checker=lambda session, profile: fake_result,
    )

    assert result.no_applications_sent is False
    assert result.error == "Gmail uygulama şifresi kayıtlı değil"


def test_check_inbox_job_catches_imap_connection_error(engine, sample_profile):
    """`ImapConnectionError` burada YAKALANIR -- `JobWorker`'ın genel hata yoluna düşmemeli."""

    def _raise(session, profile):
        raise ImapConnectionError("bağlantı zaman aşımına uğradı")

    result = check_inbox_job(
        engine,
        sample_profile,
        on_progress=lambda p: None,
        token=CancelToken(),
        checker=_raise,
    )

    assert result.connection_error == "bağlantı zaman aşımına uğradı"
    assert result.result is None
    assert result.error is None


# ---------------------------------------------------------------------------
# places_key_test_job (Ayarlar sekmesi -- "Anahtarı test et", Google Places; gerçek ağ/keyring yok)
# ---------------------------------------------------------------------------


def test_places_key_test_job_without_key_does_not_call_google(monkeypatch):
    monkeypatch.setattr("src.ui.jobs.get_api_key", lambda provider: None)

    result = places_key_test_job(
        on_progress=lambda p: None,
        token=CancelToken(),
        check=lambda key: (_ for _ in ()).throw(AssertionError("çağrılmamalı")),
    )

    assert result.no_key is True
    assert result.error is None


def test_places_key_test_job_reads_places_key_and_reports_success_and_error(monkeypatch):
    asked = []
    monkeypatch.setattr("src.ui.jobs.get_api_key", lambda provider: asked.append(provider) or "K-1")

    ok = places_key_test_job(on_progress=lambda p: None, token=CancelToken(), check=lambda key: None)
    bad = places_key_test_job(
        on_progress=lambda p: None, token=CancelToken(), check=lambda key: "Anahtar geçersiz"
    )

    assert asked == ["google_places", "google_places"]
    assert ok.no_key is False and ok.error is None
    assert bad.error == "Anahtar geçersiz"


# ---------------------------------------------------------------------------
# update_check_job (yeni sürüm kontrolü, Adım 10.9d; gerçek ağ YOK -- `check` sahte)
# ---------------------------------------------------------------------------


def test_update_check_job_without_repo_touches_neither_network_nor_check(engine, monkeypatch):
    monkeypatch.setattr(app_version, "GITHUB_REPO", None)
    result = update_check_job(
        engine,
        force=True,
        on_progress=lambda p: None,
        token=CancelToken(),
        check=lambda *a, **k: (_ for _ in ()).throw(AssertionError("çağrılmamalı")),
        repo=None,
    )
    # `GITHUB_REPO` None olursa: yapılandırılmamış olarak döner.
    assert result.configured is False and result.info is None


def test_update_check_job_passes_force_and_repo_and_returns_the_info(engine):
    seen = []
    info = UpdateInfo(version="1.2.0", url="https://github.com/a/b/releases/latest")

    def fake_check(session, *, force, repo):
        seen.append((force, repo))
        return info

    for force in (False, True):
        result = update_check_job(
            engine, force=force, on_progress=lambda p: None, token=CancelToken(),
            check=fake_check, repo="a/b",
        )
        assert result.configured is True and result.info == info
    assert seen == [(False, "a/b"), (True, "a/b")]


def test_update_check_job_reports_no_info_when_nothing_newer(engine):
    result = update_check_job(
        engine, force=False, on_progress=lambda p: None, token=CancelToken(),
        check=lambda session, *, force, repo: None, repo="a/b",
    )
    assert result.configured is True and result.info is None
