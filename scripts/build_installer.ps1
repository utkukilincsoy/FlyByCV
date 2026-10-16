<#
.SYNOPSIS
    Inno Setup 6 veya 7 ile Setup.exe üretir ve SHA-256 özetini yazar (Faz 10 - Adım 10.9g).

.DESCRIPTION
    Önkoşul: `scripts\build_exe.ps1` ile `dist\<Uygulama>` klasörü derlenmiş olmalı.
    1) Ad/sürümü src\core\version.py'den okur (sürüm bu betikte yazılmaz).
    2) dist\<Uygulama> içinde kişisel veri arar (scripts\leak_check.ps1) -- bulunursa durur.
    3) ISCC.exe'yi (Inno Setup derleyicisi) bulup installer\flybycv.iss'i derler.
    4) dist\installer\ altına Setup.exe ve <Setup.exe>.sha256 yazar.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$IssPath = Join-Path $ProjectRoot "installer\flybycv.iss"

if (-not (Test-Path $Python)) {
    Write-Error "Sanal ortam bulunamadı. Önce şunları çalıştır:`n  .venv\Scripts\activate`n  pip install -r requirements.txt"
    exit 1
}

# Uygulama adı ve sürüm TEK yerde: src\core\version.py.
$versionInfo = & $Python -c "from src.core.version import APP_NAME, __version__; print(APP_NAME); print(__version__)"
if ($LASTEXITCODE -ne 0 -or $versionInfo.Count -ne 2) {
    Write-Error "src\core\version.py okunamadı."
    exit 1
}
$AppName = $versionInfo[0]
$AppVersion = $versionInfo[1]
$SourceDir = Join-Path $ProjectRoot "dist\$AppName"
$OutputDir = Join-Path $ProjectRoot "dist\installer"
$OutputName = ($AppName -replace "\s+", "-") + "-Setup-$AppVersion"
Write-Host "$AppName $AppVersion kurucusu hazırlanıyor." -ForegroundColor Cyan

if (-not (Test-Path (Join-Path $SourceDir "$AppName.exe"))) {
    Write-Error "Derlenmiş uygulama yok: $SourceDir`nÖnce çalıştır: powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1"
    exit 1
}

# --- Kişisel veri sızıntısı denetimi (CLAUDE.md kural 6 ve 7) -----------------------
. (Join-Path $PSScriptRoot "leak_check.ps1")
$forbidden = Find-LeakedFiles -Path $SourceDir
if ($forbidden) {
    Write-Host "`nKURUCU ÜRETİLMEDİ -- pakette kişisel veri bulundu:" -ForegroundColor Red
    foreach ($file in $forbidden) {
        Write-Host "  $($file.FullName)" -ForegroundColor Red
    }
    exit 1
}
Write-Host "Sızıntı yok." -ForegroundColor Green

# --- ISCC.exe (Inno Setup derleyicisi) -----------------------------------------------
# Sıra: 1) INNO_ISCC ortam değişkeni (açıkça verilen yol; yanlışsa HATA, sessizce atlanmaz),
# 2) Inno Setup 7, sonra 6; her biri için Program Files, Program Files (x86), %LOCALAPPDATA%\Programs, 3) PATH.
$Iscc = $null
if ($env:INNO_ISCC) {
    if (-not (Test-Path -LiteralPath $env:INNO_ISCC -PathType Leaf)) {
        Write-Error "INNO_ISCC ortam değişkenindeki dosya yok: $env:INNO_ISCC"
        exit 1
    }
    $Iscc = $env:INNO_ISCC
} else {
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA "Programs")) |
        Where-Object { $_ }
    $candidates = foreach ($major in @("7", "6")) {
        foreach ($root in $roots) { Join-Path $root "Inno Setup $major\ISCC.exe" }
    }
    $Iscc = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $Iscc) {
        $onPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($onPath) { $Iscc = $onPath.Source }
    }
}
if (-not $Iscc) {
    Write-Error "Inno Setup 6 veya 7 bulunamadı (ISCC.exe). https://jrsoftware.org/isdl.php adresinden kur ya da yolu INNO_ISCC ortam değişkeniyle ver."
    exit 1
}
Write-Host "Derleyici: $Iscc" -ForegroundColor DarkGray

# --- Derleme -------------------------------------------------------------------------
New-Item -ItemType Directory -Force $OutputDir | Out-Null
& $Iscc "/DMyAppName=$AppName" "/DMyAppVersion=$AppVersion" "/DMySourceDir=$SourceDir" `
    "/DMyOutputDir=$OutputDir" "/DMyOutputName=$OutputName" $IssPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "Inno Setup derlemesi başarısız oldu (çıkış kodu $LASTEXITCODE)."
    exit 1
}

$SetupPath = Join-Path $OutputDir "$OutputName.exe"
if (-not (Test-Path $SetupPath)) {
    Write-Error "Derleme bitti ama beklenen dosya yok: $SetupPath"
    exit 1
}

# --- SHA-256 (sha256sum biçimi: "<özet> *<dosya adı>") ---------------------------------
$hash = (Get-FileHash -LiteralPath $SetupPath -Algorithm SHA256).Hash.ToLowerInvariant()
$ShaPath = "$SetupPath.sha256"
[System.IO.File]::WriteAllText($ShaPath, "$hash *$OutputName.exe`n", (New-Object System.Text.UTF8Encoding($false)))

Write-Host "`nKurucu hazır:" -ForegroundColor Green
Write-Host "  $SetupPath"
Write-Host "  $ShaPath"
Write-Host "  SHA-256: $hash"
