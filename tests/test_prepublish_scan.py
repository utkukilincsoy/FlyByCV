"""`scripts/prepublish_scan.py` (Adım 10.9i) testleri. Sahte anahtarlar çalışma anında birleştirilir ki bu dosya
kendisi taramada bulgu üretmesin."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "prepublish_scan.py"
spec = importlib.util.spec_from_file_location("prepublish_scan", SCRIPT)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)

FAKE_ANTHROPIC = "sk-" + "ant-" + "a" * 30
FAKE_GOOGLE = "AI" + "za" + "b" * 35


def rules(findings):
    return {f.rule for f in findings}


@pytest.mark.parametrize(
    "text, rule",
    [
        (f'key = "{FAKE_ANTHROPIC}"', "anthropic-key"),
        (f"GOOGLE={FAKE_GOOGLE}", "google-api-key"),
        ("-----BEGIN " + "RSA PRIVATE KEY-----", "private-key-block"),
        ('password = "gercek-parola-1"', "password-assignment"),
    ],
)
def test_secret_patterns_are_found(text, rule):
    assert rule in rules(scan.scan_text("src/x.py", text))


def test_placeholder_keys_and_passwords_are_not_flagged():
    text = 'api_key = "sk-..."\npassword = ""\npassword = "<sifre>"\nsifre=%PASSWORD%'
    assert scan.scan_text("README.md", text) == []


def test_test_files_may_use_fake_passwords_but_not_real_key_shapes():
    assert scan.scan_text("tests/test_x.py", 'password="sifre123"') == []
    assert "anthropic-key" in rules(scan.scan_text("tests/test_x.py", FAKE_ANTHROPIC))


def test_local_user_paths_are_flagged_but_placeholders_are_not():
    assert "local-path" in rules(scan.scan_text("a.md", "C:\\Users\\Birisi\\Desktop\\x"))
    assert scan.scan_text("a.md", "C:\\Users\\<kullanici>\\x ve C:\\Users\\Public\\x") == []


def test_personal_values_are_found_and_masked():
    personal = [scan.PersonalValue("email", "kisi@ornek.com"), scan.PersonalValue("phone", "0532 111 22 33")]
    findings = scan.scan_text("a.md", "yaz: Kisi@Ornek.com\ntel 05321112233", personal)

    assert [(f.line, f.rule) for f in findings] == [(1, "personal-email"), (2, "personal-phone")]
    assert all("kisi@ornek.com" not in f.detail.lower() for f in findings)


def test_name_is_allowed_in_license_only_for_the_name_rule():
    personal = [scan.PersonalValue("name", "Ali Veli Kaya"), scan.PersonalValue("email", "ali@ornek.com")]

    assert scan.scan_text("LICENSE", "Copyright Ali Veli Kaya", personal) == []
    assert "personal-name" in rules(scan.scan_text("PLAN.md", "Ali Veli Kaya", personal))
    assert "personal-email" in rules(scan.scan_text("LICENSE", "ali@ornek.com", personal))


def test_short_personal_values_are_ignored():
    assert scan.scan_text("a.md", "Ali Ali", [scan.PersonalValue("name", "Ali")]) == []


@pytest.mark.parametrize(
    "name", ["data/app.db", "output/cv.pdf", "logs/flybycv.log", ".env", ".env.local", "x/master_profile.json", "PLAN.md"]
)
def test_forbidden_file_names(name):
    assert scan.check_file_name(name) is not None


def test_normal_file_names_are_fine():
    assert scan.check_file_name("data/master_profile.example.json") is None
    assert scan.check_file_name("README.md") is None


def test_binary_files_are_skipped(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"\0\1\2" + FAKE_ANTHROPIC.encode())

    assert scan.read_text(tmp_path / "a.bin") is None


def test_load_personal_values_reads_profile_and_git_config(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    profile = {
        "personal_info": {"name": "Ali Veli Kaya", "email": "ali@ornek.com", "phone": "", "linkedin": ""},
        "experience": [{"company": "Kaya Yazılım"}],
        "education": [{"school": "Örnek Üniversitesi"}],
        "projects": [{"name": "Süper Proje"}],
    }
    (tmp_path / "data" / "master_profile.json").write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(scan, "_git_config", lambda root, key: "")

    values = {(v.kind, v.value) for v in scan.load_personal_values(tmp_path)}

    assert ("name", "Ali Veli Kaya") in values
    assert ("email", "ali@ornek.com") in values
    assert ("profile-item", "Kaya Yazılım") in values
    assert ("profile-item", "Örnek Üniversitesi") in values
    assert ("profile-item", "Süper Proje") in values
    assert not any(kind == "phone" for kind, _ in values)


def test_scan_files_and_candidate_listing_in_a_git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("data/\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "app.db").write_text("x", encoding="utf-8")  # .gitignore ile dışlı -> taranmaz
    (tmp_path / "ok.md").write_text("temiz", encoding="utf-8")
    (tmp_path / "bad.py").write_text(f'K = "{FAKE_ANTHROPIC}"', encoding="utf-8")
    (tmp_path / "notes.log").write_text("x", encoding="utf-8")

    files = scan.list_candidate_files(tmp_path)
    findings = scan.scan_files(tmp_path, files)

    assert "data/app.db" not in files
    assert {(f.path, f.rule) for f in findings} == {("bad.py", "anthropic-key"), ("notes.log", "forbidden-file")}


def test_main_exit_code_reflects_findings(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(scan, "list_candidate_files", lambda root: ["a.md"])
    (tmp_path / "a.md").write_text("temiz", encoding="utf-8")
    assert scan.main(["--no-personal"]) == 0

    (tmp_path / "a.md").write_text(FAKE_ANTHROPIC, encoding="utf-8")
    assert scan.main(["--no-personal"]) == 1


@pytest.mark.parametrize("term", ["bean" + "boo", "easter" + "_egg", "aliutku" + "2001"])
def test_leftover_terms_are_flagged(term):
    findings = scan.scan_text("src/x.py", f'x = "{term.upper()}"')
    assert "easter-egg" in rules(findings)


def test_leftover_file_name_is_flagged():
    finding = scan.check_file_name("src/ui/easter" + "_egg.py")
    assert finding is not None and finding.rule == "easter-egg"


def test_main_exits_nonzero_when_leftover_present(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("t = 'bean" + "boo'", encoding="utf-8")
    monkeypatch.setattr(scan, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(scan, "list_candidate_files", lambda root: ["a.py"])
    assert scan.main(["--no-personal"]) == 1
