# Yayın Öncesi İnceleme Raporu (Adım 10.9i)

Otomatik tarama: `python scripts/prepublish_scan.py` (izlenen + `.gitignore` ile dışlanmamış her dosya).
Aşağıdaki sonuçlar 01.10.2026 tarihli taramaya aittir; yayından hemen önce taramayı yeniden çalıştır.

## Otomatik tarama sonucu

316 dosya tarandı; aranan kişisel değerler: yerel profildeki ad, e-posta, telefon, LinkedIn, şirket/okul/proje
adları, `git config` e-postası/adı ve bilgisayar kullanıcı adı.

| Bulgu | Değerlendirme |
| --- | --- |
| `PLAN.md` (dosyanın kendisi) | Yayınlanmayacak; yeni depoda olmamalı (aşağıda 3. madde). |
| (`README.md`, `LICENSE`, kurucu `.iss`) yazarın adı | Bilerek (telif/yayıncı); taramada bu üç dosya yalnızca "ad" kuralından muaf. |
| `tests/` içindeki sahte parolalar (`sifre123`, `gizli-sifre-1234`) | Sahte test verisi; testlerde `password-assignment` kuralı kapalı. Gerçek anahtar kalıpları testlerde de aranır. |

Bulunmayanlar: API anahtarı kalıbı, yerel kullanıcı yolu, gerçek e-posta/telefon/LinkedIn, veritabanı/log/PDF/profil dosyası.

## Elle inceleme (senin kararın)

1. **`CLAUDE.md` — herkese açık olabilir (önerilir).** Proje kuralları ve mimari; kişisel veri yok. Katkıda
   bulunanlar için yararlı. Türkçe olması sorun değil.
2. **`SOZLUK.md` — herkese açık olabilir.** 66 satırlık terim sözlüğü, kişisel veri yok.
3. **`PLAN.md` — YAYINLANMAYACAK (karar verildi).** Yerelde kalır, `.gitignore`'dadır ve temiz depoya
   eklenmez. Taramada `forbidden-file` olarak görünür: eski depoda hâlâ izlendiği için bu beklenir, yeni temiz
   depoda çıkmamalı. Not: kaynak kodundaki birçok yorum/docstring "bkz. PLAN.md" diye anıyor; yayında bu
   gönderme boşta kalır (zararsız).
4. **Gerçek bir firma adresi — giderildi.** Kod, test ve belgelerde `team@example.com` /
   `ik@example.com` ile değiştirildi.
5. **`data/master_profile.example.json` ve `data/companies.example.csv`:** içindeki kişi/şirketler uydurma
   (`example.com`, `ornek-*.example.com`); gerçek veri değil. Yayınlanabilir.
6. **Git geçmişi:** eski depoda `logs/cv_autopilot.log` 3 commit'te duruyor. Karar gereği GitHub'a **geçmişsiz yeni
   depo** yüklenecek (10.9k); eski klasörü yedek olarak sakla ve ASLA bu eski depoyu push etme.
7. **Yeni depoda commit e-postası:** GitHub noreply adresi (10.9k'da ayarlanır). Yerel `git config user.email`
   şu an gerçek bir Gmail adresi; yeni depoyu açmadan önce değiştir.

## Yayından önce son kontrol listesi

- [ ] `python scripts/prepublish_scan.py` → yalnızca yukarıdaki "bilerek" bulgular kaldı.
- [x] `PLAN.md` yayınlanmayacak; temiz depoya `git add` ile eklenmediğini `git ls-files` ile doğrula.
- [ ] Yeni depoda `git config user.email` noreply adresi.
- [ ] `git ls-files` çıktısında `data/` altında yalnızca `.gitkeep`, `master_profile.example.json`, `companies.example.csv` var.
