import pytest

from src.llm.redact import (
    EMAIL_PLACEHOLDER,
    PHONE_PLACEHOLDER,
    URL_PLACEHOLDER,
    redact,
)

# Planda zorunlu tutulan örnek: "iyi çalışmalar" cümle içinde geçtiği için imza SAYILMAZ,
# kendi başına duran "Saygılarımla," ise imza sayılır.
TRICKY_REPLY = (
    "Merhaba Ali Bey, iyi çalışmalar. Maalesef başvurunuzu olumlu değerlendiremedik.\n"
    "\n"
    "Saygılarımla,\n"
    "Ayşe Yılmaz"
)


def test_inline_courtesy_phrase_is_not_a_signature():
    """Cümle içindeki "iyi çalışmalar" mailin asıl sinyalini kesmemeli."""
    result = redact(TRICKY_REPLY)
    assert "Maalesef" in result
    assert "Ayşe Yılmaz" not in result


def test_last_signature_marker_wins():
    """Birden fazla imza işareti varsa kesme noktası sonuncusu olmalı (alıntılı mail zincirleri)."""
    text = (
        "Maalesef başvurunuzu bu dönem olumlu değerlendiremedik, ilginize teşekkür ederiz.\n"
        "Saygılarımla,\n"
        "Eski Gönderen\n"
        "\n"
        "Ek olarak: pozisyon kapandı, özgeçmişinizi kayıtlarımızda tutuyoruz.\n"
        "\n"
        "İyi çalışmalar\n"
        "Ayşe Yılmaz"
    )
    result = redact(text)
    assert "Ek olarak" in result
    assert "Ayşe Yılmaz" not in result


def test_dash_line_cuts_signature():
    """Yalnızca kısa çizgilerden oluşan satır klasik imza ayıracıdır."""
    text = (
        "Görüşmeye davet etmek isteriz, uygun olduğunuz günleri yazabilir misiniz?\n"
        "--\n"
        "Ayşe Yılmaz, İnsan Kaynakları"
    )
    result = redact(text)
    assert "davet" in result
    assert "İnsan Kaynakları" not in result


def test_short_message_keeps_signature():
    """Kesme sonucu çok kısa kalacaksa imza kesilmez: kısa mailde tek sinyal o olabilir."""
    result = redact("Saygılarımla,\nAyşe Yılmaz")
    assert "Ayşe Yılmaz" in result


def test_mid_line_marker_is_ignored():
    """İmza işareti satırın başında ve kendi başına olmalı; cümle içindeyse sayılmaz."""
    text = "Yazımı Saygılarımla bitiriyorum ama devamı var: Maalesef olumsuz sonuçlandı."
    assert redact(text) == text


def test_email_is_replaced():
    """E-posta adresi yer tutucuya çevrilmeli, alan adı da geride kalmamalı."""
    result = redact("Sorularınız için ik@ornek-firma.com adresine yazabilirsiniz.")
    assert EMAIL_PLACEHOLDER in result
    assert "ornek-firma.com" not in result


@pytest.mark.parametrize(
    "url",
    ["https://ornek-firma.com/kariyer", "http://ornek-firma.com", "www.ornek-firma.com"],
)
def test_url_is_replaced(url):
    """Bağlantılar yer tutucuya çevrilmeli."""
    result = redact(f"Açık pozisyonlarımız {url} adresinde listelenmektedir.")
    assert URL_PLACEHOLDER in result
    assert "ornek-firma.com" not in result


@pytest.mark.parametrize(
    "phone",
    ["+90 532 111 22 33", "0(532) 111-22-33", "05321112233", "0532.111.22.33"],
)
def test_phone_is_replaced(phone):
    """Türkçe telefon biçimleri yer tutucuya çevrilmeli."""
    result = redact(f"Detaylar için {phone} numarasından bize ulaşabilirsiniz.")
    assert PHONE_PLACEHOLDER in result
    assert "111" not in result


@pytest.mark.parametrize(
    "text",
    [
        "Pozisyon 03/2023 – 06/2024 arası açıktı.",
        "Ücret aralığı 45.000 - 60.000 TL olarak belirlendi.",
        "2023 yılında 15 kişilik bir ekiple çalıştık.",
    ],
)
def test_dates_and_amounts_are_not_phones(text):
    """Tarih ve tutarlar telefon sanılmamalı."""
    assert redact(text) == text


def test_redact_is_idempotent():
    """İkinci kez uygulanması bir şeyi değiştirmemeli."""
    text = (
        "Başvurunuz için ik@ornek-firma.com veya 0532 111 22 33 üzerinden ulaşın, "
        "detaylar www.ornek-firma.com adresinde.\n"
        "\n"
        "Saygılarımla,\n"
        "Ayşe Yılmaz"
    )
    once = redact(text)
    assert redact(once) == once


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_empty_input_does_not_raise(text):
    """Boş girdi hata fırlatmamalı."""
    assert redact(text) == text or redact(text) == ""


def test_signature_removal_takes_pii_with_it():
    """İmza bloğundaki telefon/e-posta hem kesilerek hem maskelenerek iki kez korunmalı."""
    text = (
        "Maalesef başvurunuzu bu dönem için olumlu değerlendiremedik, ilginize teşekkürler.\n"
        "\n"
        "Saygılarımla,\n"
        "Ayşe Yılmaz | ayse@ornek-firma.com | 0532 111 22 33"
    )
    result = redact(text)
    assert "ayse@ornek-firma.com" not in result
    assert "0532" not in result
    assert "Maalesef" in result
