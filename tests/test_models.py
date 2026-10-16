import pytest
from sqlalchemy import delete, inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from src.core.db import get_session
from src.core.models import (
    Application,
    CLASSIFIED_BY_VALUES,
    Company,
    EmailReply,
    REPLY_CATEGORIES_ALL,
    Setting,
    normalize_domain,
    utcnow,
)
from tests.conftest import build_company as make_company


def test_init_db_creates_tables(engine):
    """init_db dört tabloyu da oluşturmalı."""
    table_names = set(inspect(engine).get_table_names())
    assert {"company", "application", "emailreply", "setting"} <= table_names


def test_company_domain_uniqueness_enforced(session):
    """Aynı domain ile ikinci Company eklenince commit hata vermeli."""
    session.add(make_company(domain="acme.com"))
    session.commit()

    session.add(make_company(name="Başka Şirket", domain="acme.com"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_company_delete_blocked_by_foreign_key(session):
    """Bağlı Application varken Company toplu DELETE ile silinemez (FK PRAGMA).

    ORM nesne silme yerine toplu SQL DELETE kullanılır; ORM silme, ilişkili
    satırın company_id'sini NULL yapmaya çalışıp önce NOT NULL hatasına
    çarpabilir ve asıl FK kontrolünü test etmeden geçebilir.
    """
    company = make_company(domain="acme.com")
    session.add(company)
    session.commit()

    session.add(Application(company_id=company.id))
    session.commit()

    with pytest.raises(IntegrityError) as exc_info:
        session.exec(delete(Company).where(Company.id == company.id))
        session.commit()
    assert "FOREIGN KEY" in str(exc_info.value)


def test_application_requires_company(session):
    """Var olmayan company_id ile Application eklenince FK hatası alınmalı."""
    session.add(Application(company_id=999))
    with pytest.raises(IntegrityError):
        session.commit()


def test_application_company_id_uniqueness_enforced(session):
    """Şirket başına en fazla bir Application kaydı olmalı (company_id benzersiz)."""
    company = make_company(domain="acme.com")
    session.add(company)
    session.commit()

    session.add(Application(company_id=company.id))
    session.commit()

    session.add(Application(company_id=company.id))
    with pytest.raises(IntegrityError):
        session.commit()


def test_email_reply_message_id_uniqueness(session):
    """Aynı message_id ile iki EmailReply eklenemez."""
    company = make_company(domain="acme.com")
    session.add(company)
    session.commit()

    application = Application(company_id=company.id)
    session.add(application)
    session.commit()

    session.add(
        EmailReply(
            application_id=application.id,
            from_addr="ik@acme.com",
            category="pending",
            classified_by="keyword",
            message_id="msg-1",
        )
    )
    session.commit()

    session.add(
        EmailReply(
            application_id=application.id,
            from_addr="ik@acme.com",
            category="rejected",
            classified_by="keyword",
            message_id="msg-1",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_email_reply_new_columns_default_to_none(session):
    """Faz 7 kolonları (in_reply_to, body_preview, matched_by, auto_category, updated_at)
    verilmezse None kalmalı; eski çağrı biçimi (yalnızca zorunlu alanlar) hâlâ çalışmalı."""
    company = make_company(domain="acme.com")
    session.add(company)
    session.commit()

    application = Application(company_id=company.id)
    session.add(application)
    session.commit()

    reply = EmailReply(
        application_id=application.id,
        from_addr="ik@acme.com",
        category="pending",
        classified_by="keyword",
        message_id="msg-2",
    )
    session.add(reply)
    session.commit()

    assert reply.in_reply_to is None
    assert reply.body_preview is None
    assert reply.matched_by is None
    assert reply.auto_category is None
    assert reply.updated_at is None


def test_email_reply_new_columns_roundtrip(engine, session):
    """Faz 7 kolonları doldurulunca commit sonrası aynen geri okunmalı."""
    company = make_company(domain="acme.com")
    session.add(company)
    session.commit()

    application = Application(company_id=company.id)
    session.add(application)
    session.commit()

    now = utcnow()
    reply = EmailReply(
        application_id=application.id,
        from_addr="ik@acme.com",
        category="interview",
        classified_by="user",
        message_id="msg-3",
        in_reply_to="<gonderilen-mesaj-id>",
        body_preview="Merhaba, sizinle görüşmek isteriz.",
        matched_by="thread",
        auto_category="pending",
        updated_at=now,
    )
    session.add(reply)
    session.commit()
    reply_id = reply.id

    with get_session(engine=engine) as new_session:
        fetched = new_session.get(EmailReply, reply_id)
        assert fetched.in_reply_to == "<gonderilen-mesaj-id>"
        assert fetched.body_preview == "Merhaba, sizinle görüşmek isteriz."
        assert fetched.matched_by == "thread"
        assert fetched.auto_category == "pending"
        assert fetched.updated_at == now


def test_reply_category_and_classified_by_constants_cover_six_and_four_values():
    """REPLY_CATEGORIES_ALL 6, CLASSIFIED_BY_VALUES 4 değer içermeli (yazım hatasına karşı)."""
    assert REPLY_CATEGORIES_ALL == {
        "interview", "rejected", "pending", "auto_reply", "bounce", "opt_out",
    }
    assert CLASSIFIED_BY_VALUES == {"llm", "keyword", "rule", "user"}


def test_application_bounced_at_defaults_to_none_and_roundtrips(engine, session):
    """bounced_at varsayılan None; doldurulunca status/sent_at'e dokunmadan kalıcı olmalı."""
    company = make_company(domain="acme.com")
    session.add(company)
    session.commit()

    application = Application(company_id=company.id, status="sent", sent_at=utcnow())
    session.add(application)
    session.commit()
    assert application.bounced_at is None

    bounce_time = utcnow()
    application.bounced_at = bounce_time
    session.commit()
    application_id = application.id

    with get_session(engine=engine) as new_session:
        fetched = new_session.get(Application, application_id)
        assert fetched.bounced_at == bounce_time
        assert fetched.status == "sent"  # bounce, status'u DEĞİŞTİRMEZ (kural 4 korunur)


def test_setting_json_roundtrip(session):
    """Setting.value içindeki iç içe yapı birebir geri okunmalı."""
    value = {"daily": 20, "hourly": 3, "windows": [{"start": "09:00", "end": "18:00"}]}
    session.add(Setting(key="rate_limit", value=value))
    session.commit()

    fetched = session.get(Setting, "rate_limit")
    assert fetched.value == value


def test_application_sent_at_used_for_rate_limit_query(session):
    """sent_at >= pencere_başlangıcı sorgusu doğru sayımı vermeli."""
    from datetime import timedelta

    now = utcnow()
    window_start = now - timedelta(hours=1)

    companies = [make_company(domain=f"firma{i}.com") for i in range(3)]
    session.add_all(companies)
    session.commit()

    session.add(Application(company_id=companies[0].id, sent_at=None))
    session.add(Application(company_id=companies[1].id, sent_at=now - timedelta(hours=2)))
    session.add(Application(company_id=companies[2].id, sent_at=now - timedelta(minutes=10)))
    session.commit()

    count = len(
        session.exec(select(Application).where(Application.sent_at >= window_start)).all()
    )
    assert count == 1


def test_mutable_json_columns_persist_in_place_changes(engine, session):
    """sector_tags listesine .append() ile yapılan değişiklik commit sonrası kalıcı olmalı."""
    company = make_company(domain="acme.com", sector_tags=["backend"])
    session.add(company)
    session.commit()
    company_id = company.id

    company.sector_tags.append("veri-bilimi")
    session.commit()

    with get_session(engine=engine) as new_session:
        fetched = new_session.get(Company, company_id)
        assert fetched.sector_tags == ["backend", "veri-bilimi"]


def test_found_emails_and_email_is_role_roundtrip(engine, session):
    """found_emails (JSON liste) ve email_is_role commit sonrası doğru okunmalı."""
    found = [{"email": "kariyer@acme.com", "source_url": "https://acme.com/iletisim", "is_role": True}]
    company = make_company(
        domain="acme.com", email="kariyer@acme.com", email_is_role=True, found_emails=found
    )
    session.add(company)
    session.commit()
    company_id = company.id

    with get_session(engine=engine) as new_session:
        fetched = new_session.get(Company, company_id)
        assert fetched.email_is_role is True
        assert fetched.found_emails == found


def test_email_is_role_defaults_to_none_when_no_email():
    """E-posta yoksa email_is_role None kalmalı ('kontrol edilmedi' değil, 'e-posta yok')."""
    company = make_company(domain="acme.com")
    assert company.email_is_role is None
    assert company.found_emails == []


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://www.Firma.com/iletisim", "firma.com"),
        ("http://Firma.com", "firma.com"),
        ("www.firma.com/", "firma.com"),
        ("firma.com", "firma.com"),
        ("wwwstore.com", "wwwstore.com"),
        ("firma.com?utm_source=x", "firma.com"),
        ("https://firma.com/a/b?x=1#y", "firma.com"),
        ("firma.com#iletisim", "firma.com"),
        ("http://firma.com:8080/x", "firma.com"),
        ("firma.com.", "firma.com"),
        ("  Firma.com  ", "firma.com"),
        # Türkçe "İ" düz .lower() ile birleşik nokta (U+0307) bırakır.
        ("İLKFİRMA.com", "ilkfirma.com"),
        # Büyük harfli ASCII alan adı: turkish_lower kullanılırsa "fırma.com" olurdu.
        ("WWW.FIRMA.COM", "firma.com"),
        ("", ""),
    ],
)
def test_normalize_domain(raw, expected):
    """normalize_domain şema/www/yol/sorgu/çapa/port farklarını tekilleştirmeli."""
    assert normalize_domain(raw) == expected


def test_normalize_domain_does_not_validate():
    """Biçimlendirici olduğu için anlamsız değerleri de geçirir; doğrulama çağıranın işi."""
    assert normalize_domain("yok") == "yok"
    assert normalize_domain("Acme Ltd") == "acme ltd"
