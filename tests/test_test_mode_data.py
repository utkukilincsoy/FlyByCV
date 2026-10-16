"""src/ui/test_mode_data.py testleri (Qt YOK): gerçek gönderime geçiş / test moduna dönüş kuralı."""
import pytest

from src.core.legal import record_consent
from src.core.settings import KEY_TEST_MODE, get_setting, set_setting
from src.ui.test_mode_data import (
    NO_CONSENT_TEXT,
    GoLiveRefused,
    enable_real_sending,
    go_live_blocker,
    return_to_test_mode,
)


def test_go_live_is_blocked_without_consent(session):
    assert go_live_blocker(session) == NO_CONSENT_TEXT


def test_enable_real_sending_refuses_without_consent_and_writes_nothing(session):
    with pytest.raises(GoLiveRefused):
        enable_real_sending(session)
    session.commit()

    assert get_setting(session, KEY_TEST_MODE) is True  # varsayılan, dokunulmadı


def test_enable_real_sending_with_consent_turns_test_mode_off(session):
    record_consent(session)
    session.commit()

    assert go_live_blocker(session) is None
    enable_real_sending(session)
    session.commit()

    assert get_setting(session, KEY_TEST_MODE) is False


def test_consent_of_an_old_text_version_blocks_going_live(session, monkeypatch):
    """Metin değişip sürüm artınca eski onay geçersiz olur (CLAUDE.md kural 12)."""
    record_consent(session)
    session.commit()
    monkeypatch.setattr("src.core.legal.LEGAL_TEXT_VERSION", "2099-01-01")

    with pytest.raises(GoLiveRefused):
        enable_real_sending(session)


def test_return_to_test_mode_needs_no_consent(session):
    set_setting(session, KEY_TEST_MODE, False)
    session.commit()

    return_to_test_mode(session)
    session.commit()

    assert get_setting(session, KEY_TEST_MODE) is True
