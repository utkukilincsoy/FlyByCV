"""src/ui/startup.py testleri (PLAN.md Adım 10.2b): sihirbaz yalnızca taze kurulumda açılır."""
import pytest
from sqlmodel import create_engine

from src.core.db import configure_sqlite_engine, get_session, init_db
from src.core.settings import KEY_DAILY_LIMIT, KEY_ONBOARDING_COMPLETED, KEY_SCHEMA_VERSION, KEY_TEST_MODE, set_setting
from src.core.models import Setting
from src.cv_builder.profile_editor import write_profile
from src.cv_builder.profile_loader import PersonalInfo, load_profile
from src.ui.profile_data import new_minimal_profile
from src.ui.startup import EXIT_OK, EXIT_PROFILE_ERROR, prepare_startup
from tests.conftest import build_sample_profile


class Calls:
    """Sahte bağımlılıklar; ne çağrıldığını sırasıyla kaydeder."""

    def __init__(self, profile_path, *, wizard_completes=True, wizard_writes_profile=True):
        self.events: list[str] = []
        self._profile_path = profile_path
        self._completes = wizard_completes
        self._writes = wizard_writes_profile

    def wizard(self, engine, profile_path):
        self.events.append("wizard")
        if self._completes and self._writes:
            write_profile(profile_path, build_sample_profile())
        return self._completes

    def consent(self, engine):
        self.events.append("consent")
        return True

    def error(self, path, error):
        self.events.append("error")


def _run(engine, profile_path, calls, *, with_wizard=True):
    return prepare_startup(
        engine,
        profile_path,
        run_wizard=calls.wizard if with_wizard else None,
        ask_consent=calls.consent,
        show_profile_error=calls.error,
    )


@pytest.fixture
def profile_path(tmp_path):
    return tmp_path / "data" / "master_profile.json"


def test_fresh_install_runs_wizard_then_checks_profile_and_skips_separate_consent(engine, profile_path):
    calls = Calls(profile_path)
    assert _run(engine, profile_path, calls) is None
    assert calls.events == ["wizard"]  # KVKK sihirbazın içinde; ayrıca sorulmaz


def test_fresh_install_wizard_closed_midway_exits_without_opening_main_window(engine, profile_path):
    calls = Calls(profile_path, wizard_completes=False)
    assert _run(engine, profile_path, calls) == EXIT_OK
    assert calls.events == ["wizard"]


def test_fresh_install_wizard_done_but_no_profile_is_reported(engine, profile_path):
    calls = Calls(profile_path, wizard_writes_profile=False)
    assert _run(engine, profile_path, calls) == EXIT_PROFILE_ERROR
    assert calls.events == ["wizard", "error"]


def test_onboarded_user_never_sees_wizard_and_keeps_old_order(engine, profile_path):
    write_profile(profile_path, build_sample_profile())
    with get_session(engine) as session:
        set_setting(session, KEY_ONBOARDING_COMPLETED, True)
        session.commit()
    calls = Calls(profile_path)
    assert _run(engine, profile_path, calls) is None
    assert calls.events == ["consent"]


def test_onboarded_user_with_broken_profile_gets_error_before_consent(engine, profile_path):
    with get_session(engine) as session:
        set_setting(session, KEY_ONBOARDING_COMPLETED, True)
        session.commit()
    calls = Calls(profile_path)
    assert _run(engine, profile_path, calls) == EXIT_PROFILE_ERROR
    assert calls.events == ["error"]


def test_without_wizard_fresh_install_falls_back_to_old_flow(engine, profile_path):
    write_profile(profile_path, build_sample_profile())
    calls = Calls(profile_path)
    assert _run(engine, profile_path, calls, with_wizard=False) is None
    assert calls.events == ["consent"]


def test_real_shaped_existing_database_does_not_open_wizard(tmp_path, profile_path):
    """Gerçek app.db'ler gibi: bazı ayarlar var, schema_version/onboarding YOK; açılış göçü
    çalışınca sihirbaz bu kullanıcıya açılmamalı (gerçek dosyalar KOPYALARINDA elle de doğrulandı)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)
    init_db(engine=engine)
    with get_session(engine) as session:
        session.delete(session.get(Setting, KEY_SCHEMA_VERSION))
        set_setting(session, KEY_DAILY_LIMIT, 20)
        set_setting(session, KEY_TEST_MODE, True)
        session.commit()
    init_db(engine=engine)  # uygulama açılışı: göç v4 çalışır
    write_profile(profile_path, build_sample_profile())

    calls = Calls(profile_path)
    assert _run(engine, profile_path, calls) is None
    assert "wizard" not in calls.events
    assert calls.events == ["consent"]


def test_new_minimal_profile_is_valid_empty_and_round_trips(tmp_path):
    profile = new_minimal_profile(PersonalInfo(name="Ayşe Yılmaz", email="ayse@ornek.com", location="İstanbul"))
    assert profile.summaries == {"default": ""}
    assert profile.experience == profile.education == profile.projects == profile.skills == []

    path = tmp_path / "master_profile.json"
    write_profile(path, profile)
    assert load_profile(path) == profile
