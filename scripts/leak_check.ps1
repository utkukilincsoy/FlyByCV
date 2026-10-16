<#
.SYNOPSIS
    Paketlenmiş klasörde kişisel veri / sır taşıyabilecek dosya arar (Faz 10 - Adım 10.9f).

.DESCRIPTION
    `build_exe.ps1` bunu nokta-kaynakla (dot-source) yükler ve `dist\<Uygulama>` üzerinde çalıştırır;
    ayrı bir dosyada durmasının sebebi, gerçek bir derleme yapmadan `tests\test_leak_check.py` ile
    sınanabilmesidir. Bulunan HER dosya derlemeyi başarısız sayar (CLAUDE.md kural 6 ve 7).

    Aranan dosyalar (büyük/küçük harf fark etmez):
      - tam ad: app.db, master_profile.json, cv.pdf
      - uzantı: .pdf, .log, .db, .sqlite, .sqlite3, .db-wal, .db-shm, .env
      - ad kalıbı: .env.* (ör. .env.local)
    flybycv.spec yalnızca data\master_profile.example.json'ı BİLEREK ekler; o ad listede değil.

.EXAMPLE
    . scripts\leak_check.ps1
    Find-LeakedFiles -Path "dist\FlybyCV"
#>

$script:LeakForbiddenNames = @("app.db", "master_profile.json", "cv.pdf")
$script:LeakForbiddenExtensions = @(".pdf", ".log", ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".env")

function Find-LeakedFiles {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    Get-ChildItem -LiteralPath $Path -Recurse -File -Force | Where-Object {
        ($script:LeakForbiddenNames -contains $_.Name.ToLowerInvariant()) -or
        ($script:LeakForbiddenExtensions -contains $_.Extension.ToLowerInvariant()) -or
        ($_.Name -like ".env.*")
    }
}
