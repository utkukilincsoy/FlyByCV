"""`src/inbox/classifier.py` testleri — varsayılan ayarla ("none") anahtar kelime sağlayıcısı
kullanılır, ağa çıkılmaz (bkz. `conftest.no_real_api_keys`)."""
from src.core.models import (
    CLASSIFIED_BY_KEYWORD,
    CLASSIFIED_BY_RULE,
    REPLY_AUTO,
    REPLY_BOUNCE,
    REPLY_INTERVIEW,
    REPLY_PENDING,
    REPLY_REJECTED,
)
from src.inbox.classifier import classify
from src.inbox.imap_reader import EmailBody, EmailHeader
from src.inbox.reply_matcher import MATCH_BOUNCE, MATCH_DOMAIN


def _header(
    *,
    from_addr="İK <ik@firma.com>",
    subject="Re: Başvurunuz",
    auto_submitted=None,
    precedence=None,
    x_autoreply=None,
    x_autorespond=None,
) -> EmailHeader:
    return EmailHeader(
        uid="1",
        message_id="<reply-1@firma.com>",
        from_addr=from_addr,
        subject=subject,
        date=None,
        in_reply_to=None,
        references=[],
        intended_to=None,
        auto_submitted=auto_submitted,
        precedence=precedence,
        x_autoreply=x_autoreply,
        x_autorespond=x_autorespond,
    )


def _body(text: str) -> EmailBody:
    return EmailBody(uid="1", text=text)


# ---- kural: bounce -----------------------------------------------------------------------


def test_classify_bounce_is_always_rule_based(session):
    header = _header(from_addr="mailer-daemon@googlemail.com", subject="Delivery Status Notification")
    result = classify(session, header, body=None, matched_by=MATCH_BOUNCE)

    assert result.category == REPLY_BOUNCE
    assert result.classified_by == CLASSIFIED_BY_RULE


def test_classify_bounce_ignores_body_content(session):
    # matched_by=bounce ise gövde ne derse desin (LLM/anahtar kelimeye hiç gidilmez).
    header = _header(subject="Görüşmeye davetlisiniz")
    body = _body("Mülakat için sizi bekliyoruz.")

    result = classify(session, header, body=body, matched_by=MATCH_BOUNCE)

    assert result.category == REPLY_BOUNCE
    assert result.classified_by == CLASSIFIED_BY_RULE


# ---- kural: otomatik cevap ----------------------------------------------------------------


def test_classify_detects_auto_reply_via_turkish_subject(session):
    header = _header(subject="Otomatik Yanıt: İzindeyim")
    result = classify(session, header, body=_body("Şu an ofis dışındayım."), matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_AUTO
    assert result.classified_by == CLASSIFIED_BY_RULE


def test_classify_detects_auto_reply_via_english_subject(session):
    header = _header(subject="Automatic reply: Out of Office")
    result = classify(session, header, body=None, matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_AUTO
    assert result.classified_by == CLASSIFIED_BY_RULE


def test_classify_does_not_treat_bare_noreply_sender_as_auto(session):
    """`noreply@...` TEK BAŞINA otomatik cevap sebebi değil — şirketler gerçek red/mülakat
    mailini de sık sık noreply adresinden gönderiyor; başlık/konu ipucu yoksa mail LLM/anahtar
    kelimeye düşmeli, kural aşamasında `REPLY_AUTO` sayılmamalı."""
    header = _header(from_addr="noreply@firma.com", subject="Başvurunuz Hakkında")
    result = classify(session, header, body=_body("Maalesef başvurunuz olumsuz sonuçlanmıştır."), matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_REJECTED
    assert result.classified_by == CLASSIFIED_BY_KEYWORD


def test_classify_detects_auto_reply_via_auto_submitted_header(session):
    header = _header(subject="Re: Başvurunuz", auto_submitted="auto-replied")
    result = classify(session, header, body=None, matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_AUTO
    assert result.classified_by == CLASSIFIED_BY_RULE


def test_classify_auto_submitted_no_is_not_treated_as_auto(session):
    """RFC 3834'e göre `Auto-Submitted: no` VARSAYILAN değerdir, otomatik cevap OLMADIĞINI
    gösterir."""
    header = _header(subject="Başvurunuz Hakkında", auto_submitted="no")
    result = classify(session, header, body=_body("Sizi mülakata davet ediyoruz."), matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_INTERVIEW
    assert result.classified_by == CLASSIFIED_BY_KEYWORD


def test_classify_detects_auto_reply_via_precedence_header(session):
    header = _header(subject="Re: Başvurunuz", precedence="auto_reply")
    result = classify(session, header, body=None, matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_AUTO
    assert result.classified_by == CLASSIFIED_BY_RULE


def test_classify_ignores_other_precedence_values(session):
    """`Precedence: bulk`/`list` toplu postadır, otomatik CEVAP değildir."""
    header = _header(subject="Başvurunuz Hakkında", precedence="bulk")
    result = classify(session, header, body=_body("Başvurunuzu aldık, değerlendiriyoruz."), matched_by=MATCH_DOMAIN)

    assert result.category != REPLY_AUTO


def test_classify_detects_auto_reply_via_x_autoreply_header(session):
    header = _header(subject="Re: Başvurunuz", x_autoreply="yes")
    result = classify(session, header, body=None, matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_AUTO
    assert result.classified_by == CLASSIFIED_BY_RULE


def test_classify_detects_auto_reply_via_x_autorespond_header(session):
    header = _header(subject="Re: Başvurunuz", x_autorespond="1")
    result = classify(session, header, body=None, matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_AUTO
    assert result.classified_by == CLASSIFIED_BY_RULE


def test_classify_x_autoreply_no_is_not_treated_as_auto(session):
    header = _header(subject="Başvurunuz Hakkında", x_autoreply="no")
    result = classify(session, header, body=_body("Sizi mülakata davet ediyoruz."), matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_INTERVIEW
    assert result.classified_by == CLASSIFIED_BY_KEYWORD


def test_classify_does_not_treat_normal_reply_as_auto(session):
    header = _header(subject="Başvurunuz Hakkında")
    result = classify(session, header, body=_body("Merhaba, başvurunuzu inceledik."), matched_by=MATCH_DOMAIN)

    assert result.category != REPLY_AUTO


# ---- LLM / anahtar kelimeye düşme -----------------------------------------------------------


def test_classify_falls_through_to_keyword_provider_for_interview(session):
    header = _header(subject="Görüşme Talebi")
    body = _body("Sizi mülakata davet etmek isteriz.")

    result = classify(session, header, body=body, matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_INTERVIEW
    assert result.classified_by == CLASSIFIED_BY_KEYWORD


def test_classify_falls_through_to_keyword_provider_for_rejection(session):
    header = _header(subject="Başvurunuz Hakkında")
    body = _body("Maalesef başvurunuz olumsuz sonuçlanmıştır.")

    result = classify(session, header, body=body, matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_REJECTED
    assert result.classified_by == CLASSIFIED_BY_KEYWORD


def test_classify_defaults_to_pending_when_no_keyword_matches(session):
    header = _header(subject="Merhaba")
    body = _body("Başvurunuzu aldık, değerlendirme sürecindeyiz.")

    result = classify(session, header, body=body, matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_PENDING
    assert result.classified_by == CLASSIFIED_BY_KEYWORD


def test_classify_handles_missing_body(session):
    header = _header(subject="Merhaba")
    result = classify(session, header, body=None, matched_by=MATCH_DOMAIN)

    assert result.category == REPLY_PENDING
    assert result.classified_by == CLASSIFIED_BY_KEYWORD
