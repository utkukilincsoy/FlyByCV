"""src/cv_builder/profile_editor.py testleri."""
import json

from src.cv_builder.profile_editor import backup_profile, write_profile
from src.cv_builder.profile_loader import load_profile
from tests.conftest import build_sample_profile


def test_backup_profile_returns_none_when_file_missing(tmp_path):
    assert backup_profile(tmp_path / "yok.json") is None


def test_backup_profile_copies_existing_file(tmp_path):
    path = tmp_path / "master_profile.json"
    path.write_text('{"eski": true}', encoding="utf-8")

    backup_path = backup_profile(path)

    assert backup_path is not None
    assert backup_path.read_text(encoding="utf-8") == '{"eski": true}'
    assert backup_path.name.startswith("master_profile.json.bak-")
    assert path.exists()  # kaynak dosya yerinde kalmalı


def test_write_profile_creates_file_and_loads_back(tmp_path):
    path = tmp_path / "data" / "master_profile.json"
    profile = build_sample_profile()

    backup_path = write_profile(path, profile)

    assert backup_path is None  # ilk yazımda yedeklenecek eski dosya yok
    assert path.exists()
    reloaded = load_profile(path)
    assert reloaded.personal_info.name == profile.personal_info.name
    assert reloaded == profile


def test_write_profile_backs_up_previous_version(tmp_path):
    path = tmp_path / "master_profile.json"
    profile = build_sample_profile()
    write_profile(path, profile)

    updated = profile.model_copy(
        update={"personal_info": profile.personal_info.model_copy(update={"name": "Yeni Ad"})}
    )
    backup_path = write_profile(path, updated)

    assert backup_path is not None
    assert backup_path.exists()
    backed_up_data = json.loads(backup_path.read_text(encoding="utf-8"))
    assert backed_up_data["personal_info"]["name"] == profile.personal_info.name
    assert load_profile(path).personal_info.name == "Yeni Ad"


def test_write_profile_omits_none_fields(tmp_path):
    path = tmp_path / "master_profile.json"
    profile = build_sample_profile()

    write_profile(path, profile)

    data = json.loads(path.read_text(encoding="utf-8"))
    # PersonalInfo.phone hiç verilmedi (None) -- `exclude_none=True` bu yüzden yazılmamalı.
    assert "phone" not in data["personal_info"]
