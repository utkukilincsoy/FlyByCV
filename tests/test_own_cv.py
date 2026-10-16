import logging

import pytest

from src.cv_builder import own_cv
from src.cv_builder.own_cv import (
    has_own_cv,
    own_cv_path_if_present,
    own_cv_status_text,
    remove_own_cv,
    set_own_cv,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("FLYBYCV_HOME", str(tmp_path))


def test_has_own_cv_false_when_nothing_uploaded():
    assert has_own_cv() is False
    assert own_cv_path_if_present() is None
    assert "seçilmedi" in own_cv_status_text()


def test_set_own_cv_copies_file(tmp_path):
    source = tmp_path / "kaynak.pdf"
    source.write_bytes(b"%PDF-1.4 test")

    destination = set_own_cv(source)

    assert destination.exists()
    assert destination.read_bytes() == b"%PDF-1.4 test"
    assert has_own_cv() is True
    assert own_cv_path_if_present() == destination
    assert "Kayıtlı" in own_cv_status_text()


def test_set_own_cv_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        set_own_cv(tmp_path / "yok.pdf")


def test_set_own_cv_rejects_non_pdf(tmp_path):
    source = tmp_path / "kaynak.docx"
    source.write_text("merhaba")

    with pytest.raises(ValueError):
        set_own_cv(source)


def test_set_own_cv_rejects_renamed_non_pdf(tmp_path):
    source = tmp_path / "aslinda_metin.pdf"
    source.write_text("bu bir PDF değil", encoding="utf-8")

    with pytest.raises(ValueError, match="geçerli bir PDF değil"):
        set_own_cv(source)
    assert has_own_cv() is False


def test_set_own_cv_rejects_too_large_file(tmp_path, monkeypatch):
    monkeypatch.setattr(own_cv, "MAX_OWN_CV_BYTES", 100)
    source = tmp_path / "buyuk.pdf"
    source.write_bytes(b"%PDF-1.4 " + b"x" * 200)

    with pytest.raises(ValueError, match="çok büyük"):
        set_own_cv(source)
    assert has_own_cv() is False


def test_set_own_cv_accepts_file_at_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(own_cv, "MAX_OWN_CV_BYTES", 100)
    source = tmp_path / "sinirda.pdf"
    source.write_bytes(b"%PDF-1.4 " + b"x" * 91)  # tam 100 bayt

    assert set_own_cv(source).exists()


def test_remove_own_cv(tmp_path):
    source = tmp_path / "kaynak.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    set_own_cv(source)

    assert remove_own_cv() is True
    assert has_own_cv() is False
    assert remove_own_cv() is False


def test_own_cv_logs_do_not_contain_full_path(tmp_path, caplog):
    source = tmp_path / "kaynak.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    with caplog.at_level(logging.INFO):
        set_own_cv(source)
        remove_own_cv()
    assert str(tmp_path) not in caplog.text
    assert "kaynak.pdf" in caplog.text
