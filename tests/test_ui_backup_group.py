"""ui/settings_groups/backup_group: "Şimdi yedekle", geri yükleme onayı, durum satırı.

Modal pencereler (`QFileDialog`, `QMessageBox`) yamalanır; dosyalar yalnızca `tmp_path` altında.
"""
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox
from sqlmodel import create_engine

from src.core.backup import KIND_AUTO, BackupInfo, list_backups
from src.core.db import configure_sqlite_engine, init_db
from src.ui.settings_groups import backup_group
from src.ui.settings_groups.backup_group import (
    NO_BACKUP_TEXT,
    UNAVAILABLE_TEXT,
    BackupGroup,
    confirm_restore_text,
    status_text,
)


@pytest.fixture
def db_path(tmp_path: Path):
    path = tmp_path / "data" / "app.db"
    path.parent.mkdir()
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)
    init_db(engine=engine)
    yield path
    engine.dispose()


@pytest.fixture
def messages(monkeypatch):
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a: shown.append(("info", text)))
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a: shown.append(("warning", text)))
    return shown


def _group(qtbot, db_path, tmp_path) -> BackupGroup:
    group = BackupGroup(db_path, tmp_path / "data" / "master_profile.json")
    qtbot.addWidget(group)
    return group


def test_status_text_for_no_backups_and_latest_backup(tmp_path):
    assert status_text([]) == NO_BACKUP_TEXT
    info = BackupInfo(tmp_path / "x.zip", datetime(2026, 10, 9, 11, 5), KIND_AUTO, 1)
    assert status_text([info, info]) == "Son yedek: 09.10.2026 11:05 (otomatik) · 2 yedek"


def test_backup_now_creates_backup_and_refreshes_status(qtbot, db_path, tmp_path, messages):
    group = _group(qtbot, db_path, tmp_path)
    assert group.status_label.text() == NO_BACKUP_TEXT

    group.backup_now_button.click()

    backups = list_backups(db_path.parent / "backups")
    assert len(backups) == 1 and backups[0].kind == "manuel"
    assert group.status_label.text().startswith("Son yedek:")
    assert messages == [("info", f"Yedek alındı: {backups[0].path.name}")]


def test_buttons_disabled_without_a_database_file(qtbot, tmp_path):
    group = BackupGroup(None, tmp_path / "master_profile.json")
    qtbot.addWidget(group)

    assert group.status_label.text() == UNAVAILABLE_TEXT
    assert not group.backup_now_button.isEnabled()
    assert not group.restore_button.isEnabled()
    assert not group.open_folder_button.isEnabled()


@pytest.mark.parametrize("answer, expected", [(QMessageBox.No, []), (QMessageBox.Yes, ["sec"])])
def test_restore_emits_only_after_confirmation(answer, expected, qtbot, db_path, tmp_path, monkeypatch):
    chosen = tmp_path / "cv-autopilot-yedek-20261009-110500-manuel.zip"
    asked: list[str] = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(chosen), ""))

    def fake_question(parent, title, text, buttons, default):
        asked.append(text)
        assert default == QMessageBox.No  # varsayılan cevap "Hayır"
        return answer

    monkeypatch.setattr(QMessageBox, "question", fake_question)
    group = _group(qtbot, db_path, tmp_path)
    emitted: list[Path] = []
    group.restore_requested.connect(emitted.append)

    group.restore_button.click()

    assert asked == [confirm_restore_text(chosen.name)]
    assert emitted == ([chosen] if expected else [])


def test_restore_cancelled_file_dialog_does_nothing(qtbot, db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("sorulmamalıydı"))
    group = _group(qtbot, db_path, tmp_path)
    emitted: list[Path] = []
    group.restore_requested.connect(emitted.append)

    group.restore_button.click()

    assert emitted == []


def test_confirm_text_mentions_safety_backup_and_restart():
    text = confirm_restore_text("yedek.zip")
    assert "yedek.zip" in text
    assert "önce ayrıca yedeklenecek" in text
    assert "yeniden başlatılacak" in text


def test_open_folder_creates_backups_folder(qtbot, db_path, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(backup_group.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    group = _group(qtbot, db_path, tmp_path)

    group.open_folder_button.click()

    backups = db_path.parent / "backups"
    assert backups.is_dir()
    assert Path(opened[0]) == backups
