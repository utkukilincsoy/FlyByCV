"""Şirket sektör etiketleri ve bunların Türkçe okunuşları."""

TAG_VOCABULARY: set[str] = {
    "yapay-zeka",
    "makine-ogrenmesi",
    "veri-bilimi",
    "veri-analizi",
    "siber-guvenlik",
    "ag-yonetimi",
    "sistem-yonetimi",
    "web-gelistirme",
    "backend",
    "frontend",
    "mobil",
    "bulut-devops",
    "gomulu-sistemler",
    "oyun-gelistirme",
    "veritabani",
    "otomasyon",
    "test-qa",
    "ui-ux",
    "proje-yonetimi",
    "elektronik",
    "arge",
}

# Mail gövdesinde "{etiket} alanındaki faaliyetleri" biçiminde cümle içinde kullanılır:
# bu yüzden küçük harfle yazılır (kısaltmalar hariç) ve içinde " ve " geçmez — iki etiket
# " ve " ile birleştirildiğinde "X ve Y ve Z alanlarındaki" gibi bozuk cümle çıkmasın.
TAG_LABELS: dict[str, str] = {
    "yapay-zeka": "yapay zeka",
    "makine-ogrenmesi": "makine öğrenmesi",
    "veri-bilimi": "veri bilimi",
    "veri-analizi": "veri analizi",
    "siber-guvenlik": "siber güvenlik",
    "ag-yonetimi": "ağ yönetimi",
    "sistem-yonetimi": "sistem yönetimi",
    "web-gelistirme": "web geliştirme",
    "backend": "backend geliştirme",
    "frontend": "frontend geliştirme",
    "mobil": "mobil uygulama geliştirme",
    "bulut-devops": "bulut bilişim",
    "gomulu-sistemler": "gömülü sistemler",
    "oyun-gelistirme": "oyun geliştirme",
    "veritabani": "veritabanı sistemleri",
    "otomasyon": "otomasyon",
    "test-qa": "yazılım testi",
    "ui-ux": "arayüz tasarımı",
    "proje-yonetimi": "proje yönetimi",
    "elektronik": "elektronik",
    "arge": "Ar-Ge",
}
