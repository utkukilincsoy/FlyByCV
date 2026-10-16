"""LLM'siz sağlayıcı: anahtar kelime eşleştirmesiyle etiketleme ve cevap sınıflandırma.

API anahtarı istemez, ağa çıkmaz, her zaman aynı sonucu verir. Uygulamanın LLM olmadan
da çalışmasını sağlayan taban budur (CLAUDE.md kural 3) ve bir LLM sağlayıcısı hata
verdiğinde `llm/factory.SafeProvider` buraya düşer.

Sözlükler bilinçli olarak modül düzeyinde durur ki yeni kelime eklemek tek satırlık iş olsun.
Eşleşme kelime sınırına saygılıdır (`core/text.build_word_pattern`), bu yüzden Türkçe çekimli
biçimler açıkça yazılmalıdır: "test" deseni "testi" içinde EŞLEŞMEZ. Türkçe karakterler
eşleştirme sırasında sadeleştirildiği için ("ağ" = "ag") sözlüğe hem Türkçe karakterli hem
karaktersiz biçimi yazmak GEREKMEZ; sözlükte doğru yazım kullanılır.
"""
import logging
import re
from typing import Optional

from src.core.text import build_word_pattern, count_distinct_matches, matches_any
from src.llm.base import (
    REPLY_AUTO,
    REPLY_INTERVIEW,
    REPLY_PENDING,
    REPLY_REJECTED,
    LLMProvider,
    filter_tags,
)

logger = logging.getLogger(__name__)

# Sözlükteki her etiketin (src/core/tags.TAG_VOCABULARY) karşılığı burada olmak zorundadır;
# tests/test_keyword_provider.py bunu denetler. Sıra önemlidir: eşit puan alan iki etiketten
# burada önce yazılan kazanır, böylece sonuç her çalıştırmada aynı olur.
TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "yapay-zeka": (
        "yapay zeka", "yapay zekâ", "yapay zekası", "artificial intelligence", "ai",
        "llm", "büyük dil modeli", "dil modeli", "chatbot", "sohbet botu",
        "üretken yapay zeka", "generative ai", "doğal dil işleme", "nlp",
        "görüntü işleme", "computer vision", "yüz tanıma",
    ),
    "makine-ogrenmesi": (
        "makine öğrenmesi", "makine öğrenimi", "machine learning", "ml",
        "derin öğrenme", "deep learning", "sinir ağları", "neural network",
        "model eğitimi", "tensorflow", "pytorch", "scikit-learn", "tahmin modeli",
    ),
    "veri-bilimi": (
        "veri bilimi", "veri bilimci", "data science", "data scientist",
        "istatistik", "istatistiksel", "statistics", "büyük veri", "big data",
        "tahminleme", "predictive analytics", "veri modelleme",
    ),
    "veri-analizi": (
        "veri analizi", "veri analitiği", "data analytics", "data analysis",
        "iş zekası", "business intelligence", "power bi", "tableau",
        "raporlama", "reporting", "veri görselleştirme", "dashboard", "kpi",
    ),
    "siber-guvenlik": (
        "siber güvenlik", "siber saldırı", "cyber security", "cybersecurity",
        "bilgi güvenliği", "sızma testi", "pentest", "penetration testing",
        "firewall", "güvenlik duvarı", "zafiyet", "vulnerability", "soc",
        "kvkk", "iso 27001", "şifreleme", "log yönetimi",
    ),
    "ag-yonetimi": (
        "ağ", "ağ yönetimi", "ağ altyapısı", "ağ güvenliği", "bilgisayar ağı",
        "bilgisayar ağları", "network", "networking", "cisco", "router", "yönlendirici",
        "bgp", "ospf", "lan", "wan", "vlan", "mpls", "ccna", "ccnp",
        "switch", "kablosuz", "wifi", "fiber optik",
    ),
    "sistem-yonetimi": (
        "sistem yönetimi", "sistem yöneticisi", "system administrator", "sysadmin",
        "sunucu", "sunucular", "server", "windows server", "linux", "unix",
        "active directory", "vmware", "sanallaştırma", "virtualization",
        "yedekleme", "backup", "bakım anlaşması",
    ),
    "web-gelistirme": (
        "web sitesi", "web siteleri", "web geliştirme", "web development",
        "web tasarımı", "web tasarım", "kurumsal web", "e-ticaret", "e-commerce",
        "wordpress", "html", "css", "javascript", "web uygulaması", "web uygulamaları",
    ),
    "backend": (
        "backend", "back-end", "sunucu tarafı", "api", "rest api", "restful",
        "mikroservis", "mikroservisler", "microservice", "microservices",
        "java", "spring boot", "django", "node.js", "asp.net", ".net", "php",
        "laravel", "golang", "fastapi",
    ),
    "frontend": (
        "frontend", "front-end", "react", "reactjs", "angular", "vue", "vuejs",
        "next.js", "typescript", "arayüz geliştirme", "tek sayfa uygulaması",
        "tailwind", "bootstrap", "responsive",
    ),
    "mobil": (
        "mobil uygulama", "mobil uygulamalar", "mobil geliştirme", "mobile app",
        "android", "ios", "kotlin", "swift", "flutter", "react native",
        "play store", "app store", "cross-platform",
    ),
    "bulut-devops": (
        "bulut", "bulut bilişim", "cloud", "cloud computing", "devops",
        "aws", "azure", "google cloud", "gcp", "kubernetes", "docker",
        "konteyner", "container", "ci/cd", "terraform", "jenkins", "ansible",
    ),
    "gomulu-sistemler": (
        "gömülü", "gömülü sistemler", "gömülü yazılım", "embedded",
        "mikrodenetleyici", "mikrokontrolcü", "microcontroller", "firmware",
        "stm32", "arduino", "raspberry pi", "rtos", "fpga", "can bus",
        "iot", "nesnelerin interneti",
    ),
    "oyun-gelistirme": (
        "oyun", "oyunlar", "oyun geliştirme", "oyun stüdyosu", "oyun motoru",
        "game development", "game studio", "unity", "unreal engine", "unreal",
        "mobil oyun", "mobil oyunlar", "gameplay", "hiper kazüel",
    ),
    "veritabani": (
        "veritabanı", "veritabanları", "database", "sql", "mysql", "postgresql",
        "postgres", "oracle", "mssql", "sql server", "mongodb", "redis",
        "veri ambarı", "data warehouse", "etl", "sorgu optimizasyonu",
    ),
    "otomasyon": (
        "otomasyon", "endüstriyel otomasyon", "süreç otomasyonu", "automation",
        "plc", "scada", "hmi", "siemens", "robot", "robotlar", "robotik",
        "robotics", "rpa", "üretim hattı", "sensörlü sistem",
    ),
    "test-qa": (
        "test", "testi", "testler", "testleri", "yazılım testi", "test otomasyonu",
        "test mühendisi", "qa", "quality assurance", "kalite güvence",
        "selenium", "cypress", "birim test", "unit test", "regresyon testi",
        "hata takibi",
    ),
    "ui-ux": (
        "ui", "ux", "kullanıcı deneyimi", "user experience", "user interface",
        "kullanıcı arayüzü", "arayüz tasarımı", "figma", "adobe xd", "sketch",
        "wireframe", "prototip", "kullanılabilirlik", "grafik tasarım",
    ),
    "proje-yonetimi": (
        "proje yönetimi", "proje yöneticisi", "project management", "scrum",
        "scrum master", "agile", "çevik", "kanban", "jira", "pmp",
        "iş analizi", "business analyst", "süreç yönetimi",
    ),
    "elektronik": (
        "elektronik", "elektronik kart", "electronics", "pcb", "devre",
        "devre tasarımı", "circuit", "kart tasarımı", "altium", "analog",
        "sensör", "sensörler", "sensor", "güç elektroniği", "elektrik",
        "elektrik elektronik",
    ),
    "arge": (
        "ar-ge", "arge", "r&d", "araştırma geliştirme", "araştırma ve geliştirme",
        "research and development", "tübitak", "inovasyon", "innovation",
        "patent", "teknopark", "teknokent", "ürün geliştirme",
    ),
}

# Cevap sınıflandırma. Red kelimeleri mülakat kelimelerine göre önceliklidir: "görüşme
# talebiniz için teşekkürler, maalesef..." gibi cümleler red sayılmalı.
INTERVIEW_KEYWORDS: tuple[str, ...] = (
    "mülakat", "mülakata", "mülakat için", "görüşme", "görüşmek", "görüşmek isteriz",
    "tanışmak isteriz", "tanışma toplantısı", "randevu", "ön görüşme",
    "interview", "schedule a call", "meet you", "next steps",
)

REJECT_KEYWORDS: tuple[str, ...] = (
    "maalesef", "olumsuz", "uygun bulunmadı", "uygun bulunmamıştır",
    "başka adaylar", "başka bir adayla",
    # Soğuk başvuruya gelen tipik Türkçe kapanış cümleleri.
    "açık pozisyonumuz bulunmamaktadır", "pozisyon bulunmamaktadır",
    "değerlendiremeyeceğiz",
    "unfortunately", "we regret", "not moving forward", "no open positions",
    "not hiring",
)

# `src/inbox/classifier.py`nin başlık/konu tabanlı otomatik cevap kuralı bunların çoğunu zaten
# yakalar; bu liste bir GÜVENLİK AĞI — konu satırı ipucu vermeyen ama GÖVDESİ tipik bir tatil/
# izin yanıtı olan mailler için (ör. bir zincirin ortasındaki "Re: ..." konusu).
AUTO_REPLY_KEYWORDS: tuple[str, ...] = (
    "otomatik yanıt", "otomatik cevap", "otomatik bir yanıttır",
    "şu an ofis dışındayım", "ofis dışındayım", "izindeyim", "yıllık izindeyim",
    "tatildeyim", "döndüğümde",
    "out of office", "automatic reply", "auto-reply", "auto reply", "autoreply",
    "currently out of the office", "on leave", "on vacation", "i will be back",
)

_TAG_PATTERNS: dict[str, re.Pattern[str]] = {
    tag: build_word_pattern(keywords) for tag, keywords in TAG_KEYWORDS.items()
}
# Eşit puanda hangi etiketin önce geleceğini belirler (sözlükteki yazım sırası).
_TAG_ORDER: dict[str, int] = {tag: index for index, tag in enumerate(TAG_KEYWORDS)}
_INTERVIEW_PATTERN = build_word_pattern(INTERVIEW_KEYWORDS)
_REJECT_PATTERN = build_word_pattern(REJECT_KEYWORDS)
_AUTO_REPLY_PATTERN = build_word_pattern(AUTO_REPLY_KEYWORDS)


def _searchable(*parts: Optional[str]) -> str:
    """Boş olmayan parçaları tek metinde birleştirir (katlama arama yardımcılarında yapılır)."""
    return "\n".join(part for part in parts if part)


class KeywordProvider(LLMProvider):
    """Anahtar kelime sözlüğüyle çalışan sağlayıcı; hiçbir metin üretmez."""

    name = "keyword"

    def classify_company(self, name: str, about_text: str) -> list[str]:
        """Şirket adı ve tanıtım metnindeki anahtar kelimelere göre en fazla 3 etiket."""
        text = _searchable(name, about_text)
        scored = [
            (tag, count_distinct_matches(pattern, text))
            for tag, pattern in _TAG_PATTERNS.items()
        ]
        hits = [(tag, score) for tag, score in scored if score > 0]
        hits.sort(key=lambda item: (-item[1], _TAG_ORDER[item[0]]))

        tags = filter_tags([tag for tag, _ in hits])
        logger.debug(f"Anahtar kelime etiketleri ({name}): {tags or 'eşleşme yok'}")
        return tags

    def company_focus_phrase(self, name: str, about_text: str) -> Optional[str]:
        """LLM'siz ifade üretilmez; her zaman None (CLAUDE.md kural 1)."""
        logger.debug("Anahtar kelime sağlayıcısı şirket odağı ifadesi üretmez")
        return None

    def classify_reply(self, subject: str, body: str) -> str:
        """Cevabı sınıflandırır; red ifadeleri mülakat ifadelerine göre önceliklidir.

        Otomatik cevap (tatil/izin) buraya normalde hiç ULAŞMAZ — `src/inbox/classifier.py`
        başlık/konu kuralıyla önden yakalar; `AUTO_REPLY_KEYWORDS` yalnızca o kuralın kaçırdığı
        (konu satırı ipucu vermeyen) durumlar için bir güvenlik ağıdır, bu yüzden açık red/
        mülakat ifadelerinden SONRA kontrol edilir.
        """
        text = _searchable(subject, body)
        if matches_any(_REJECT_PATTERN, text):
            return REPLY_REJECTED
        if matches_any(_INTERVIEW_PATTERN, text):
            return REPLY_INTERVIEW
        if matches_any(_AUTO_REPLY_PATTERN, text):
            return REPLY_AUTO
        return REPLY_PENDING
