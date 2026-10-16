"""src/core/mail_template_presets.py testleri (PLAN.md Faz 10 Adım 10.4c)."""
import json
import logging

from src.core.mail_template_presets import load_generic_mail_templates
from src.core.paths import mail_template_presets_dir
from src.cv_builder.mail_template import LANGUAGES, TARGETS


def test_real_presets_cover_every_target_and_language():
    """8 şablon (4 hedef x 2 dil) hepsi mevcut ve yükleniyor."""
    presets = load_generic_mail_templates()
    for target in TARGETS:
        for language in LANGUAGES:
            assert (target, language) in presets, (target, language)
    assert len(presets) == len(TARGETS) * len(LANGUAGES)


def test_real_presets_dir_points_at_real_folder_with_json_files():
    directory = mail_template_presets_dir()
    assert directory.is_dir()
    assert list(directory.glob("*.json"))


def test_missing_directory_returns_empty_and_warns(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        result = load_generic_mail_templates(tmp_path / "does_not_exist")
    assert result == {}
    assert "does_not_exist" in caplog.text


def test_missing_file_is_simply_absent_not_an_error(tmp_path):
    """Klasör var ama `job_en.json` yoksa -- yalnızca o giriş sözlükte yok, çökme yok."""
    (tmp_path / "job_tr.json").write_text(
        json.dumps({"subject": "s", "body": "b"}), encoding="utf-8"
    )
    result = load_generic_mail_templates(tmp_path)
    assert ("job", "tr") in result
    assert ("job", "en") not in result


def test_broken_json_is_skipped_not_raised(tmp_path, caplog):
    (tmp_path / "job_tr.json").write_text("{not valid json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = load_generic_mail_templates(tmp_path)
    assert result == {}
    assert "job_tr.json" in caplog.text


def test_blank_subject_is_rejected(tmp_path, caplog):
    (tmp_path / "job_tr.json").write_text(
        json.dumps({"subject": "  ", "body": "b"}), encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        result = load_generic_mail_templates(tmp_path)
    assert result == {}
    assert "job_tr.json" in caplog.text


def test_unknown_placeholder_is_rejected(tmp_path, caplog):
    (tmp_path / "job_tr.json").write_text(
        json.dumps({"subject": "{bilinmeyen}", "body": "b"}), encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        result = load_generic_mail_templates(tmp_path)
    assert result == {}
    assert "job_tr.json" in caplog.text
