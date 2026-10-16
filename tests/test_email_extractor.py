import secrets

import pytest

from src.company_finder.email_extractor import (
    PAGE_RANK_ABOUT,
    PAGE_RANK_HOME,
    PAGE_RANK_PRIORITY,
    FoundEmail,
    best_email,
    decode_cfemail,
    extract_emails,
    is_role_address,
    normalize_email,
    rank_emails,
)


def _cfemail_hex(email: str) -> str:
    key = secrets.randbits(8)
    encoded = bytes([key]) + bytes(b ^ key for b in email.encode("utf-8"))
    return encoded.hex()


def test_mailto_link_found_and_subject_stripped():
    html = '<a href="mailto:kariyer@acme.example.com?subject=Basvuru">Kariyer</a>'
    found = extract_emails(html, company_domain="acme.example.com", source_url="https://acme.example.com")
    assert [f.email for f in found] == ["kariyer@acme.example.com"]


def test_plain_text_email_found():
    html = "<p>Bize info@acme.example.com adresinden yazabilirsiniz.</p>"
    found = extract_emails(html, company_domain="acme.example.com", source_url="https://acme.example.com")
    assert [f.email for f in found] == ["info@acme.example.com"]


@pytest.mark.parametrize(
    "text",
    [
        "info [at] acme [dot] example [dot] com",
        "info (at) acme (dot) example (dot) com",
        "info {at} acme {dot} example {dot} com",
        "info [nokta] degil ama info at acme dot example dot com",
    ],
)
def test_obfuscated_email_decoded(text):
    html = f"<p>{text}</p>"
    found = extract_emails(html, company_domain="acme.example.com", source_url="https://acme.example.com")
    assert "info@acme.example.com" in [f.email for f in found]


def test_normal_sentence_with_at_does_not_produce_email():
    html = "<p>we met at noon yesterday and talked about the weather</p>"
    found = extract_emails(html, company_domain="acme.example.com", source_url="https://acme.example.com")
    assert found == []


def test_cloudflare_cfemail_decoded():
    encoded = _cfemail_hex("kariyer@acme.example.com")
    html = f'<span data-cfemail="{encoded}">deneme</span>'
    found = extract_emails(html, company_domain="acme.example.com", source_url="https://acme.example.com")
    assert [f.email for f in found] == ["kariyer@acme.example.com"]


@pytest.mark.parametrize("filename", ["logo@2x.png", "banner@3x.jpg", "sprite@1x.svg"])
def test_image_filenames_not_treated_as_email(filename):
    html = f"<p>Gorsel dosya: {filename} burada gecer.</p>"
    found = extract_emails(html, company_domain="acme.example.com", source_url="https://acme.example.com")
    assert found == []


@pytest.mark.parametrize(
    "address",
    [
        "noreply@acme.example.com", "info@sentry.io", "destek@sentry.wixpress.com",
        "info@example.com", "webmaster@acme.example.com", "hostmaster@acme.example.com",
        "root@acme.example.com", "wordpress@acme.example.com",
    ],
)
def test_junk_system_and_example_addresses_filtered(address):
    html = f"<p>{address}</p>"
    found = extract_emails(html, company_domain="acme.example.com", source_url="https://acme.example.com")
    assert found == []


@pytest.mark.parametrize(
    "address",
    ["bilgi@hs02.kep.tr", "muhasebe@hs01.kep.tr", "kariyer@kep.tr"],
)
def test_kep_addresses_filtered(address):
    """KEP'e normal e-postadan gönderim yapılamaz (geri döner), asla saklanmamalı."""
    html = f"<p>{address}</p>"
    found = extract_emails(html, company_domain="acme.example.com", source_url="https://acme.example.com")
    assert found == []


@pytest.mark.parametrize(
    "address, expected",
    [
        ("kariyer@acme.example.com", True),
        ("IK@acme.example.com", True),
        ("hr-turkey@acme.example.com", True),
        ("ahmet.yilmaz@acme.example.com", False),
        ("ikram@acme.example.com", False),
    ],
)
def test_is_role_address(address, expected):
    assert is_role_address(address) is expected


def test_company_domain_email_ranked_before_free_mail():
    found = [
        FoundEmail("kariyer@gmail.com", "https://acme.example.com", is_role=True, on_company_domain=False),
        FoundEmail("info@acme.example.com", "https://acme.example.com", is_role=True, on_company_domain=True),
    ]
    ranked = rank_emails(found)
    assert ranked[0].email == "info@acme.example.com"


def test_rank_order_by_role_tier():
    found = [
        FoundEmail("ahmet.yilmaz@acme.example.com", "u", is_role=False, on_company_domain=True),
        FoundEmail("destek@acme.example.com", "u", is_role=True, on_company_domain=True),
        FoundEmail("info@acme.example.com", "u", is_role=True, on_company_domain=True),
        FoundEmail("kariyer@acme.example.com", "u", is_role=True, on_company_domain=True),
    ]
    ranked = rank_emails(found)
    assert [f.email for f in ranked] == [
        "kariyer@acme.example.com",
        "info@acme.example.com",
        "destek@acme.example.com",
        "ahmet.yilmaz@acme.example.com",
    ]


def test_gmail_with_company_prefix_not_eliminated_but_not_on_company_domain():
    html = "<p>firma.kariyer@gmail.com</p>"
    found = extract_emails(html, company_domain="acme.example.com", source_url="https://acme.example.com")
    assert len(found) == 1
    assert found[0].email == "firma.kariyer@gmail.com"
    assert found[0].on_company_domain is False


def test_same_email_across_pages_deduplicated_keeping_best():
    other_page = FoundEmail(
        "info@acme.example.com", "https://acme.example.com/other", is_role=True,
        on_company_domain=True, page_rank=PAGE_RANK_HOME,
    )
    contact_page = FoundEmail(
        "info@acme.example.com", "https://acme.example.com/iletisim", is_role=True,
        on_company_domain=True, page_rank=PAGE_RANK_PRIORITY,
    )
    ranked = rank_emails([other_page, contact_page])
    assert len(ranked) == 1
    assert ranked[0].source_url == "https://acme.example.com/iletisim"


def test_page_rank_breaks_ties_when_role_tier_equal():
    # "destek" ve "satis" ikisi de aynı basamakta (2 - diğer rol); sadece sayfa türü ayırt eder.
    home = FoundEmail("destek@acme.example.com", "u1", is_role=True, on_company_domain=True, page_rank=PAGE_RANK_HOME)
    about = FoundEmail("satis@acme.example.com", "u2", is_role=True, on_company_domain=True, page_rank=PAGE_RANK_ABOUT)
    ranked = rank_emails([home, about])
    assert ranked[0].email == "satis@acme.example.com"


def test_best_email_returns_none_for_empty_list():
    assert best_email([]) is None


def test_normalize_email_matches_csv_import_behavior():
    """csv_import artık bu fonksiyona delege ediyor; davranış aynı kalmalı."""
    assert normalize_email("mailto:İK@Acme.example.COM") == "ik@acme.example.com"
    assert normalize_email("ali@") is None
    assert normalize_email("") is None
