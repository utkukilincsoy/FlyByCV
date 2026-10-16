"""`src/core/version.py` (sürümün TEK kaynağı) ve derleme dosyalarının ona bağlılığı testleri."""
import re
from pathlib import Path

import pytest

from src.core import version

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Sürümün okunması GEREKEN derleme dosyaları: hiçbirinde sürüm sabit yazılı olmamalı.
BUILD_FILES = [
    PROJECT_ROOT / "flybycv.spec",
    PROJECT_ROOT / "scripts" / "build_exe.ps1",
]
BUILD_FILES += sorted((PROJECT_ROOT / "installer").glob("*.iss"))
BUILD_FILES += sorted((PROJECT_ROOT / "scripts").glob("build_*.ps1"))
BUILD_FILES = list(dict.fromkeys(BUILD_FILES))

SEMVER_LITERAL = re.compile(r"(?<![\w.])\d+\.\d+\.\d+(?:\.\d+)?(?![\w.])")


def test_version_is_plain_major_minor_patch():
    assert re.fullmatch(r"\d+\.\d+\.\d+", version.__version__)


def test_app_name_and_default_repo():
    assert version.APP_NAME == "FlybyCV"
    # Yayın deposu tanımlı (kullanıcı adı burada tekrar yazılmaz: yayın öncesi taramaya takılır).
    assert version.GITHUB_REPO is not None and version.GITHUB_REPO.endswith("/flybycv")


@pytest.mark.parametrize(
    "text, expected",
    [("1.0.0", (1, 0, 0, 0)), ("2.13.4", (2, 13, 4, 0)), ("10.0.100", (10, 0, 100, 0))],
)
def test_version_tuple_appends_a_zero_build_number(text, expected):
    assert version.version_tuple(text) == expected


@pytest.mark.parametrize("text", ["1.0", "v1.0.0", "1.0.0-beta", "1.0.0.1", "", "a.b.c"])
def test_version_tuple_rejects_anything_but_major_minor_patch(text):
    with pytest.raises(ValueError):
        version.version_tuple(text)


def test_version_tuple_defaults_to_the_current_version():
    assert version.version_tuple() == version.version_tuple(version.__version__)


@pytest.mark.parametrize("path", BUILD_FILES, ids=lambda p: p.name)
def test_no_build_file_hardcodes_a_version(path):
    text = path.read_text(encoding="utf-8")

    assert version.__version__ not in text
    assert SEMVER_LITERAL.findall(text) == []


def test_spec_reads_version_and_name_from_the_single_source():
    text = (PROJECT_ROOT / "flybycv.spec").read_text(encoding="utf-8")

    assert "version.py" in text
    assert "version_tuple()" in text
    assert "version=VERSION_INFO" in text
    assert 'APP_NAME = "' not in text


def test_build_script_reads_version_from_the_single_source():
    text = (PROJECT_ROOT / "scripts" / "build_exe.ps1").read_text(encoding="utf-8")

    assert "src.core.version" in text
    assert '$AppName = "' not in text


def test_main_sets_application_name_and_version_from_the_single_source():
    text = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

    assert "setApplicationVersion(__version__)" in text
    assert "setApplicationName(APP_NAME)" in text
