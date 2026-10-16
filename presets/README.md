# Meslek paketleri

Bu klasördeki (`presets/professions/`) her `.json` dosyası bir meslek tanımlar. Yeni bir
meslek eklemek için **kod yazmaya gerek yok** — bu klasöre şemaya uyan yeni bir `.json`
dosyası eklemek yeterli, uygulama açılışta otomatik okur.

## Mesleğiniz listede yok mu?

Dosya eklemeden de kullanabilirsiniz: kurulum sihirbazında ya da Ayarlar'da meslek listesinin
**en sonundaki "Diğer"i** seçin, meslek adını ve arama kelimelerini (virgülle ayırarak, en çok 10)
yazın. Bu kelimeler Google Places aramalarında kullanılır; OpenStreetMap ve teknokent bu meslekte
kullanılmaz, mail şablonu genel şablondan gelir ve `{meslek}` yazdığınız ad olur. Daha isabetli
sonuç (OpenStreetMap etiketi, mesleğe özel şablon, firma türleri) için aşağıdaki gibi bir paket
dosyası ekleyin. **`diger` adı "Diğer" seçeneğine ayrılmıştır; bir paketin `id`'si `diger` olamaz.**

## Yeni bir meslek nasıl eklenir

1. `presets/professions/` içine, meslek adının sadeleştirilmiş hâliyle bir dosya adı seçin
   (küçük harf, Türkçe karaktersiz, boşluk yerine `_`). Örnek: `veterinerlik.json`.
2. Aşağıdaki alanları doldurun (örnek olarak `presets/professions/ic_mimarlik.json`'a bakın):

```json
{
  "id": "veterinerlik",
  "name_tr": "Veterinerlik",
  "technical": false,
  "search_queries_tr": [
    "veteriner kliniği",
    "veterinerlik hizmetleri firması",
    "hayvan hastanesi"
  ],
  "company_types": [
    "Veteriner kliniği",
    "Hayvan hastanesi",
    "Veterinerlik ürünleri firması"
  ],
  "default_mail_templates": {
    "job": {"subject": "...", "body": "..."},
    "internship": {"subject": "...", "body": "..."},
    "volunteer_internship": {"subject": "...", "body": "..."},
    "part_time": {"subject": "...", "body": "..."}
  }
}
```

- `id`: dosya adıyla (uzantısız) AYNI olmak zorunda, aksi halde paket atlanır.
- `name_tr`: kullanıcıya arayüzde gösterilecek meslek adı.
- `technical`: `true` yalnızca teknokent firma listelerinde (yazılım/elektronik/Ar-Ge
  ağırlıklı) iş bulma ihtimali gerçekten olan teknik mesleklerde (mühendislik gibi).
  Mimarlık/iç mimarlık/peyzaj mimarlığı, muhasebe, hukuk gibi meslekler `false`.
- `search_queries_tr`: Google Places/OSM aramalarında kullanılacak, GERÇEKÇİ Türkçe arama
  kelimeleri (en az 3). Uydurma sektör adı yazmayın — gerçekten var olan şirket/büro
  türlerini arayın (ör. "mali müşavirlik bürosu", "peyzaj mimarlığı ofisi").
- `company_types`: bu meslekte iş arayan birinin hedefleyeceği şirket/kurum türleri
  (en az 3).
- `default_mail_templates`: dört hedef için de (`job`=iş, `internship`=staj,
  `volunteer_internship`=gönüllü staj, `part_time`=yarı zamanlı iş) bir konu+gövde önerisi.
  Yalnızca şu yer tutucular kullanılabilir: `{ad_soyad}`, `{sirket_adi}`, `{okul}`,
  `{bolum}`, `{hedef}`, `{meslek}`, `{sehir}`, `{telefon}`, `{eposta}`, `{linkedin}`
  (bunlar `src/cv_builder/mail_template.py::PLACEHOLDERS`de tanımlı — başka bir yer
  tutucu yazarsanız paket reddedilir). Şablonlar TR'dir; Ayarlar'da dil "İngilizce"
  seçildiğinde bunun yerine genel (mesleğe özel olmayan) İngilizce şablonlar önerilir
  (`presets/mail_templates/`). Kullanıcı önerilen metni Ayarlar'da kendi diliyle/üslubuyla
  değiştirebilir; elle düzenlenmiş bir metnin üzerine hedef/meslek değişince SORMADAN
  yazılmaz (bkz. `src/ui/settings_data.py::suggested_mail_template`).

3. Dosyayı kaydedin, uygulamayı yeniden başlatın. Şema hatalı olan bir dosya uygulamayı
   ÇÖKERTMEZ — yalnızca o dosya atlanır ve günlüğe (log) bir uyarı yazılır; hatayı görmek
   için `logs/` klasörüne bakın.

## Şema

Gerçek kaynak: `src/core/professions.py::ProfessionPreset`. Değişirse bu belge de
güncellenmelidir.

## Not

Bu paketler Ayarlar'daki "Meslek" seçimine ve mail şablonu önerisine BAĞLANDI (PLAN.md
Adım 10.4). Ancak `search_queries_tr`/`company_types`/`technical` alanları HENÜZ otopilotun
şirket keşfine bağlanmadı (bkz. PLAN.md Adım 10.6) — bugünkü yazılım/bilgisayar mühendisliği
davranışı (`core/tags.py`, `llm/keyword_provider.py`, `autopilot_queries` varsayılanı) bu
klasörden etkilenmeden aynen çalışmaya devam eder.

## Mail sağlayıcı ön ayarları

Bu klasörde ayrıca `mail_providers.json` var (PLAN.md Adım 10.7) — meslek paketlerinden farklı
bir dosya: Ayarlar sekmesindeki "Mail sağlayıcısı" seçiminin SMTP/IMAP host/port/güvenlik
bilgisini tutar (Gmail, Yandex, Outlook/Hotmail — bu sonuncusu `"supported": false` ile
işaretli, Microsoft OAuth2'ye zorunlu geçirdiği için bu sürümde desteklenmiyor). "Özel"
sağlayıcı bu dosyada YOK, kullanıcı host/port'u Ayarlar'da kendisi girer. Gerçek kaynak:
`src/core/mail_providers.py::MailProviderPreset`.
