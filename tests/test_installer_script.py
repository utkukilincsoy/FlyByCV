"""Inno Setup kurucu betiğinin (Adım 10.9g) değişmez özellikleri; kurucu burada DERLENMEZ."""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ISS = (PROJECT_ROOT / "installer" / "flybycv.iss").read_text(encoding="utf-8-sig")
BUILD = (PROJECT_ROOT / "scripts" / "build_installer.ps1").read_text(encoding="utf-8")


def test_app_id_is_a_fixed_guid():
    assert re.search(r"^AppId=\{\{[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}\}$", ISS, re.MULTILINE)


def test_app_id_did_not_change_with_the_rename():
    """CV Autopilot -> FlybyCV: aynı AppId, yeni kurulum eskisinin yerine geçsin diye."""
    assert "AppId={{B7E4A2D1-5C3F-4A86-9D0E-2F1B6C8A7E35}" in ISS


def test_upgrade_from_the_old_name_removes_old_shortcuts_and_program_folder():
    assert '#define LegacyAppName "CV Autopilot"' in ISS
    install_delete = ISS.split("\n[InstallDelete]\n", 1)[1].split("\n[", 1)[0]
    assert r'Name: "{autoprograms}\{#LegacyAppName}.lnk"' in install_delete
    assert r'Name: "{autodesktop}\{#LegacyAppName}.lnk"' in install_delete
    assert r'Name: "{autopf}\{#LegacyAppName}"' in install_delete
    assert "{userappdata}" not in install_delete  # kurulum kullanıcı verisini ASLA silmez
    assert "UsePreviousAppDir=no" in ISS


def test_uninstall_yes_also_deletes_the_old_app_data_folder():
    before_yes, yes_branch = ISS.split("if Answer = IDYES then", 1)
    assert r"{userappdata}\{#LegacyAppName}" in ISS
    assert "DeleteDataDir(UserDataDir());" in yes_branch
    assert "DeleteDataDir(LegacyUserDataDir());" in yes_branch
    assert "DeleteDataDir(" not in before_yes.split("procedure CurUninstallStepChanged", 1)[1]


def test_installer_does_not_ask_for_admin_rights():
    assert "PrivilegesRequired=lowest" in ISS


def test_start_menu_and_optional_desktop_shortcut_exist():
    assert "{autoprograms}" in ISS
    assert "{autodesktop}" in ISS
    assert 'Name: "desktopicon"' in ISS


def test_uninstall_asks_before_deleting_user_data_and_defaults_to_no():
    assert "UninstallSilent()" in ISS
    assert "MB_YESNO or MB_DEFBUTTON2" in ISS
    assert ", IDNO)" in ISS  # sessiz/bastırılmış kutuda varsayılan cevap
    assert "{userappdata}" in ISS


def test_uninstall_purges_secrets_only_after_a_yes_answer():
    before_yes, yes_branch = ISS.split("if Answer = IDYES then", 1)
    assert "PurgeSecrets();" in yes_branch
    assert "--purge-secrets" in ISS
    # Çağrı yalnızca "Evet" dalında; öncesinde yalnızca tanımı (procedure PurgeSecrets) var.
    assert before_yes.count("PurgeSecrets();") == before_yes.count("procedure PurgeSecrets();")


def test_version_comes_from_defines_not_literals():
    assert "#ifndef MyAppVersion" in ISS
    assert "AppVersion={#MyAppVersion}" in ISS


def test_build_script_reads_version_and_checks_for_leaks_and_writes_sha256():
    assert "src.core.version" in BUILD
    assert "Find-LeakedFiles" in BUILD
    assert "Get-FileHash" in BUILD
    assert ".sha256" in BUILD


def test_build_script_finds_inno_setup_7_and_6_and_honours_env_override():
    assert "INNO_ISCC" in BUILD
    assert 'foreach ($major in @("7", "6"))' in BUILD
    assert "ProgramFiles(x86)" in BUILD and "LOCALAPPDATA" in BUILD


def test_setup_architecture_is_only_written_for_inno_setup_7_and_later():
    # SetupArchitecture 6.x'te yok (derleme hatası); ISPP sürüm koşuluna bağlı olmalı.
    before, after = ISS.split("SetupArchitecture=x64", 1)
    assert before.rstrip().endswith("#if Ver >= 0x07000000")
    assert after.lstrip().startswith("#endif")
