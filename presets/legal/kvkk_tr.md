# KVKK ve 6563 sayılı Kanun – Kısa Bilgilendirme

**Bu metin genel bir bilgilendirmedir; hukuki tavsiye değildir.** Kanunlar ve yorumlar değişebilir, bu metin bir avukat tarafından incelenmemiştir ve uygulamanın yasalara "uygun olduğunu" garanti etmez. Kendi durumunuz için emin değilseniz bir avukata danışın.

## Bu uygulama ne yapar?

FlybyCV, seçtiğiniz meslekteki şirketleri bulur, şirketlerin internet sitelerinde **herkese açık olarak yayımlanmış** kurumsal e-posta adreslerini (ör. kariyer@, ik@, info@) çıkarır ve bu adreslere başvuru e-postanızı ve CV'nizi **sizin e-posta hesabınızdan** gönderir. İlan sitelerini taramaz, web formu doldurmaz.

## Hangi veriler, nerede durur?

- **Şirket verileri:** Şirket adı, internet sitesi, şehir ve herkese açık kurumsal e-posta adresi. Kişi adı gibi görünen adresler (ör. ahmet.yilmaz@firma.com) otomatik modda kullanılmaz; yalnızca siz onay kuyruğunda görüp onaylarsanız gönderilir.
- **Sizin verileriniz:** Profiliniz, CV'niz, ayarlarınız, gönderim ve cevap kayıtlarınız **yalnızca bu bilgisayarda** durur (veritabanı, profil dosyası ve üretilen PDF'ler). Uygulamanın kendi sunucusu yoktur; verilerinizi bize ya da başka bir sunucuya yüklemez.
- **Şifreler ve API anahtarları** bilgisayarınızın güvenli kasasında (Windows Kimlik Bilgisi Yöneticisi) saklanır; dosyaya yazılmaz.

## Verinin bilgisayarınızdan çıktığı yerler

Veriler yerelde durur, ancak uygulamanın çalışması için şu bağlantılar yapılır:

- **E-posta:** Başvurularınız ve e-posta kutunuzun okunması, kendi e-posta sağlayıcınız (ör. Gmail, Outlook, Yandex) üzerinden olur.
- **Şirket siteleri:** Şirketlerin internet siteleri açılıp okunur.
- **Şirket bulma kaynakları:** OpenStreetMap (Overpass), teknokent rehberleri ve — siz açarsanız — Google Places'e arama sorguları (şehir + meslek arama kelimeleri) gönderilir.
- **Yeni sürüm kontrolü:** Uygulama, günde en fazla bir kez GitHub'a (GitHub Releases) "yeni sürüm var mı?" diye sorar. Bu isteğe profiliniz, şirket verileriniz ya da ayarlarınız eklenmez; yalnızca uygulamanın adı ve sürümü ile — her internet isteğinde olduğu gibi — IP adresiniz GitHub'a görünür. Hiçbir şey otomatik indirilmez ya da kurulmaz. Ayarlar sekmesindeki "Güncellemeler" bölümünden kapatabilirsiniz.
- **Yapay zekâ (isteğe bağlı):** Bir yapay zekâ sağlayıcısı seçer ve anahtarını girerseniz şirket sitesinden çıkan kısa metinler ve gelen cevapların (ad, telefon, e-posta gibi bilgiler yer tutucuyla değiştirilmiş) metni seçtiğiniz sağlayıcıya gider. "none" seçiliyse hiçbir şey gitmez.

## Bilmeniz gerekenler (genel bilgi)

- **6698 sayılı KVKK:** Kişisel veri, kimliği belirli ya da belirlenebilir bir kişiye ait veridir. Kişi adı içeren bir e-posta adresi kişisel veri olabilir. Bu tür verileri işlerken meşru amaç, ölçülülük ve güvenlik gibi ilkeler geçerlidir; ilgili kişilerin başvuru ve itiraz hakları vardır.
- **6563 sayılı Elektronik Ticaretin Düzenlenmesi Hakkında Kanun:** Ticari elektronik iletiler için genel olarak alıcının önceden onayı ve iletiyi reddetme hakkı söz konusudur; bazı alıcı türleri için (ör. tacir ve esnaf) istisnalar bulunur. Bireysel bir iş/staj başvurusunun bu Kanun kapsamında "ticari elektronik ileti" sayılıp sayılmayacağı durumdan duruma değişebilir; ancak uygulama çok sayıda şirkete toplu başvuru gönderebildiği için dikkatli olmanız önerilir.
- **Reddetme (çıkma) hakkı:** Bir şirket "bir daha yazmayın", "listeden çıkarın", "ilgilenmiyoruz" gibi bir cevap verirse, uygulama o şirketin alan adını **kalıcı olarak engeller** ve bir daha hiçbir yoldan (keşif, CSV, kuyruk, gönderim) e-posta göndermez. Cevaplar sekmesinden elle de engel koyabilir ya da kaldırabilirsiniz. Bir şirketin isteğini uygulamanın yakalayamadığı bir biçimde (ör. telefon) iletmesi halinde engeli elle koymak sizin sorumluluğunuzdadır.

## Sizin sorumluluklarınız

- Gönderimleri kendi adınıza yaptığınızı ve e-postaların içeriğinden **sizin** sorumlu olduğunuzu kabul edersiniz.
- Gerçek başvurulara geçmeden önce Test modunda kendi adresinize gelen örnekleri kontrol etmelisiniz.
- Uygulamadaki gönderim sınırlarını (günlük/saatlik limit, bekleme süreleri, mesai saati) atlatmaya çalışmamalı; istenmeyen toplu e-posta göndermemelisiniz.
- Şirketlerden gelen çıkma/itiraz taleplerine uymalısınız.
- Bu bilgilendirmeyi okuduğunuzu ve yukarıdakileri anladığınızı onaylamadan **gerçek gönderim yapılmaz** (Test modu, kendi adresinize gönderdiği için onaysız da çalışır).

*Bu onay bu bilgisayarda bir kez sorulur; metin değişirse yeniden sorulur.*
