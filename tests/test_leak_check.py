"""scripts/leak_check.ps1 (derleme sonrası sızıntı denetimi, Adım 10.9f) ve build_exe.ps1'e bağlanışı.

Gerçek .exe DERLENMEZ: denetim işlevi geçici bir klasörde elle yerleştirilen dosyalar üzerinde
PowerShell'de çalıştırılır (Windows PowerShell yoksa atlanır)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEAK_SCRIPT = PROJECT_ROOT / "scripts" / "leak_check.ps1"
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_exe.ps1"

POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

#: Pakette olmaması GEREKEN dosyalar (alt klasörler ve büyük harfli uzantılar dahil).
LEAKED = [
    "app.db",
    "master_profile.json",
    "cv.pdf",
    "docs/belge.pdf",
    "docs/BUYUK.PDF",
    "logs/flybycv.log",
    "nested/deep/uygulama.LOG",
    "yedek.db",
    "veri.sqlite",
    "veri.sqlite3",
    "app.db-wal",
    "app.db-shm",
    ".env",
    ".env.local",
    "sub/.env",
]

#: Pakette olması BEKLENEN dosyalar: yanlış alarm vermemeli.
ALLOWED = [
    "FlybyCV.exe",
    "master_profile.example.json",
    "companies.example.csv",
    "presets/cities.json",
    "templates/cv.html",
    "_internal/python312.dll",
    "_internal/envision.txt",  # ".env" ile başlayan ad değil, ".env" uzantısı da değil
    "_internal/dblib.txt",
    "_internal/blog.txt",
]


def _find_leaked(root: Path) -> set[str]:
    env = {**os.environ, "LEAK_SCRIPT": str(LEAK_SCRIPT), "LEAK_DIR": str(root)}
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            ". $env:LEAK_SCRIPT; Find-LeakedFiles -Path $env:LEAK_DIR | ForEach-Object { $_.FullName }",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    found = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return {Path(path).relative_to(root).as_posix() for path in found}


def _make_tree(root: Path, names: list[str]) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell yok")
def test_every_forbidden_file_is_found_and_no_allowed_file_is(tmp_path):
    _make_tree(tmp_path, LEAKED + ALLOWED)

    assert _find_leaked(tmp_path) == set(LEAKED)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell yok")
def test_clean_tree_reports_nothing(tmp_path):
    _make_tree(tmp_path, ALLOWED)

    assert _find_leaked(tmp_path) == set()


def test_build_script_runs_the_leak_check_from_the_separate_file_and_prints_the_version():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert 'leak_check.ps1' in text and "Find-LeakedFiles -Path $DistDir" in text
    assert "$AppName $AppVersion derleniyor" in text  # sürüm derleme başında yazdırılır
    # Eski, yalnızca dört kalıplı satır içi süzgeç geri gelmesin.
    assert '($_.Name -eq "app.db")' not in text


def test_leak_check_lists_the_log_db_env_and_sqlite_types():
    text = LEAK_SCRIPT.read_text(encoding="utf-8")

    for extension in (".log", ".db", ".sqlite", ".env"):
        assert f'"{extension}"' in text
