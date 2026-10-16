# FlybyCV

Mesleğine göre şirketleri bulan, sitelerinden herkese açık e-posta adreslerini çıkaran, CV'ni ekleyip
**senin e-posta hesabından** başvuru gönderen ve gelen cevapları sınıflandıran Windows masaüstü uygulaması.

- İlan sitelerini (LinkedIn, Kariyer.net vb.) **taramaz**, web formu **doldurmaz**.
- Tüm verin (profil, CV, veritabanı) yalnızca **senin bilgisayarında** durur; uygulamanın kendi sunucusu yoktur.
- Ücretsizdir ve açık kaynaktır (MIT lisansı).

> **Önemli:** Bu uygulama çok sayıda şirkete başvuru gönderebilir. İçeriğinden ve gönderimlerden sen sorumlusun.
> İlk açılışta çıkan KVKK / 6563 bilgilendirmesi genel bilgidir, **hukuki tavsiye değildir** ve bir avukat
> tarafından incelenmemiştir.

## Sorumlu kullanım ve feragat

**Bu uygulamayı kendi riskinizle kullanın.** Yazılım MIT lisansıyla "olduğu gibi" (as is), hiçbir garanti
olmadan sunulur; yanlış gönderim, mail hesabının kısıtlanması, veri kaybı ya da hukuki sonuçlar dahil hiçbir
zarardan geliştirici sorumlu tutulamaz (bkz. [LICENSE](LICENSE)).

Kullanırken:

- **Toplu / istenmeyen mail (spam) aracı olarak kullanma.** Uygulama tek tek, kişiselleştirilmiş iş başvurusu
  içindir. [Gönderim limitleri](#otopilot-nasıl-kullanılır) bu yüzden kodda sabittir ve aşılamaz.
- **Önce test modunda dene.** Test modu (varsayılan: açık) tüm mailleri kendi adresine gönderir;
  gerçek gönderime ancak örnekleri kontrol ettikten sonra geç.
- **"Bir daha yazmayın" diyen şirkete yazma.** Uygulama bu cevapları yakalayıp alan adını kalıcı engeller;
  yine de cevaplarını kendin de takip et.
- **Mail sağlayıcının kurallarına uy.** Gmail / Yandex günlük gönderim sınırları ve kullanım koşulları senin
  hesabın için geçerlidir; ihlal hesabının kısıtlanmasına yol açabilir.
- **KVKK / 6563 ve ilgili mevzuata uyum senin sorumluluğundadır.** İlk açılıştaki bilgilendirme genel
  bilgidir, hukuki tavsiye değildir.

## İndirme ve kurulum

1. Bu projenin [**Releases** (Sürümler)](https://github.com/utkukilincsoy/flybycv/releases) sayfasından
   en son sürümün `FlybyCV-Setup-<sürüm>.exe` dosyasını indir.
   Dosya Chromium'u da içerdiği için büyüktür (yaklaşık 200 MB).
2. (İsteğe bağlı ama önerilir) Yanındaki `.sha256` dosyasındaki özeti, indirdiğin dosyanınkiyle karşılaştır.
   PowerShell'de:
   ```
   Get-FileHash .\FlybyCV-Setup-<sürüm>.exe -Algorithm SHA256
   ```
   Çıkan değer `.sha256` dosyasındakiyle aynı olmalı.
3. İndirdiğin dosyaya çift tıkla. **Yönetici izni gerekmez**; uygulama kendi kullanıcı klasörüne kurulur.
4. Kurulum bitince Başlat menüsünden **FlybyCV**'yi aç (masaüstü kısayolu için kurulumda kutuyu işaretle).

### "Windows bilgisayarınızı korudu" uyarısı

Kurucu henüz **dijital olarak imzalı değil** (kod imzalama sertifikası ücretlidir). Bu yüzden Windows
SmartScreen şu uyarıyı gösterebilir: *"Windows bilgisayarınızı korudu — Microsoft Defender SmartScreen tanınmayan
bir uygulamanın başlatılmasını engelledi."* Bu, dosyanın zararlı olduğu anlamına gelmez; yalnızca yeterince yaygın
indirilmediği için tanınmadığı anlamına gelir. Dosyayı yalnızca bu projenin Releases sayfasından indirdiğinden
emin ol ve geçmek için:

1. Uyarıdaki **"Ek bilgi"** bağlantısına tıkla.
2. Çıkan **"Yine de çalıştır"** düğmesine bas.

Bazı antivirüs programları, uygulamanın paketleme aracı (PyInstaller) yüzünden yanlışlıkla uyarı verebilir.
Şüphen varsa yukarıdaki SHA-256 karşılaştırmasını yap.

## İlk açılış: kurulum sihirbazı

Uygulamayı ilk açtığında ana pencereden önce **Kurulum sihirbazı** çıkar. Dokuz adımdır; her "İleri"de
girdiklerin kaydedilir, yarıda kapatırsan kaldığın adımdan devam edersin.

1. **Hoş geldin + KVKK bilgilendirmesi** — onay kutusu zorunlu. Onaylamazsan uygulama açılır ama gerçek gönderim yapılmaz.
2. **Kişisel bilgi + şehir** — ad, e-posta, telefon, LinkedIn; firmaların aranacağı il.
3. **Hedef + meslek** — iş / staj / gönüllü staj / yarı zamanlı. Mesleğin listede yoksa "Diğer"i seçip kendi
   meslek adını ve arama kelimelerini yazarsın.
4. **CV** — "Kendi PDF'imi olduğu gibi ekle" (önerilen) ya da profilinden ATS uyumlu CV üret.
5. **Mail hesabı** — sağlayıcını seç, **uygulama şifresi** al (aşağıya bak), "Bağlantıyı test et".
6. **Firma kaynakları** — OpenStreetMap (ücretsiz) ve isteğe bağlı, ücretli olabilen Google Places.
7. **Mail şablonu** — firmalara giden mailin metni; "Önizle" ile bakabilirsin.
8. **Kendine test maili (zorunlu)** — mail hesabından SENİN adresine, gerçek bir başvurudaki metin ve CV ekiyle bir
   deneme maili gider; hiçbir firmaya gitmez. Gelen kutunu (gelmezse spam klasörünü) kontrol edip kutuyu işaretlersin.
9. **Özet** — **Test modu açık kalır:** Otopilot ilk denemelerde mailleri firmalara değil kendi adresine gönderir.

Sihirbazı sonradan **Ayarlar → "Kurulum sihirbazını yeniden aç"** ile tekrar açabilirsin. Her şeyi Ayarlar ve
Profil sekmelerinden değiştirebilirsin.

## Gmail uygulama şifresi nasıl alınır

Gmail, normal şifrenle dışarıdaki programların giriş yapmasına izin vermez; **uygulama şifresi** gerekir.
(Sihirbazın 5. adımı da bunları adım adım gösterir.)

1. Tarayıcıda [myaccount.google.com](https://myaccount.google.com) adresine gir, soldan **Güvenlik**'i aç.
2. **2 Adımlı Doğrulama** kapalıysa önce aç; uygulama şifresi yalnızca açıkken oluşturulabilir.
3. Hesap sayfasının arama kutusuna **"Uygulama şifreleri"** yaz ve o sayfayı aç
   (doğrudan adres: `myaccount.google.com/apppasswords`).
4. Uygulama adı olarak **FlybyCV** yaz, **Oluştur**'a bas.
5. Çıkan **16 haneli şifreyi** boşluksuz kopyala (pencere kapanınca bir daha gösterilmez).
6. Uygulamada şifreyi yapıştırıp **Bağlantıyı test et**'e bas.

Şifre dosyaya değil, Windows'un güvenli kasasına (Kimlik Bilgisi Yöneticisi) kaydedilir.

## Otopilot nasıl kullanılır

1. Kurulumu bitir; **Test modunu açık bırak** (varsayılan). Mailler firmalara değil kendi adresine gider.
2. **Otopilot** sekmesine geç ve **Başlat**'a bas. Test modu kapalıysa "GERÇEK GÖNDERİM" uyarısını onaylarsın.
3. Otopilot şirketleri bulur, sitelerini tarar, uygun bir e-posta adresi varsa CV'yi hazırlar ve limitlere
   uyarak gönderir. Sayaçlar ve log sekmede görünür.
4. **Durdur** ile bir sonraki adımı durdurabilirsin; kırmızı **ACİL DURDUR** her modda anında durdurur.
5. Mail ulaşmama (bounce) oranı yükselirse ya da art arda gönderim hatası olursa Otopilot kendini durdurur.

Otopilot yalnızca uygulama penceresi açıkken çalışır; Windows'la otomatik başlamaz.

**Gönderim limitleri** her zaman geçerlidir ve ayarlardan aşılamaz: günde en fazla 50, saatte en fazla 20 mail,
iki mail arasında en az 30 saniye. Varsayılanlar daha düşüktür (günde 20 mail, 09:00–18:00 mesai saati).
Aynı şirkete ikinci kez gönderim yapılmaz; kişi adı gibi görünen adreslere (ör. `ahmet.yilmaz@firma.com`)
otomatik modda gönderim yapılmaz.

### Şirketler nereden bulunur

- **CSV:** Kendi şirket listeni içe aktarırsın.
- **OpenStreetMap (ücretsiz, anahtarsız):** Şehirdeki firmaları açık haritadan alır. Küçük illerde kayıt az olabilir.
- **Teknokent rehberleri (anahtarsız):** Yalnızca teknik meslekler ve desteklenen şehirler için.
- **Google Places (isteğe bağlı, ÜCRETLİ olabilir):** Varsayılan kapalıdır. Açmak için kendi Google API anahtarını
  girersin; aylık istek sınırı koyabilirsin ve "Başlat"tan önce tahmini maliyet gösterilir. Google'ın bütçe uyarısı
  faturayı **durdurmaz**; Cloud Console'da günlük kota koymanı öneririz.

## Sık sorulan sorular

**Yapay zekâ CV'mde bir şey uyduruyor mu?**
Hayır. Yapay zekâ CV metni, tarih, unvan ya da yetenek **yazmaz**. Yalnızca şirketin sektör etiketini, profil
maddelerinin sıralamasını ve mail için tek cümlelik bir giriş cümlesini üretir; bunlar profiline karşı doğrulanır,
geçmezse atılır. İstersen CV özelleştirmeyi tamamen kapatırsın; yapay zekâ olmadan da uygulama çalışır.

**Hangi mail sağlayıcıları destekleniyor?**
Gmail ve Yandex. Outlook / Hotmail desteklenmiyor: Microsoft kişisel hesaplarda uygulama şifresiyle girişi
kapatıp OAuth2'ye zorunlu geçirdi, bu sürüm OAuth2 desteklemiyor.

**Mail geldi mi, nasıl bilirim?**
Otopilot sekmesindeki sayaçlara ve **Cevaplar** sekmesine bak. Test mailleri kendi gelen kutuna düşer; gelmezse
spam / gereksiz klasörünü kontrol et.

**Bir şirket "bir daha yazmayın" derse ne olur?**
Cevaplar sekmesinde "Şimdi kontrol et" ile cevaplar okunur. "Bir daha yazmayın", "listeden çıkarın",
"ilgilenmiyoruz" gibi cevaplarda şirketin alan adı **kalıcı engellenir**; hiçbir yoldan tekrar mail gitmez.
Yanlış yakalanırsa engeli Cevaplar sekmesinden kaldırabilirsin.

**Test modu ne yapar?**
Tüm mailleri gerçek şirkete değil kendi adresine gönderir. Gerçek başvuruya geçmeden önce örnekleri kontrol et.

**Bilgisayarı kapatırsam ne olur?**
Otopilot yalnızca uygulama açıkken çalışır; kapatınca durur, kaldığın yerden sonra devam edebilirsin.

**Bir sorun yaşıyorum, nereye bakmalıyım?**
Log dosyası: `%APPDATA%\FlybyCV\logs\flybycv.log`. Log kişisel veri (adın, e-posta adreslerin)
içerebilir; birine göndermeden önce bu bilgileri sil.

**Güncellemeler nasıl gelir?**
Uygulama günde en fazla bir kez GitHub'a "yeni sürüm var mı?" diye sorar ve varsa bir şerit gösterir. Hiçbir şey
kendiliğinden indirilmez ya da kurulmaz. Ayarlar → Güncellemeler'den kapatabilirsin.

## Gizlilik

- **Verilerin yalnızca bu bilgisayarda durur:** profil, CV, veritabanı ve üretilen PDF'ler
  `%APPDATA%\FlybyCV\` klasöründedir. Uygulamanın kendi sunucusu yoktur ve verini bir yere yüklemez.
- **Şifreler ve API anahtarları** dosyaya yazılmaz; Windows Kimlik Bilgisi Yöneticisi'nde saklanır.
- **Dışarı çıkan bağlantılar:** kendi mail sağlayıcın (gönderim ve gelen kutusu), şirketlerin internet siteleri,
  OpenStreetMap / teknokent rehberleri, **açtıysan** Google Places, **seçtiysen** yapay zekâ sağlayıcın (ad, telefon,
  e-posta gibi bilgiler yer tutucuyla değiştirilerek) ve günde bir kez GitHub (yalnızca uygulama adı ve sürümü).
  Ayrıntı için ilk açılışta çıkan bilgilendirme metnine bak.
- **Reklam, izleme ya da kullanım istatistiği toplanmaz.**

### Kaldırma

**Ayarlar → Uygulamalar**'dan (ya da Başlat menüsündeki kaldırıcıdan) FlybyCV'yi kaldır. Kaldırıcı,
kişisel verilerinin de silinip silinmeyeceğini sorar; **varsayılan cevap "Hayır"dır** (yeniden kurarsan
verilerin yerinde durur). "Evet" dersen `%APPDATA%\FlybyCV\` klasörü (varsa eski adın klasörü
`%APPDATA%\CV Autopilot\` de) ve Windows kasasındaki mail şifren / API anahtarların silinir; bu işlem geri alınamaz. Kaldırıcıyı sessiz modda çalıştırırsan veri hiçbir zaman silinmez.

## Lisans ve geliştirme

MIT lisansı ([LICENSE](LICENSE)), telif: Ali Utku Kılınçsoy. Sürüm notları: [CHANGELOG.md](CHANGELOG.md).
Kaynaktan çalıştırma, test etme ve derleme için: [docs/GELISTIRME.md](docs/GELISTIRME.md).
Proje kuralları: [CLAUDE.md](CLAUDE.md).
