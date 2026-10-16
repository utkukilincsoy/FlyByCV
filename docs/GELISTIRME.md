# Geliştirici Rehberi

Bu dosya uygulamayı kaynaktan çalıştırmak, test etmek ve paketlemek isteyenler içindir. Kullanıcı
rehberi için `README.md`; proje kuralları ve mimari için `CLAUDE.md`.

## Geliştirme ortamı (Windows)

```
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
pytest
python main.py
```

## Bağımlılık güvenlik taraması

Geliştirme araçları `requirements-dev.txt`'te (`pip install -r requirements-dev.txt`); bilinen açık taraması için: `pip-audit --local --skip-editable` (ağa çıkar, sonuç "No known vulnerabilities found" olmalı).

## Veriler nerede durur

- **Paketlenmiş `.exe` çalışırken:** `%APPDATA%\FlybyCV\` altında
  (`data\master_profile.json`, `data\app.db`, `output\<şirket>\*.pdf`, `logs\`).
- **Geliştirmede (`python main.py`):** proje klasörünün altında, `data\`, `output\` ve `logs\`
  (git'e girmez, `.gitignore`'da).
- Ayrı bir kurulum denemek için `FLYBYCV_HOME` ortam değişkeni başka bir klasöre yönlendirilebilir;
  kasadaki kayıtlar da o kurulum için ayrı tutulur. Eski adı `CV_AUTOPILOT_HOME` yalnızca `FLYBYCV_HOME`
  yoksa okunur.
- **Eski ad (CV Autopilot):** paketlenmiş uygulama ilk açılışta `%APPDATA%\CV Autopilot\` varsa ve
  `%APPDATA%\FlybyCV\` yoksa (ya da içinde hiç dosya yoksa) eski klasörü yenisine **kopyalar**
  (`src\core\rename_migration.py`); kasadaki `cv-autopilot` kayıtları da `flybycv` servisine kopyalanır
  (`secrets.migrate_legacy_secrets`). Eskiler silinmez; yalnızca kaldırıcıda "verilerimi de sil" denirse
  onlar da silinir.

## Log dosyası

Paketlenmiş uygulamada `%APPDATA%\FlybyCV\logs\flybycv.log` (1 MB × 3 yedek ile döner).
Uygulama konsolsuz çalıştığı için (`--windowed`) hatalar ekrana değil buraya yazılır; beklenmeyen bir çökmede
ekrana çıkan hata penceresi de bu dosyanın yolunu gösterir. Geliştirmede aynı mekanizma proje kökündeki
`logs\` klasörünü kullanır.

## .exe nasıl derlenir

Gereksinimler: `.venv` sanal ortamı kurulu ve `pip install -r requirements.txt` çalıştırılmış olmalı.

```
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

Betik sırasıyla:

1. Gömülü Chromium'u (headless shell) `.venv` içine kurar (yoksa).
2. `pyinstaller flybycv.spec` ile **onedir** (tek klasör) derleme yapar — onefile DEĞİL, çünkü
   onefile her açılışta paketi `%TEMP%`'e açar ve açılış yavaşlar.
3. `dist\FlybyCV` içinde kişisel veri (`app.db`, `master_profile.json`, `*.pdf`, `*.log`, `*.db`,
   `*.sqlite`, `.env` ...; tam liste `scripts\leak_check.ps1`) arar; bulursa derlemeyi başarısız sayıp
   `dist\`ı siler.
4. `--selftest` çalıştırıp sonucu log dosyasından gösterir.

Sonuç: `dist\FlybyCV\FlybyCV.exe`. Öz-testi atlamak için `-SkipSelfTest`, Chromium'u yeniden
indirmek için `-ForceChromiumReinstall`.

Sürüm numarası TEK yerde durur: `src\core\version.py`. Derleme dosyalarına sürüm yazılmaz.

## Kurucu (Setup.exe) nasıl derlenir

Önkoşul: yukarıdaki `.exe` derlemesi yapılmış olmalı ve [Inno Setup 6 veya 7](https://jrsoftware.org/isdl.php)
kurulu olmalı.

```
powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
```

`ISCC.exe` sırayla şurada aranır: `INNO_ISCC` ortam değişkeni (tam yol; verilmişse ve yoksa hata verir), sonra Inno Setup 7 ve 6
(Program Files, Program Files (x86), `%LOCALAPPDATA%\Programs`), en son PATH. Örnek:
`$env:INNO_ISCC = "C:\Program Files\Inno Setup 7\ISCC.exe"`.

Çıktı `dist\installer\` altında: `FlybyCV-Setup-<sürüm>.exe` ve `.sha256` dosyası. Kurucu betiği:
`installer\flybycv.iss` (`AppId` değiştirilmemeli: eski adla kurulmuş sürümün üstüne kurulur, eski
kısayollar ve eski program klasörü `[InstallDelete]` ile kaldırılır).

## Yayın öncesi

Yayına çıkarmadan önce `scripts\prepublish_scan.py` çalıştırılır (kişisel veri, anahtar, yerel yol taraması).
