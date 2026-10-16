"""src/ui/companies_tab.py testleri (qtbot).

Yalnızca Otopilot kilitlemesi (PLAN Adım 9.6, `set_autopilot_active`/`is_job_running`) test
ediliyor -- tablonun/aramanın/diyalogların kendisi bu adımda değişmedi, kapsam dışı.
"""
from PySide6.QtWidgets import QMessageBox

from src.ui.companies_tab import CompaniesTab


def _make_tab(qtbot, engine, tmp_path) -> CompaniesTab:
    profile_path = tmp_path / "master_profile.json"  # bu testlerde okunmuyor
    tab = CompaniesTab(engine, profile_path)
    qtbot.addWidget(tab)
    return tab


def test_companies_tab_is_job_running_false_when_no_run(qtbot, engine, tmp_path):
    tab = _make_tab(qtbot, engine, tmp_path)

    assert tab.is_job_running() is False


def test_companies_tab_set_autopilot_active_locks_bulk_buttons(qtbot, engine, tmp_path):
    tab = _make_tab(qtbot, engine, tmp_path)

    tab.set_autopilot_active(True)
    assert not tab._import_button.isEnabled()
    assert not tab._enrich_missing_button.isEnabled()
    assert not tab._enrich_selected_button.isEnabled()
    assert not tab._build_button.isEnabled()

    tab.set_autopilot_active(False)
    assert tab._import_button.isEnabled()
    assert tab._enrich_missing_button.isEnabled()
    assert tab._enrich_selected_button.isEnabled()
    assert tab._build_button.isEnabled()


def test_companies_tab_set_autopilot_active_does_not_lock_add_button(qtbot, engine, tmp_path):
    """"Şirket ekle" toplu bir iş değil -- Otopilot çalışırken de kullanılabilir kalmalı."""
    tab = _make_tab(qtbot, engine, tmp_path)

    tab.set_autopilot_active(True)

    assert tab._add_button.isEnabled()


def test_companies_tab_guard_running_blocks_and_warns_while_autopilot_active(
    qtbot, engine, tmp_path, monkeypatch
):
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    tab = _make_tab(qtbot, engine, tmp_path)
    tab.set_autopilot_active(True)

    assert tab._guard_running() is True
