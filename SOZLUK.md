# Sözlük – Projede geçen terimler

| Terim | Ne demek? |
|---|---|
| **Faz** | Projenin bir aşaması. Birini bitirmeden diğerine geçmiyoruz. |
| **Terminal** | Komut yazdığın metin ekranı. VS Code'da `Ctrl + ö` (veya Görünüm → Terminal) ile açılır. |
| **Python** | Uygulamayı yazdığımız programlama dili. |
| **pip** | Python kütüphanelerini (hazır kod paketlerini) indiren araç. |
| **Kütüphane / paket** | Başkalarının yazdığı, bizim kullandığımız hazır kod. Örn. Playwright. |
| **requirements.txt** | Projenin ihtiyaç duyduğu kütüphanelerin listesi. `pip install -r requirements.txt` hepsini kurar. |
| **Sanal ortam (venv)** | Bu projeye özel, izole bir Python kutusu. Kütüphaneler bilgisayarının geri kalanını etkilemez. Terminalde satır başında `(.venv)` görüyorsan açıktır. |
| **Git** | Kodun "kayıt noktası" sistemi. Bir şey bozulursa eski hâle dönebilirsin. |
| **Commit** | Git'te bir kayıt noktası oluşturmak. "Oyunu kaydetmek" gibi. |
| **CLAUDE.md** | Claude Code'un her oturumda okuduğu proje hafızası ve kurallar dosyası. |
| **/clear** | Claude Code sohbetini sıfırlar. Limit tasarrufu sağlar, proje dosyaları silinmez. |
| **/model** | Claude Code'da kullanılan modeli (Sonnet / Opus / Haiku) değiştirir. |
| **Plan modu** | `Shift + Tab` ile açılır. Claude kod yazmadan önce ne yapacağını anlatır, sen onaylarsın. |
| **API** | Bir programın başka bir servisle konuşma kapısı. Örn. uygulamamız Claude'a API ile soru sorar. |
| **API anahtarı** | Bu kapının şifresi. Kimseyle paylaşma, koda yazma. |
| **LLM** | Büyük dil modeli (Claude, Gemini gibi yapay zekalar). |
| **Halüsinasyon** | Yapay zekanın olmayan bir bilgiyi uydurması. Bizim projede bunu kodla engelliyoruz. |
| **Etiket (tag)** | Profil maddelerine koyduğumuz konu işaretleri. Örn. `yapay-zeka`, `siber-guvenlik`. |
| **Playwright** | Kodla tarayıcıyı kontrol eden araç. Şirket sitelerini okumak ve PDF üretmek için kullanıyoruz. |
| **Scraping / tarama** | Bir web sayfasındaki bilgiyi otomatik okumak. |
| **SMTP** | Mail göndermek için kullanılan protokol. |
| **"Diğer" meslek** | Meslek listesinde yoksa, meslek adını ve arama kelimelerini kendin yazdığın seçenek. Kelimeler harita/Google Places aramalarında kullanılır; dosya eklemek gerekmez. |
| **Kurulum sihirbazı** | Uygulama ilk kez açılınca çıkan, adım adım ilerleyen kurulum penceresi (kişisel bilgi, meslek, CV, mail hesabı, firma kaynakları, mail şablonu, test maili). Önceden kurmuş birine kendiliğinden açılmaz; Ayarlar'dan "Kurulum sihirbazını yeniden aç" ile tekrar açılabilir. |
| **Kendine test maili** | Sihirbazın son adımlarından biri: mail hesabından SENİN adresine, gerçek bir başvurudaki metin ve CV ekiyle giden deneme maili. Hiçbir firmaya gitmez, veritabanına kayıt açmaz. Gelen kutunda (gelmezse spam klasöründe) görüp onaylamadan kurulum bitmez. |
| **IMAP** | Gelen kutusunu okumak için kullanılan protokol. |
| **Uygulama şifresi** | Gmail'in, programların senin hesabınla mail atabilmesi için verdiği özel şifre. İki adımlı doğrulama açık olmalı. |
| **MX kaydı** | Bir alan adının mail alıp alamadığını gösteren kayıt. Göndermeden önce kontrol ediyoruz. |
| **Rate limit** | Belirli sürede yapılabilecek en fazla işlem sayısı. Örn. günde 20 mail. |
| **keyring** | Şifreleri işletim sisteminin güvenli kasasında saklayan kütüphane. |
| **SQLite** | Tek dosyadan oluşan küçük veritabanı. Şirketler, gönderimler burada tutulur. |
| **JSON** | Veriyi düzenli saklamak için metin formatı. Profilin bu formatta. |
| **Mock** | Testte gerçek servisi (örn. API) çağırmak yerine sahte cevap kullanmak. |
| **Test (pytest)** | Kodun doğru çalıştığını otomatik kontrol eden küçük programlar. `pytest` komutuyla çalışır. |
| **PyInstaller** | Python uygulamasını tek bir `.exe` dosyasına çeviren araç. |
| **.gitignore** | Git'in kaydetmemesi gereken dosyaların listesi (şifreler, kişisel veriler). |
| **Foreign key (yabancı anahtar)** | Bir tablodaki satırın başka bir tablodaki satırla bağlantısını gösteren alan (örn. bir başvurunun hangi şirkete ait olduğu). |
| **SQLModel** | SQLite tablolarını Python sınıfı gibi yazmamızı sağlayan kütüphane. |
| **Session (oturum)** | Veritabanına yapılan okuma/yazma işlemlerini bir arada tutan, sonunda kaydedilen (commit) çalışma birimi. |
| **PRAGMA foreign_keys** | SQLite'a "tablolar arası bağlantı kurallarını da kontrol et" diyen ayar; varsayılan kapalıdır, biz açıyoruz. |
| **UNIQUE (tekil) alan** | Bir sütunda aynı değerin iki kez bulunmasını veritabanının engellediği kural (örn. aynı alan adına iki kez şirket eklenemez). |
| **Test modu** | Ayarlarda açıkken tüm mailler gerçek şirkete değil, kullanıcının kendi adresine gider. Yanlışlıkla toplu mail atmayı önler; kapatılınca gönderim gerçek olur. |
| **STARTTLS** | Düz bir bağlantıyı sonradan şifrelemeye (TLS) yükselten yöntem. Gmail'e mail göndermek için 587 numaralı kapıda kullanılıyor. |
| **Message-ID** | Her maile verilen benzersiz bir kimlik numarası. Gelen bir cevabın hangi başvuruya ait olduğunu bulmak için (Faz 7) kullanılacak. |
| **Ek (attachment)** | Maile eklenen dosya. Bizde her zaman tek bir CV PDF'i. |
| **.eml** | Bir mailin tüm içeriğini (başlıklar, gövde, ek) tek dosyada tutan format. Outlook/Thunderbird'de çift tıklayıp önizleyebilirsin; hiç gönderim yapmaz. |
| **Kuyruk** | Gönderilmeyi bekleyen başvuruların listesi. Manuel modda sadece onayladığın başvurular sıradan çıkar. |
| **Acil Durdur** | Bir ayar (`sending_paused`): açıkken hiçbir mail gönderilmez, açık kalan bir gönderim döngüsü bile bir sonraki adımda durur. |
| **Saat dilimi (Europe/Istanbul) / tzdata** | Bilgisayarın saatini Türkiye saatine çevirmek için kullanılan kural seti. Windows'ta bunun için `tzdata` adlı küçük bir paket gerekir. |
| **WAL** | SQLite'ın "aynı anda birden fazla program veritabanını okuyup yazabilsin" moduna geçme ayarı (Write-Ahead Log). |
| **Otopilot** | Tek "Başlat" düğmesiyle şirket bulma → site tarama → CV hazırlama → gönderimin hepsini kendi kendine, sırayla yapan mod. Zamanlayıcı/Windows'la otomatik başlama YOK; sadece pencere açıkken çalışır. |
| **Teknokent** | Üniversitelere bağlı, teknoloji şirketlerinin bir arada bulunduğu bölge (örn. ODTÜ Teknokent). Bu şirketlerin herkese açık "firma rehberi" sayfalarından, API anahtarı gerekmeden şirket listesi çekiyoruz. |
| **Bounce** | "Bu adrese mail ulaşmadı" bildirimi (örn. adres artık kullanılmıyor). Sık bounce almak Otopilot'u otomatik olarak durdurur. |
| **SKU** | Google'ın faturalandırma kalemi. Aynı API isteği, istediğin alanlara göre farklı SKU'ya (Essentials, Pro, Enterprise) girer ve fiyatı değişir. |
| **Alan maskesi (field mask)** | Google Places isteğinde "yalnızca şu alanları getir" listesi. Ne kadar çok alan istersen (örn. web sitesi) istek o kadar pahalı SKU'ya girer. |
| **Overpass** | OpenStreetMap verisini sorgulamak için kullanılan ücretsiz, anahtarsız sorgu servisi. OSM kaynağı şirketleri buradan alır. |
| **place_id** | Google'ın bir yere verdiği kalıcı kimlik. Şartlar gereği Places'ten yalnızca bunu kalıcı saklayabiliriz; aynı yer ikinci kez işlenmesin diye kullanılır. |
| **bbox (sınırlayıcı kutu)** | Bir şehri/ilçeyi çevreleyen dikdörtgen koordinat aralığı. OSM sorgusu bu kutu içinde yapılır. |
| **KVKK** | 6698 sayılı Kişisel Verilerin Korunması Kanunu. Kişi adı içeren bir e-posta adresi gibi verilerin nasıl işlenmesi gerektiğini düzenler. Uygulama ilk açılışta kısa bir bilgilendirme gösterir (hukuki tavsiye değildir). |
| **6563 sayılı Kanun** | Elektronik Ticaretin Düzenlenmesi Hakkında Kanun. Ticari elektronik iletiler (toplu ticari e-posta) için alıcı onayı ve "çıkma" hakkını düzenler. |
| **Opt-out** | Alıcının "bir daha yazmayın / listeden çıkarın" demesi. Böyle bir cevap gelince şirketin alan adı kalıcı engellenir. |
| **Engelli alan adı (engel listesi)** | Bir daha hiçbir yoldan (keşif, CSV, kuyruk, gönderim) mail atılmayacak alan adları. Cevaplar sekmesinden elle eklenip kaldırılabilir. |
| **Güvenlik freni** | Bounce oranı çok yükselirse veya art arda mail gönderim hatası olursa Otopilot'un kendini otomatik durdurması (Acil Durdur ile aynı anahtarı kullanır). |
