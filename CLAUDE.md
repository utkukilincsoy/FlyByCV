# FlybyCV – Claude Code Proje Rehberi

Masaüstü uygulaması: Kullanıcının mesleğine göre şirketleri bulur, e-posta adreslerini çıkarır,
(isteğe bağlı) şirkete göre vurgulanmış CV'yi PDF olarak hazırlar, e-postayla gönderir ve gelen
cevapları sınıflandırır. İlan sitesi (LinkedIn, Kariyer.net vb.) TARANMAZ. Web formu DOLDURULMAZ.

İlerleme durumu ve görev listesi: `PLAN.md` (yalnızca yerelde durur, GitHub'a yüklenmez). Terimler: `SOZLUK.md`.

## Çalışma dili ve üslup
- Türkçe konuş.
- Her adımı kısa açıkla, yeni bir terim geçerse bir cümleyle ne olduğunu söyle.
- Kullanıcının kendisinin yapması gereken işleri (kurulum, API anahtarı alma, terminal komutu)
  açıkça "🟡 Senin yapman gereken:" başlığıyla belirt.
- Tek seferde tek, küçük bir görev yap. Bitince çalıştırıp test etmesi için komutu ver ve dur.

## Teknoloji
- Python 3.12, PySide6 (arayüz), SQLite + SQLModel (veritabanı)
- Playwright (şirket sitesi tarama + PDF üretimi), Jinja2 (HTML şablonları)
- APScheduler (zamanlayıcı), keyring (şifre/API anahtarı saklama)
- smtplib / imaplib (mail), dnspython (MX kontrolü)
- LLM: `anthropic` (Claude) ve `openai` paketi (Gemini + Groq, OpenAI-uyumlu uç nokta)
- Test: pytest. Paketleme: PyInstaller.

## Klasör yapısı
```
src/core/            config, veritabanı modelleri, loglama, ortak yardımcılar; blocklist (engelli alan
                     adları), legal (KVKK/6563 bilgilendirme onayı), update_check (günde bir "yeni
                     sürüm var mı?" — yalnızca bilgi, otomatik indirme/kurma yok), purge_secrets
                     (`main.py --purge-secrets`: kaldırıcı için kasadaki şifre/anahtarları siler),
                     backup (app.db + master_profile.json zip yedeği: günde bir açılış yedeği, en
                     yeni 7 tutulur; Ayarlar'da "Şimdi yedekle"/"Yedekten geri yükle"; şifreler girmez),
                     rename_migration (eski ad "CV Autopilot": ilk açılışta %APPDATA%\CV Autopilot →
                     %APPDATA%\FlybyCV KOPYASI; eski klasör silinmez)
src/llm/             LLMProvider arayüzü; claude, openai_compat (Gemini/Groq), none (anahtar kelime)
src/company_finder/  csv_import, google_places (isteğe bağlı, ücretli olabilir), osm_source (anahtarsız
                     OpenStreetMap), teknokent_source (anahtarsız firma listeleri), naming (alan
                     adından/başlıktan şirket adı), site_crawler, url_safety (taramada yalnızca genel
                     IP'ye istek: yerel/özel ağ adresleri engellenir, SSRF), email_extractor, mx_check
src/cv_builder/      profile_loader, tag_matcher, validator, renderer (HTML→PDF), mail_template
src/sender/          smtp_sender, rate_limiter, queue (manuel/otomatik mod), worker (kuyruk döngüsü),
                     safety_brake (bounce oranı / art arda hata → acil durdur), self_test (sihirbazın
                     kendine test maili: DB'ye yazmaz, alıcı yalnızca göndericinin kendi adresi)
src/inbox/           imap_reader, reply_matcher, classifier
src/autopilot/       runner (tek "Başlat" ile keşif → tara → hazırla → gönder döngüsü)
src/ui/              PySide6 pencereleri (legal_dialog: ilk açılış KVKK onayı; onboarding_wizard +
                     onboarding_pages/: taze kurulumda 9 sayfalı kurulum sihirbazı, veri katmanı
                     onboarding_data/onboarding_summary Qt'siz; startup: açılış akışı)
templates/           cv.html, cv.css, mail şablonları
presets/             meslek paketleri, şehirler, mail sağlayıcıları, legal/kvkk_tr.md (bilgilendirme metni)
data/                master_profile.json, app.db  (git'e GİRMEZ – kişisel veri)
output/              üretilen PDF'ler (git'e GİRMEZ)
tests/               pytest testleri
main.py              uygulamanın giriş noktası
```

## DEĞİŞMEZ KURALLAR
1. **Sıfır halüsinasyon.** LLM asla CV metni, tarih, unvan, yetenek veya başarı YAZMAZ.
   LLM yalnızca (a) şirket sektör etiketlerini, (b) profil maddelerinin ID sıralamasını,
   (c) mail için tek cümlelik şirkete özel bir giriş cümlesini döndürür.
   Her LLM çıktısı `cv_builder/validator.py` ile `master_profile.json`'a karşı doğrulanır;
   geçmezse çıktı atılır ve ana CV kullanılır.
2. **CV özelleştirme kapatılabilir.** Ayarlarda kapalıysa LLM hiç çağrılmaz, herkese ana CV gider.
3. **Uygulama LLM olmadan da çalışmalı.** "none" sağlayıcısı anahtar kelime eşleştirmesiyle çalışır.
4. **Aynı şirkete asla iki kez gönderim yapılmaz** (veritabanında e-posta alan adına göre kontrol).
   `Company` oluşturan her kod, `domain`'i kaydetmeden önce `src/core/models.normalize_domain()`
   ile normalize etmelidir (`Company.domain` ve `Application.company_id` veritabanında benzersizdir).
5. **Gönderim limitleri her iki modda da zorunlu:** günlük/saatlik limit, rastgele bekleme,
   mesai saati kısıtı, acil durdur. Test modunda tüm mailler kullanıcının kendi adresine gider.
   Ayrıca kodda DEĞİŞTİRİLEMEZ sabit üst sınırlar vardır (`src/sender/rate_limiter.py`:
   `HARD_MAX_DAILY = 50`, `HARD_MAX_HOURLY = 20`, `HARD_MIN_DELAY_SECONDS = 30`); Ayarlar
   sekmesi bunları aşan bir değeri kaydetmeyi reddeder, `load_limits()` ise (Otopilot, kuyruk
   işçisi, `scripts/send_one.py` dahil HER gönderim yolu) ayarlardaki değeri sessizce bu
   sınırların içine kırpar ve loglar. Tek seferlik manuel gönderimde (`scripts/send_one.py`)
   limit/mesai kısıtı, kullanıcının KENDİ (sabit sınırın altındaki) ayarından geliyorsa açık
   onayla aşılabilir; sabit üst sınırların kendisi ve `sending_paused` (acil durdur) hiçbir
   şekilde aşılamaz.
6. **Sırlar koda veya dosyaya yazılmaz.** API anahtarları ve mail şifreleri yalnızca `keyring`'de.
7. Kişisel veri (`data/`, `output/`) asla git'e eklenmez.
8. Uzun işler (tarama, gönderim, IMAP) arayüzü dondurmamalı: QThread / arka plan işçisi kullan.
9. Profil metinleri kullanıcının CV'sinden birebir alınır, yeniden yazılmaz. Profildeki her
   değişiklik önce liste olarak gösterilir ve onay alınır.
10. **Rol adresi olmayan e-postaya otomatik gönderim YAPILMAZ.** `Company.email_is_role`
    False ise (adres kişi adı gibi görünüyor, örn. `ahmet.yilmaz@firma.com`) otomatik modda o
    şirkete gönderim yapılmaz; yalnızca manuel modda, kullanıcı onay kuyruğunda görüp
    onayladıysa gönderilir. Tespit: `company_finder/email_extractor.is_role_address()`.
11. **Engelli alan adına HİÇBİR yoldan işlem yapılmaz.** "Bir daha yazmayın / listeden çıkarın /
    ilgilenmiyoruz" diyen şirketin alan adı (`BlockedDomain`, `src/core/blocklist.py`) kalıcı
    engellenir; keşif/CSV (`csv_import.add_company` — tüm kaynakların ortak geçidi), kuyruk seçimi,
    Otopilot tarama/hazırlama ve gönderim (`smtp_sender.send_application`, test modunda da) onu
    atlar. Tespit YALNIZCA anahtar kelime kuralıdır (`inbox/classifier.py`, LLM'e bırakılmaz).
    Engeli yalnızca kullanıcı Cevaplar sekmesinden kaldırır.
12. **Onaysız gerçek gönderim yok.** KVKK / 6563 bilgilendirmesi (`presets/legal/kvkk_tr.md`,
    "hukuki tavsiye değildir") ilk açılışta gösterilir, kullanıcı onaylayana kadar
    `send_application` GERÇEK gönderimi reddeder (test modu çalışır). Onay sürümlüdür
    (`core/legal.LEGAL_TEXT_VERSION`); metin değişirse yeniden sorulur. Metin "uyumludur" diye
    yazılmaz, bir avukat görmeden böyle bir iddia eklenmez.

## CV şablonu – ATS kuralları
CV PDF'i üretilirken (`cv_builder/renderer.py`, `templates/cv.html`/`cv.css`) ATS (İşe Alım Takip
Sistemi) tarayıcılarının doğru okuyabilmesi için:
- Tek sütun düzen. Tablo, çok sütunlu yerleşim, metin kutusu yok.
- İkon, grafik, yetenek çubuğu, fotoğraf yok. Metin asla görsel olarak basılmaz.
- Standart bölüm başlıkları: Özet, Deneyim, Eğitim, Projeler, Yetenekler, Sertifikalar.
- İletişim bilgileri sayfa üst/alt bilgisinde (header/footer) değil, gövdede.
- Standart fontlar (Arial, Calibri veya benzeri), 10–12 punto.
- Tarih formatı her yerde tutarlı (örn. "03/2023 – 06/2024").
- PDF'teki metin seçilebilir ve kopyalanabilir olmalı. Test: PDF'ten metin çıkarılıp profildeki
  metinle karşılaştırılacak.
- Tasarım, kullanıcının orijinal CV'sinin (`data/cv.pdf`) düzenine ve sırasına mümkün olduğunca
  yakın olacak. Sadece ATS'e aykırı kısımlar değiştirilecek.

## Kod stili
- Tip ipuçları (type hints) kullan, fonksiyonlar kısa ve tek iş yapan olsun.
- Kod ve değişken adları İngilizce; arayüz metinleri ve kullanıcıya dönük mesajlar Türkçe.
- `print` yerine `logging` kullan.
- Her yeni modül için en az bir pytest testi yaz. Ağ/LLM çağrılarını testte mock'la.

## Komutlar (Windows)
```
.venv\Scripts\activate          # sanal ortamı aç
pip install -r requirements.txt
playwright install chromium
pytest                          # testleri çalıştır
python main.py                  # uygulamayı başlat
```

## Çalışma şekli
- Büyük bir işe başlamadan önce planı anlat, onay al.
- Her tamamlanan görevden sonra (yerelde varsa) `PLAN.md`'de ilgili kutuyu işaretle ve
  kullanıcıya git commit komutunu öner.
