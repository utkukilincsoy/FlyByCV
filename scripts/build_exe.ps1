<#
.SYNOPSIS
    FlybyCV'yi tek klasörlük (onedir) .exe olarak derler (Faz 8 - Adım 8.5).

.DESCRIPTION
    1) Gömülü Chromium'un (headless shell) `.venv` içinde kurulu olduğunu denetler;
       yoksa `PLAYWRIGHT_BROWSERS_PATH=0` ile kurar (bkz. flybycv.spec,
       src/core/paths.configure_playwright_browsers_path()).
    2) `pyinstaller flybycv.spec` ile derler.
    3) `dist\FlybyCV` içinde kişisel veri / sır (app.db, master_profile.json, cv.pdf,
       *.pdf, *.log, *.db, *.sqlite, .env ...; tam liste scripts\leak_check.ps1) arar -- bulunursa
       derleme BAŞARISIZ sayılır ve dist\ silinir (CLAUDE.md kural 6 ve 7).
    4) `--selftest` çalıştırıp log dosyasının son satırlarını gösterir.

.PARAMETER SkipSelfTest
    Derlemeden sonra `--selftest`i çalıştırmaz.

.PARAMETER ForceChromiumReinstall
    Chromium zaten kuruluysa bile yeniden indirir (`playwright install --force`).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipSelfTest,
    [switch]$ForceChromiumReinstall
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PyInstaller = Join-Path $ProjectRoot ".venv\Scripts\pyinstaller.exe"
$SpecPath = Join-Path $ProjectRoot "flybycv.spec"
$LocalBrowsers = Join-Path $ProjectRoot ".venv\Lib\site-packages\playwright\driver\package\.local-browsers"

if (-not (Test-Path $Python) -or -not (Test-Path $PyInstaller)) {
    Write-Error "Sanal ortam / PyInstaller bulunamadı. Önce şunları çalıştır:`n  .venv\Scripts\activate`n  pip install -r requirements.txt"
    exit 1
}

# Uygulama adı ve sürüm TEK yerde: src\core\version.py (Adım 10.9a). Bu betikte sürüm yazılmaz.
$versionInfo = & $Python -c "from src.core.version import APP_NAME, __version__; print(APP_NAME); print(__version__)"
if ($LASTEXITCODE -ne 0 -or $versionInfo.Count -ne 2) {
    Write-Error "src\core\version.py okunamadı."
    exit 1
}
$AppName = $versionInfo[0]
$AppVersion = $versionInfo[1]
$DistDir = Join-Path $ProjectRoot "dist\$AppName"
Write-Host "$AppName $AppVersion derleniyor." -ForegroundColor Cyan

# --- 1) Gömülü Chromium (headless shell) -----------------------------------------
# PLAYWRIGHT_BROWSERS_PATH=0: tarayıcı playwright paketinin KENDİ klasörünün altına
# (driver\package\.local-browsers) kurulur, bu sayede playwright'in kendi PyInstaller
# kancası (hook-playwright.sync_api.py) onu otomatik olarak pakete gömer. Çalışma
# zamanında AYNI değişkeni frozen .exe kendisi ayarlıyor (src/core/paths.py) -- burada
# yalnızca DERLEME zamanı kurulumu yapılıyor.
$hasChromium = $false
if (Test-Path $LocalBrowsers) {
    $chromiumDirs = Get-ChildItem $LocalBrowsers -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "chromium*" }
    $hasChromium = $chromiumDirs.Count -gt 0
}

if ($ForceChromiumReinstall -or -not $hasChromium) {
    Write-Host "Gömülü Chromium (headless shell) kuruluyor -- PLAYWRIGHT_BROWSERS_PATH=0 ..." -ForegroundColor Cyan
    $env:PLAYWRIGHT_BROWSERS_PATH = "0"
    $installArgs = @("-m", "playwright", "install", "chromium", "--only-shell")
    if ($ForceChromiumReinstall) {
        $installArgs += "--force"
    }
    & $Python @installArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "playwright install başarısız oldu (çıkış kodu $LASTEXITCODE)."
        exit 1
    }
} else {
    Write-Host "Gömülü Chromium zaten kurulu: $LocalBrowsers" -ForegroundColor DarkGray
}

# --- 2) Derleme --------------------------------------------------------------------
Write-Host "`nPyInstaller ile derleniyor (onedir)..." -ForegroundColor Cyan
& $PyInstaller $SpecPath --noconfirm --clean
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller derlemesi başarısız oldu (çıkış kodu $LASTEXITCODE)."
    exit 1
}

if (-not (Test-Path $DistDir)) {
    Write-Error "Derleme bitti ama beklenen klasör yok: $DistDir"
    exit 1
}

# --- 3) Kişisel veri sızıntısı denetimi (CLAUDE.md kural 6 ve 7) --------------------
# flybycv.spec yalnızca data\master_profile.example.json'ı BİLEREK ekler; onun dışında
# app.db / master_profile.json / *.pdf / *.log / *.db / *.sqlite / .env pakette ASLA olmamalı.
# Aranan dosya listesi scripts\leak_check.ps1'de (tests\test_leak_check.py ile sınanır).
. (Join-Path $PSScriptRoot "leak_check.ps1")
Write-Host "`nKişisel veri sızıntısı denetleniyor: $DistDir" -ForegroundColor Cyan
$forbidden = Find-LeakedFiles -Path $DistDir

if ($forbidden) {
    Write-Host "`nDERLEME BAŞARISIZ -- pakette kişisel veri bulundu:" -ForegroundColor Red
    foreach ($file in $forbidden) {
        Write-Host "  $($file.FullName)" -ForegroundColor Red
    }
    Write-Host "Kişisel veri içeren dist\ klasörü siliniyor..." -ForegroundColor Red
    Remove-Item -Recurse -Force (Join-Path $ProjectRoot "dist")
    exit 1
}
Write-Host "Sızıntı yok: pakette yalnızca beklenen dosyalar var." -ForegroundColor Green

# --- 3b) presets/ klasörünün TAMAMEN pakete girdiğinin denetimi ---------------------
# flybycv.spec artık presets/ klasörünün tamamını tek satırda kopyalıyor; burada
# birkaç temsili dosyayı kontrol ederek "spec'te presets tekrar tek tek dosya listesine
# döndürülür de yeni bir preset unutulur" hatasını derleme anında yakalıyoruz.
$PresetsDist = Join-Path $DistDir "_internal\presets"
if (-not (Test-Path $PresetsDist)) {
    $PresetsDist = Join-Path $DistDir "presets"
}

$requiredPresetFiles = @(
    (Join-Path $PresetsDist "cities.json"),
    (Join-Path $PresetsDist "mail_providers.json"),
    (Join-Path $PresetsDist "mail_templates\job_tr.json"),
    (Join-Path $PresetsDist "professions\yazilim.json")
)
$missingPresetFiles = $requiredPresetFiles | Where-Object { -not (Test-Path $_) }

if ($missingPresetFiles) {
    Write-Host "`nDERLEME BAŞARISIZ -- presets/ klasörü eksik pakete girmiş:" -ForegroundColor Red
    foreach ($file in $missingPresetFiles) {
        Write-Host "  EKSİK: $file" -ForegroundColor Red
    }
    Write-Host "flybycv.spec içindeki 'datas' listesinde presets/ klasörünün TAMAMININ kopyalandığını kontrol et." -ForegroundColor Red
    exit 1
}
Write-Host "presets/ klasörü tam: cities.json, mail_providers.json, mail_templates/ ve professions/ pakette." -ForegroundColor Green

# --- 3c) Tema/ikon veri dosyaları ve uygulama simgesi pakette mi --------------------------
# qt-material (tema XML + yazı tipleri), qtawesome (Font Awesome .ttf) ve assets/ (simge)
# flybycv.spec'te elle toplanır; biri düşerse paketlenmiş uygulamada tema/ikon sessizce kaybolur.
$InternalDist = Join-Path $DistDir "_internal"
if (-not (Test-Path $InternalDist)) {
    $InternalDist = $DistDir
}
$requiredUiFiles = @(
    (Join-Path $InternalDist "qt_material\themes\light_blue.xml"),
    (Join-Path $InternalDist "qt_material\themes\dark_blue.xml"),
    (Join-Path $InternalDist "qt_material\material.qss.template"),
    (Join-Path $InternalDist "qtawesome\fonts\fontawesome5-solid-webfont-5.15.4.ttf"),
    (Join-Path $InternalDist "assets\app_icon.svg"),
    (Join-Path $InternalDist "assets\app_icon.ico")
)
$missingUiFiles = $requiredUiFiles | Where-Object { -not (Test-Path $_) }
if ($missingUiFiles) {
    Write-Host "`nDERLEME BAŞARISIZ -- tema/ikon dosyaları eksik pakete girmiş:" -ForegroundColor Red
    foreach ($file in $missingUiFiles) {
        Write-Host "  EKSİK: $file" -ForegroundColor Red
    }
    Write-Host "flybycv.spec içindeki collect_data_files('qt_material'/'qtawesome') ve assets/ satırlarını kontrol et." -ForegroundColor Red
    exit 1
}
Write-Host "Tema/ikon dosyaları tam: qt-material, qtawesome ve assets/ pakette." -ForegroundColor Green

# --- 4) --selftest -------------------------------------------------------------------
if ($SkipSelfTest) {
    Write-Host "`nDerleme tamamlandı: $DistDir" -ForegroundColor Green
    exit 0
}

$ExePath = Join-Path $DistDir "$AppName.exe"
Write-Host "`nÖz-test çalıştırılıyor: `"$ExePath`" --selftest" -ForegroundColor Cyan
& $ExePath --selftest
$selfTestExit = $LASTEXITCODE

# Derleme konsolsuz (--windowed) olduğu için stdout görünmez; rapor yalnızca log
# dosyasında (bkz. src/core/logging_setup.py, src/core/selftest.py).
$LogPath = Join-Path $env:APPDATA "$AppName\logs\flybycv.log"
if (Test-Path $LogPath) {
    Write-Host "`nÖz-test raporu ($LogPath):" -ForegroundColor Cyan
    Get-Content $LogPath -Tail 20
} else {
    Write-Host "`nLog dosyası bulunamadı: $LogPath" -ForegroundColor Yellow
}

if ($selfTestExit -eq 0) {
    Write-Host "`nDerleme VE öz-test başarılı: $DistDir" -ForegroundColor Green
    exit 0
} else {
    Write-Host "`nDerleme tamamlandı ama öz-test HATA verdi (çıkış kodu $selfTestExit) -- yukarıdaki rapora bak." -ForegroundColor Yellow
    exit 1
}
