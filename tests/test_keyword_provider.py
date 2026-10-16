import pytest

from src.core.tags import TAG_VOCABULARY
from src.llm.base import MAX_TAGS, REPLY_AUTO, REPLY_INTERVIEW, REPLY_PENDING, REPLY_REJECTED
from src.llm.keyword_provider import TAG_KEYWORDS, KeywordProvider

# Her etiket için gerçekçi bir "Hakkımızda" metni. Beklenti: o etiket sonuçta olmalı
# (tek etiket olmak zorunda değil, metinler birbirine komşu alanlara değebilir).
ABOUT_SAMPLES: list[tuple[str, str]] = [
    ("yapay-zeka", "Yapay zeka tabanlı sohbet botu ve doğal dil işleme çözümleri geliştiriyoruz."),
    ("makine-ogrenmesi", "Makine öğrenmesi ve derin öğrenme modelleri için PyTorch kullanıyoruz."),
    ("veri-bilimi", "Veri bilimi ekibimiz büyük veri üzerinde istatistiksel tahminleme yapar."),
    ("veri-analizi", "Veri analizi ve iş zekası raporlaması için Power BI panoları kuruyoruz."),
    (
        "siber-guvenlik",
        "Siber güvenlik hizmetleri: sızma testi, güvenlik duvarı kurulumu ve KVKK uyumu.",
    ),
    ("ag-yonetimi", "Cisco router ve switch kurulumu, LAN/WAN ağ altyapısı projeleri yapıyoruz."),
    (
        "sistem-yonetimi",
        "Windows Server ve Linux sunucu bakımı, Active Directory ve yedekleme hizmetleri.",
    ),
    (
        "web-gelistirme",
        "Kurumsal web sitesi ve e-ticaret projeleri: WordPress, HTML ve CSS ile geliştirme.",
    ),
    ("backend", "REST API ve mikroservis mimarisi; Java Spring Boot ile backend geliştiriyoruz."),
    ("frontend", "React ve TypeScript ile modern frontend arayüz geliştirme yapıyoruz."),
    ("mobil", "Android ve iOS için Flutter ile mobil uygulama geliştiriyoruz."),
    ("bulut-devops", "AWS ve Azure üzerinde Kubernetes, Docker ve ci/cd süreçleri kuruyoruz."),
    (
        "gomulu-sistemler",
        "Gömülü yazılım ve firmware geliştirme; STM32 mikrodenetleyici tabanlı iot cihazları.",
    ),
    ("oyun-gelistirme", "Unity ve Unreal Engine ile mobil oyun geliştiren bir stüdyoyuz."),
    (
        "veritabani",
        "PostgreSQL ve MongoDB veritabanı danışmanlığı, veri ambarı ve etl süreçleri.",
    ),
    (
        "otomasyon",
        "Endüstriyel otomasyon: PLC ve SCADA programlama, robotik üretim hattı çözümleri.",
    ),
    ("test-qa", "Yazılım testi ve test otomasyonu hizmeti; Selenium ile qa süreçleri kuruyoruz."),
    (
        "ui-ux",
        "Figma ile kullanıcı arayüzü ve kullanıcı deneyimi tasarımı, ui/ux danışmanlığı.",
    ),
    ("proje-yonetimi", "Scrum ve Kanban ile çevik proje yönetimi danışmanlığı, Jira kurulumu."),
    ("elektronik", "Elektronik kart ve pcb devre tasarımı, sensör ve güç elektroniği çözümleri."),
    ("arge", "Ar-Ge merkezimizde TÜBİTAK destekli inovasyon ve patent çalışmaları yürütüyoruz."),
]


@pytest.fixture
def provider():
    return KeywordProvider()


def test_every_vocabulary_tag_has_keywords():
    """TAG_VOCABULARY'deki her etiketin sözlükte karşılığı olmalı, fazlası olmamalı."""
    assert set(TAG_KEYWORDS) == TAG_VOCABULARY


@pytest.mark.parametrize("tag", sorted(TAG_KEYWORDS))
def test_keyword_lists_are_not_empty(tag):
    """Hiçbir etiket boş kelime listesiyle kalmamalı."""
    assert TAG_KEYWORDS[tag]


def test_about_samples_cover_every_tag():
    """Örnek metin tablosu her etiketi denemeli (yeni etiket eklenince test de büyüsün)."""
    assert {tag for tag, _ in ABOUT_SAMPLES} == TAG_VOCABULARY


@pytest.mark.parametrize("tag,about", ABOUT_SAMPLES, ids=[tag for tag, _ in ABOUT_SAMPLES])
def test_classify_company_finds_expected_tag(provider, tag, about):
    """Her etiket, kendi örnek Hakkımızda metninden çıkarılabilmeli."""
    assert tag in provider.classify_company("Örnek A.Ş.", about)


@pytest.mark.parametrize("tag,about", ABOUT_SAMPLES, ids=[tag for tag, _ in ABOUT_SAMPLES])
def test_classify_company_stays_in_vocabulary(provider, tag, about):
    """Sonuç her zaman sözlüğün alt kümesi ve en fazla 3 etiket olmalı."""
    tags = provider.classify_company("Örnek A.Ş.", about)
    assert set(tags) <= TAG_VOCABULARY
    assert len(tags) <= MAX_TAGS


@pytest.mark.parametrize(
    "name,about",
    [
        ("Bağlantı Sanayi", "Bağlantı elemanları üretiyoruz; sağlam ve ağır yükler taşır."),
        # Türkçe karakter kullanılmadan yazılmış hali de eşleşmemeli.
        ("Baglanti Sanayi", "Baglanti elemanlari uretiyoruz; saglam ve agir yukler tasir."),
    ],
)
def test_classify_company_respects_word_boundaries(provider, name, about):
    """'ağ' kelimesi 'bağlantı' / 'sağlam' / 'ağır' içinde eşleşmemeli."""
    assert "ag-yonetimi" not in provider.classify_company(name, about)


def test_classify_company_matches_text_without_turkish_characters(provider):
    """Türkçe karakter kullanmadan yazılmış tanıtım metni de etiketlenebilmeli."""
    tags = provider.classify_company(
        "Ornek Bilisim", "siber guvenlik ve ag yonetimi hizmetleri sunuyoruz"
    )
    assert sorted(tags) == ["ag-yonetimi", "siber-guvenlik"]


def test_classify_company_matches_standalone_word(provider):
    """Aynı kelime tek başına geçtiğinde eşleşmeli (sınır kuralı fazla katı olmasın)."""
    assert "ag-yonetimi" in provider.classify_company("Örnek", "Ağ altyapısı kurulumu yapıyoruz.")


def test_classify_company_limits_to_three_tags(provider):
    """Beş alandan bahseden metin bile en fazla 3 etiket vermeli."""
    about = (
        "Web sitesi, mobil uygulama, siber güvenlik, veritabanı ve oyun geliştirme "
        "hizmetleri sunuyoruz."
    )
    assert len(provider.classify_company("Her İş A.Ş.", about)) == MAX_TAGS


def test_classify_company_is_deterministic(provider):
    """Aynı metin her çağrıda aynı sırayla aynı etiketleri vermeli."""
    about = "Android ve iOS mobil uygulama, ayrıca React ile frontend geliştirme."
    assert provider.classify_company("Örnek", about) == provider.classify_company("Örnek", about)


def test_classify_company_uses_company_name_too(provider):
    """Tanıtım metni boş olsa bile şirket adı değerlendirilmeli."""
    assert "siber-guvenlik" in provider.classify_company("Siber Güvenlik Teknolojileri", "")


def test_classify_company_returns_empty_when_nothing_matches(provider):
    """Alakasız metin boş liste vermeli; uydurma etiket üretilmemeli."""
    assert provider.classify_company("Zeytinyağı Fabrikası", "Soğuk sıkım zeytinyağı üretiyoruz.") == []


def test_company_focus_phrase_is_always_none(provider):
    """LLM'siz sağlayıcı metin üretmez."""
    assert provider.company_focus_phrase("Örnek A.Ş.", "Otomasyon çözümleri sunuyoruz.") is None


def test_provider_name_matches_classified_by_value(provider):
    """Ad, EmailReply.classified_by alanına birebir yazılabilecek değerde olmalı."""
    assert provider.name == "keyword"


@pytest.mark.parametrize(
    "subject,body,expected",
    [
        ("Mülakat daveti", "Sizinle mülakat yapmak istiyoruz.", REPLY_INTERVIEW),
        ("Başvurunuz", "Tanışmak isteriz, uygun bir zaman paylaşır mısınız?", REPLY_INTERVIEW),
        ("Interview invitation", "We would like to schedule a call this week.", REPLY_INTERVIEW),
        ("Başvurunuz hakkında", "Maalesef başvurunuz olumsuz sonuçlanmıştır.", REPLY_REJECTED),
        ("Your application", "Unfortunately we are not moving forward.", REPLY_REJECTED),
        ("Başvuru", "BAŞVURUNUZ MAALESEF UYGUN BULUNMADI.", REPLY_REJECTED),
        # İkisi birden geçiyorsa red önceliklidir.
        (
            "Görüşme talebiniz",
            "Görüşme talebiniz için teşekkürler, maalesef şu an uygun bulunmadı.",
            REPLY_REJECTED,
        ),
        # Soğuk başvuruya gelen tipik iki cevap: biri red, biri gerçekten bekleyen.
        (
            "Başvurunuz",
            "Şu an için açık pozisyonumuz bulunmamaktadır, CV'niz havuzumuza eklenmiştir.",
            REPLY_REJECTED,
        ),
        (
            "Başvurunuz alındı",
            "Özgeçmişiniz değerlendirmeye alınmıştır, size dönüş yapacağız.",
            REPLY_PENDING,
        ),
        # Türkçe karakter kullanmadan yazılmış cevaplar da doğru sınıflanmalı.
        ("Basvurunuz", "Su an icin acik pozisyonumuz bulunmamaktadir.", REPLY_REJECTED),
        ("Basvurunuz", "Maalesef uygun bulunmadi.", REPLY_REJECTED),
        ("Gorusme", "Sizinle mulakat yapmak isteriz.", REPLY_INTERVIEW),
        ("Basvurunuz alindi", "Ozgecmisiniz degerlendirmeye alinmistir.", REPLY_PENDING),
        ("", "", REPLY_PENDING),
        # Konu satırı otomatik cevaba benziyor (gerçek akışta bu `classifier.py`nin başlık/konu
        # kuralında zaten yakalanır; burada doğrudan sağlayıcı çağrılıyor).
        ("Otomatik yanıt", "Mailiniz ulaştı, en kısa sürede ilgileneceğiz.", REPLY_AUTO),
        # Konu ipucu vermiyor ama gövde tipik bir tatil/izin yanıtı — güvenlik ağı bunu yakalamalı.
        ("Re: Başvurunuz", "Şu an ofis dışındayım, döndüğümde size dönüş yapacağım.", REPLY_AUTO),
        ("Re: Your application", "I am currently out of the office until Monday.", REPLY_AUTO),
    ],
)
def test_classify_reply(provider, subject, body, expected):
    """Cevap sınıflandırma: TR + EN, ikisi birden geçerse red kazanır."""
    assert provider.classify_reply(subject, body) == expected
