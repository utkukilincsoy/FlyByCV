# Değişiklik Günlüğü

Biçim [Keep a Changelog](https://keepachangelog.com/tr/1.1.0/) düzenini, sürümleme
[Anlamsal Sürümleme](https://semver.org/lang/tr/) kuralını izler.

## [1.0.0] - 2026-10-14

İlk sürüm.

### Eklendi
- **Yedekleme:** her gün ilk açılışta veritabanı ve profil, veri klasöründeki `backups/` altına tek bir zip olarak
  yedeklenir; en yeni 7 yedek tutulur. Şifreler ve API anahtarları yedeğe girmez (Windows şifre kasasında kalır).
- **Ayarlar > Yedekleme:** "Şimdi yedekle" ve "Yedekten geri yükle". Geri yüklemeden önce onay sorulur, mevcut
  veriler ayrıca yedeklenir ve uygulama kendini yeniden başlatır.
- **Otopilot:** tek "Başlat" ile şirket bulma → site tarama → CV hazırlama → gönderim döngüsü.
- **Kurulum sihirbazı:** ilk açılışta dokuz adım (KVKK bilgilendirmesi, kişisel bilgi, meslek, CV, mail hesabı,
  firma kaynakları, mail şablonu, kendine test maili, özet).
- **Şirket kaynakları:** CSV içe aktarma, OpenStreetMap (anahtarsız), teknokent rehberleri, isteğe bağlı Google Places.
- **CV:** kendi PDF'ini olduğu gibi ekleme ya da profilinden ATS uyumlu (tek sütun, seçilebilir metin) CV üretme;
  isteğe bağlı şirkete göre vurgu. Yapay zekâ CV metni yazmaz; çıktısı profile karşı doğrulanır.
- **Gönderim güvenliği:** günlük/saatlik limit, rastgele bekleme, mesai saati, acil durdur, güvenlik freni
  (bounce oranı / art arda hata), aynı şirkete ikinci gönderimin engellenmesi, test modu.
- **Cevaplar:** gelen kutusu okuma, cevap eşleştirme ve sınıflandırma; "bir daha yazmayın" diyen şirketin alan
  adının kalıcı engellenmesi.
- **KVKK / 6563 bilgilendirmesi:** onaylanmadan gerçek gönderim yapılmaz.
- **Güncelleme kontrolü:** günde en fazla bir kez "yeni sürüm var mı?" bilgisi; otomatik indirme/kurma yok,
  Ayarlar'dan kapatılabilir.
- **Windows kurucusu:** yönetici izni istemez; kaldırırken kişisel verinin silinip silinmeyeceği sorulur
  (varsayılan: silme).

### Değişti
- **Yeni ad: FlybyCV** (eski adı CV Autopilot) ve yeni simge. Eski adla kurulmuş bir sürüm varsa yeni kurulum
  onun yerine geçer, eski kısayollar kalkar. İlk açılışta `%APPDATA%\CV Autopilot\` klasöründeki veriler
  `%APPDATA%\FlybyCV\`'ye, kasadaki mail şifresi ve API anahtarları da yeni ada **kopyalanır**; eskiler silinmez.
  Eski `CV_AUTOPILOT_HOME` ortam değişkeni, `FLYBYCV_HOME` yoksa hâlâ okunur.

### Güvenlik
- **Site tarama:** şirket sitesi taranırken yalnızca genel internet adreslerine istek atılır. Yerel/özel ağ
  adreslerine (bu bilgisayar, ev ağındaki modem vb.) giden adresler ve yönlendirmeler engellenir, sayfa atlanır
  ve loga yazılır.
